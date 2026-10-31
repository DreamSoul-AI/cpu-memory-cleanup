"""内存清理小窗口。打开只读，绿色按钮显式执行一次系统工作集整理。"""

from pathlib import Path
import queue
import threading
import time

from .presentation import summary
from .ring import RingRaster

GREEN = "#087F4F"
BACKGROUND = "#F3F7F4"


def _gib(value):
    return "未知" if value is None else f"{value / 1024 ** 3:.3f} GiB"


def _present(report):
    metrics = report.samples[-1].metrics if report.samples else {}
    physical, commit = metrics.get("physical") or {}, metrics.get("commit") or {}
    total, available = physical.get("total_bytes"), physical.get("available_bytes")
    percent = ((total - available) / total * 100
               if total and available is not None and 0 <= available <= total else None)
    lines = []
    result, result_note = "尚未清理", "轻点绿色按钮，查看本次可用内存变化。"
    if report.mode == "maintain":
        outcome = {"success": "已执行清理", "failure": "清理调用失败", "not_started": "未执行清理"}.get(
            report.action.get("call_outcome"), "清理结果未知，请核对")
        result = outcome
        lines.append(outcome)
        result_note = "本次状态与记录可在详情中查看。"
        if len(report.samples) >= 2:
            before = report.samples[0].metrics
            before_available = (before.get("physical") or {}).get("available_bytes")
            for title, category, key in (("可用内存", "physical", "available_bytes"), ("提交量", "commit", "used_bytes")):
                lines.append(f"{title}：{_gib((before.get(category) or {}).get(key))} → {_gib((metrics.get(category) or {}).get(key))}")
            if report.action.get("call_outcome") == "success" and before_available is not None and available is not None:
                difference = available - before_available
                result = (f"可用内存{'增加' if difference > 0 else '减少'} {_gib(abs(difference))}"
                          if difference else "可用内存没有变化")
                result_note = f"{_gib(before_available)} → {_gib(available)}\n再次使用程序时，内存占用可能回升。"
        if report.action.get("privilege_restored") is not True and report.action.get("attempted"):
            lines.append("权限恢复未确认，请查看详情。")
        lines.append("数值变化不代表实际提速。")
    header = report.metadata.get("header") or {}
    if report.mode == "maintain" and header.get("scope") == "process":
        target = header.get("target") or {}
        title = f"{target.get('name') or '选中进程'}（PID {target.get('pid')}）"
        lines.insert(0, "定向整理：" + title)
        result_note = title + "；只整理此进程，程序继续运行。"
        if len(report.samples) >= 2 and report.action.get("call_outcome") == "success":
            a = (report.samples[0].metrics.get("target") or {}).get("working_set_bytes")
            b = (report.samples[-1].metrics.get("target") or {}).get("working_set_bytes")
            if a is not None and b is not None:
                delta = b - a
                result = f"目标工作集{'增加' if delta > 0 else '减少'} {_gib(abs(delta))}" if delta else "目标工作集没有变化"
                lines.append(f"目标工作集：{_gib(a)} → {_gib(b)}")
    errors = (report.samples[-1].errors if report.samples else []) + report.errors
    details = lines + [str(e.get("message") or e.get("reason") or e) if isinstance(e, dict) else str(e) for e in errors]
    if percent is None:
        details.append("物理内存读数缺失或不一致，占用保持未知。")
    if report.output_dir:
        details.append("本次记录：" + report.output_dir)
    timestamp = report.samples[-1].timestamp if report.samples else None
    return {"percent": percent, "headline": "未知" if percent is None else f"{percent:.0f}%",
            "values": {"total": total, "available": available, "commit_used": commit.get("used_bytes"), "commit_limit": commit.get("limit_bytes")},
            "available": f"已用 {_gib(None if percent is None else total - available)} / 共 {_gib(total)}",
            "commit": f"提交量 {_gib(commit.get('used_bytes'))} / 上限 {_gib(commit.get('limit_bytes'))}",
            "context": f"最近更新：{timestamp or '未知'}", "result": result, "result_note": result_note,
            "details": "\n".join(details) or "本次没有报告读取或记录错误。"}


class MemoryWindow:
    def __init__(self, root, app, tk, *, output_dir, clock=None, settings_path=None, tray_factory=None):
        self.root, self.app = root, app
        self._output_dir = Path(output_dir)
        self._events = queue.Queue()
        self._worker = self._cancel_event = None
        self._closing = self._closed = False
        self._supported = "working-set" in app.capabilities.mechanisms
        self._percent = None
        self._effect = self._effect_job = None
        self._latest_values = None
        self._clock = clock or time.monotonic
        self.workspace = None
        self._settings_path, self._tray_factory = settings_path, tray_factory
        self._tray = None
        self._tray_events = queue.Queue()
        self._rule_snapshot = None
        self._shown = None
        self._animation_job = self._memory_job = None
        self._memory_worker = None
        self._memory_events = queue.Queue()
        self._live = bool(getattr(app, "memory_display_available", False))
        self._memory_context = "最近更新：未知"
        self._latest_report = None
        self._report_details = "尚无运行记录"
        self._paused = False
        # Canvas尺寸使用像素，普通Tk字体使用点；按同一显示比例换算，避免大字体挤进固定小圆环。
        scale = root.winfo_fpixels("1i") / 96
        px = lambda value: round(value * scale)
        ring_size = px(220)
        self._ring_inset, self._ring_width = px(14), px(11)
        root.title("内存清理")
        root.geometry(f"{px(460)}x{px(640)}")
        root.minsize(px(460), px(640))
        root.configure(bg=BACKGROUND)
        body = tk.Frame(root, bg=BACKGROUND, padx=px(28), pady=px(18))
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        heading = tk.Frame(body, bg=BACKGROUND)
        heading.grid(row=0, column=0, sticky="ew")
        heading.columnconfigure(0, weight=1)
        tk.Label(heading, text="内存清理", bg=BACKGROUND, fg="#183428", font=("Microsoft YaHei UI", 16, "bold")).grid(row=0, column=0, sticky="w")
        self.refresh_button = tk.Button(heading, text="暂停显示", command=self.toggle_live,
            bg="#E5EFE8", fg="#426451", activebackground="#D9E9DF", relief="flat", bd=0,
            font=("Microsoft YaHei UI", 9), padx=10, pady=6, cursor="hand2")
        self.refresh_button.grid(row=0, column=1, sticky="e")
        self.ring = tk.Canvas(body, width=ring_size, height=ring_size, bg=BACKGROUND, highlightthickness=0)
        self.ring.grid(row=1, column=0, pady=(px(22), px(16)))
        self._photo_factory = tk.PhotoImage
        self._ring_raster = None
        self._ring_frame = None
        self._ring_item = self.ring.create_image(ring_size/2, ring_size/2, anchor="center")
        self._draw_ring(ring_size)
        self.ring.bind("<Configure>", lambda event: self._draw_ring(event.width))
        self.percent = tk.Label(self.ring, text="未知", bg=BACKGROUND, fg="#183428", font=("Microsoft YaHei UI", -px(54), "bold"))
        self.percent.place(relx=0.5, rely=0.43, anchor="center")
        self.gauge_label = tk.Label(self.ring, text="物理内存占用", bg=BACKGROUND, fg="#6C7D73", font=("Microsoft YaHei UI", -px(13)))
        self.gauge_label.place(relx=0.5, rely=0.68, anchor="center")
        self.available = tk.Label(body, text="已用 未知 / 共 未知", bg=BACKGROUND, fg="#52645B", font=("Microsoft YaHei UI", 10))
        self.available.grid(row=2, column=0, pady=(0, 10))
        self.button = tk.Button(body, text="一键整理工作集", command=self.maintain, bg=GREEN, fg="white",
            activebackground="#06643E", activeforeground="white", disabledforeground="#CDE6D7", relief="flat", bd=0,
            font=("Microsoft YaHei UI", 15, "bold"), cursor="hand2", takefocus=True, pady=9)
        self.button.grid(row=3, column=0, sticky="ew", pady=(8, 8))
        self.button.bind("<Enter>", lambda event: self.button.configure(bg="#066F45") if self._worker is None else None)
        self.button.bind("<Leave>", lambda event: self.button.configure(bg=GREEN))
        self.status = tk.Label(body, text="实时查看，按需整理。", bg=BACKGROUND, fg="#6C7D73", font=("Microsoft YaHei UI", 9), wraplength=px(380))
        self.status.grid(row=4, column=0, sticky="ew", pady=(0, 14))
        feedback = tk.Frame(body, bg="white", padx=16, pady=14)
        feedback.grid(row=5, column=0, sticky="ew")
        feedback.columnconfigure(0, weight=1)
        tk.Label(feedback, text="最近一次整理", bg="white", fg="#72877A", font=("Microsoft YaHei UI", 9)).grid(row=0, column=0, sticky="w")
        self.result = tk.Label(feedback, text="尚未整理", bg="white", fg=GREEN, font=("Microsoft YaHei UI", 12, "bold"), anchor="w", justify="left", wraplength=px(360), height=2)
        self.result.grid(row=1, column=0, sticky="ew", pady=(7, 5))
        self.result_note = tk.Label(feedback, text="轻点绿色按钮，查看本次内存变化。", bg="white", fg="#6C7D73", font=("Microsoft YaHei UI", 9), anchor="w", justify="left", wraplength=px(360), height=2)
        self.result_note.grid(row=2, column=0, sticky="ew")
        self.result_context = tk.Label(feedback, text="结果与实时读数分别保留", bg="white", fg="#72877A", font=("Microsoft YaHei UI", 8), anchor="w", wraplength=px(360), height=2)
        self.result_context.grid(row=3, column=0, sticky="ew", pady=(6, 0))
        controls = tk.Frame(body, bg=BACKGROUND)
        controls.grid(row=6, column=0, sticky="ew", pady=(15, 0))
        controls.columnconfigure(0, weight=1)
        self.details_button = tk.Button(controls, text="详情与设置  ›", command=self.open_workspace,
            bg=BACKGROUND, fg=GREEN, activebackground="#E5EFE8", relief="flat", bd=0,
            font=("Microsoft YaHei UI", 10), cursor="hand2", pady=6)
        self.details_button.grid(row=0, column=0, sticky="w")
        self.cancel_button = tk.Button(controls, text="停止后续动作", command=self.cancel, state="disabled",
            bg=BACKGROUND, fg="#52645B", relief="flat", bd=0, font=("Microsoft YaHei UI", 9))
        self.cancel_button.grid(row=0, column=1, sticky="e")
        self._commit_text = "提交量 未知"
        self.context = tk.Label(body, text="最近更新：未知", bg=BACKGROUND, fg="#72877A", font=("Microsoft YaHei UI", 8), wraplength=px(380))
        self.context.grid(row=7, column=0, sticky="w", pady=(8, 0))
        root.protocol("WM_DELETE_WINDOW", self.close)
        self._controls()
        if not self._supported:
            self.status.configure(text=app.capabilities.reason or "当前系统不支持工作集整理，仅能查看内存。")
        # 只在初次布局时按真实字体尺寸适配，状态更新不反复改变窗口尺寸。
        root.update_idletasks()
        width, height = max(px(460), root.winfo_reqwidth()), max(px(640), root.winfo_reqheight() + px(12))
        root.minsize(width, height)
        root.geometry(f"{width}x{height}")

    def _draw_ring(self, width):
        height = self.ring.winfo_height()
        size = int(min(width, height))
        if size <= 2 * self._ring_inset:
            return
        display = self._effect["value"] if self._effect is not None else self._percent
        frame = (size, round(display or 0., 1))
        if frame != self._ring_frame:
            if self._ring_raster is None or self._ring_raster.size != size:
                self._ring_raster = RingRaster(size, self._ring_inset, self._ring_width, BACKGROUND, "#DFEAE3", GREEN)
            self._ring_photo = self._photo_factory(master=self.ring, data=self._ring_raster.render(frame[1]), format="PPM")
            self.ring.itemconfigure(self._ring_item, image=self._ring_photo)
            self._ring_frame = frame
        self.ring.coords(self._ring_item, width/2, height/2)

    def _paint_memory(self, values):
        self._shown = values
        total, available = values["total"], values["available"]
        self._percent = ((total - available) / total * 100
                         if total and available is not None and 0 <= available <= total else None)
        if self._effect is None:
            self.percent.configure(text="未知" if self._percent is None else f"{self._percent:.0f}%")
        self.available.configure(text=f"已用 {_gib(None if self._percent is None else total - available)} / 共 {_gib(total)}")
        self._commit_text = f"提交量 {_gib(values['commit_used'])} / 上限 {_gib(values['commit_limit'])}"
        self._draw_ring(self.ring.winfo_width())

    def _target_memory(self, view):
        if self._animation_job is not None:
            self.root.after_cancel(self._animation_job)
            self._animation_job = None
        target = view["values"]
        self._latest_values = target
        self._memory_context = view["context"]
        self.context.configure(text=self._memory_context + " · 平滑显示")
        if self._effect is not None:
            self._paint_memory(target)
            return
        if self._shown is None or view["percent"] is None:
            self._paint_memory(target)
            return
        self._animation_from = self._shown.copy()
        self._animation_to = target
        self._animation_started = self._clock()
        self._animation_job = self.root.after(16, self._animate)

    def _animate(self):
        self._animation_job = None
        if self._closing or self._closed:
            return
        progress = min(1.0, max(0.0, (self._clock() - self._animation_started) / 0.4))
        weight = progress * progress * (3 - 2 * progress)
        values = {key: (before + (after - before) * weight if before is not None and after is not None else after)
                  for key, after in self._animation_to.items() for before in [self._animation_from[key]]}
        self._paint_memory(self._animation_to if progress >= 1 else values)
        if progress < 1:
            self._animation_job = self.root.after(16, self._animate)

    def _poll_memory(self):
        self._memory_job = None
        if self._paused or self._closing:
            return
        if self._closing or self._closed:
            return
        if self._memory_worker is None:
            def read():
                try:
                    self._memory_events.put((self.app.memory_status(), None))
                except Exception as error:
                    self._memory_events.put((None, error))
            try:
                self._memory_worker = threading.Thread(target=read, daemon=False)
                self._memory_worker.start()
            except Exception:
                self._memory_worker = None
                self.context.configure(text=self._memory_context + " · 显示读取线程未启动，保留上次读数。")
        self._memory_job = self.root.after(500, self._poll_memory)

    def toggle_live(self):
        self._paused = not self._paused
        self.refresh_button.configure(text="恢复显示" if self._paused else "暂停显示")
        if self._paused:
            for job in (self._animation_job, self._memory_job):
                if job is not None:
                    self.root.after_cancel(job)
            self._animation_job = self._memory_job = None
            self.status.configure(text="实时显示已暂停，保留上次读数。")
        else:
            self.status.configure(text="实时显示已恢复。")
            self.refresh()

    def _controls(self):
        busy = self._closing or self._worker is not None or self.app.busy or self._effect is not None
        self.button.configure(state="normal" if self._supported and not busy else "disabled")
        self.refresh_button.configure(state="disabled" if self._closing else "normal")
        self.cancel_button.configure(state="normal" if self._worker is not None and not self._closing else "disabled")

    def open_workspace(self):
        if self._closing:
            return
        if self.workspace is None:
            from .workspace import Workspace
            self.workspace = Workspace(self, settings_path=self._settings_path)
        else:
            self.workspace.show()

    def enable_tray(self, enabled):
        if enabled and self._tray is None:
            if self._tray_factory is None:
                raise RuntimeError("当前接入没有托盘能力")
            tray = self._tray_factory(lambda: self._tray_events.put("show"))
            tray.start()
            self._tray = tray
        elif not enabled and self._tray is not None:
            self.root.deiconify()
            self._tray.close()
            self._tray = None

    def hide_to_tray(self):
        if self._tray is None:
            self.status.configure(text="请先在范围窗口启用托盘；当前窗口保持打开。")
            return
        if self.workspace is not None:
            self.workspace.hide()
        self.root.withdraw()

    def _snapshot_for_rules(self, report):
        self._rule_snapshot = None
        if report.completion != "success" or not report.samples or report.samples[-1].errors:
            return
        physical = report.samples[-1].metrics.get("physical") or {}
        total, available = physical.get("total_bytes"), physical.get("available_bytes")
        if total and available is not None and 0 <= available <= total:
            self._rule_snapshot = (time.monotonic(), available / total * 100)

    def start(self):
        if self._live:
            self._poll_memory()
        else:
            self.refresh()
        self.root.after(50, self.pump)

    def _submit(self, function, *args, **options):
        if self._closing or self._worker is not None or self.app.busy or self._effect is not None:
            return False
        self._cancel_event = threading.Event()
        cancel_event = self._cancel_event

        def work():
            try:
                self._events.put((function(*args, cancel_event=cancel_event, **options), None))
            except Exception as error:
                self._events.put((None, error))

        try:
            self._worker = threading.Thread(target=work, daemon=False)
            self._controls()
            self._worker.start()
        except Exception as error:
            self._worker = self._cancel_event = None
            self._controls()
            self.status.configure(text=f"无法启动工作线程：{error}。本次请求未执行，可重新点击。")
            return False
        return True

    def refresh(self):
        if self._live:
            if not self._closing and self._memory_worker is None:
                if self._memory_job is not None:
                    self.root.after_cancel(self._memory_job)
                self._poll_memory()
            return
        if self._submit(self.app.inspect, {"count": 1, "interval": 1, "with_processes": False}):
            self.status.configure(text="正在读取内存状态，不执行清理…")

    def maintain(self):
        if not self._supported:
            return
        if self._submit(self.app.memory_cleanup, self._output_dir, confirmed=True,
                        confirmation_source="desktop:memory-clean-button", observation_seconds=3):
            self._start_effect()
            self.button.configure(text="正在整理…")
            self.status.configure(text="正在整理并观察3秒短期变化，当前读数持续更新。")
            self.result.configure(text="正在整理内存…")
            self.result_note.configure(text="只调用一次；观察结束后显示净变化，不代表系统已稳定。")

    def cancel(self):
        if self._cancel_event is not None:
            self._cancel_event.set()
        accepted = self.app.request_cancel()
        self.status.configure(text="核心已接受派发前取消；等待收尾。" if accepted else "取消请求已登记；最终状态以本次报告为准。")

    def close(self):
        if self._closing:
            return
        self._closing = True
        if self.workspace is not None:
            self.workspace.policy.stop()
        if self._tray is not None:
            try:
                self._tray.close()
                self._tray = None
            except Exception as error:
                self.status.configure(text=f"托盘退出未确认：{error}")
        for job in (self._animation_job, self._memory_job, self._effect_job):
            if job is not None:
                self.root.after_cancel(job)
        self._animation_job = self._memory_job = self._effect_job = self._effect = None
        if self._cancel_event is not None:
            self._cancel_event.set()
        self.app.shutdown()
        self._controls()
        self.status.configure(text="正在等待当前流程安全收尾…")
        if self._worker is None and self._memory_worker is None and not self.app.busy:
            self._closed = True
            self.root.destroy()

    def _start_effect(self):
        if self._animation_job is not None:
            self.root.after_cancel(self._animation_job)
            self._animation_job = None
        self._effect = {"phase": "emptying", "started": self._clock(), "from": self._percent or 0.,
                        "value": self._percent or 0., "pending": None}
        self._effect_job = self.root.after(16, self._effect_tick)
        self.gauge_label.configure(text="整理动效 · 非实时占用")

    def _effect_tick(self):
        self._effect_job = None
        if self._closing or self._effect is None:
            return
        e, now = self._effect, self._clock()
        if e["phase"] == "emptying":
            progress = min(1., (now-e["started"])/.45)
            e["value"] = e["from"]*(1-progress)**2
            if progress == 1:
                e.update(phase="waiting", started=now, value=0.)
        elif e["phase"] == "waiting" and e["pending"] is not None and now-e["started"] >= .2:
            values = self._latest_values or self._shown or {}
            total, available = values.get("total"), values.get("available")
            target = (total-available)/total*100 if total and available is not None and 0 <= available <= total else 0.
            e.update(phase="returning", started=now, target=target)
        elif e["phase"] == "returning":
            progress = min(1., (now-e["started"])/.7)
            e["value"] = e["target"]*(1-(1-progress)**3)
            if progress == 1:
                report, error = e["pending"]
                self._effect = None
                self.gauge_label.configure(text="物理内存占用")
                if self._latest_values is not None:
                    self._paint_memory(self._latest_values)
                else:
                    self.percent.configure(text="未知")
                self._apply_report(report, error)
                return
        self.percent.configure(text=f"{e['value']:.0f}%")
        self.gauge_label.configure(text="整理动效 · " + {"emptying":"归零中", "waiting":"等待中", "returning":"回到实测值"}[e["phase"]])
        self._draw_ring(self.ring.winfo_width())
        self._effect_job = self.root.after(16, self._effect_tick)

    def _apply_report(self, report, error):
        self.button.configure(text="一键整理工作集")
        if error is not None:
            self.status.configure(text="本次未完成，保留旧读数与更新时间。")
            self.result.configure(text="本次未完成")
            self.result_note.configure(text=str(error))
            self._report_details = str(error)
            self.result_context.configure(text="结果未知 · 不自动重试")
        else:
            view = _present(report)
            if not self._live and report.samples and report.mode not in ("review", "batch-review"):
                self._target_memory(view)
            if report.mode in ("maintain", "batch", "review", "batch-review"):
                self._latest_report = report
                feedback = summary(report)
                self.result.configure(text=feedback["title"])
                self.result_note.configure(text=feedback["note"])
                self.result_context.configure(text=feedback["context"])
                self._report_details = view["details"]
            completion = {"success": "完成", "partial": "结果不完整", "failed": "失败", "cancelled": "已取消", "unknown": "结果未知"}.get(report.completion, report.completion)
            self.status.configure(text=("清理：" if report.mode == "maintain" else "内存刷新：") + completion)
            if report.completion == "success" and (report.metadata.get("header") or {}).get("post_observation_seconds"):
                self.status.configure(text="本次调用与短期观察已结束，实时读数继续更新。")
            if self.workspace is not None:
                self.workspace.on_report(report)
            if self._tray is not None and report.mode in ("maintain", "batch"):
                try:
                    self._tray.notify("整理：" + completion + "。数值变化不代表提速；详情见窗口。")
                except Exception as error:
                    self.status.configure(text=f"整理已收尾，通知未确认：{error}")
        self._controls()

    def pump(self):
        if self._closed:
            return
        while not self._tray_events.empty():
            self._tray_events.get_nowait()
            if not self._closing:
                self.root.deiconify()
                self.root.lift()
        if self.workspace is not None and not self._closing:
            self.workspace.tick()
        if self._memory_worker is not None and not self._memory_worker.is_alive():
            self._memory_worker.join()
            self._memory_worker = None
            try:
                report, error = self._memory_events.get_nowait()
            except queue.Empty:
                report, error = None, RuntimeError("显示读取线程未返回结果")
            if not self._closing:
                if error is None:
                    self._snapshot_for_rules(report)
                else:
                    self._rule_snapshot = None
                if not self._paused and error is None and report.completion == "success" and report.samples:
                    self._target_memory(_present(report))
                elif not self._paused:
                    self.context.configure(text=self._memory_context + " · 最近读取失败，保留旧读数。")
        if self._worker is not None and not self._worker.is_alive():
            self._worker.join()
            self._worker = self._cancel_event = None
            if not self._closing:
                try:
                    report, error = self._events.get_nowait()
                except queue.Empty:
                    report, error = None, RuntimeError("工作线程未返回结果")
                if self._effect is not None and (error is not None or report.completion != "success"):
                    if self._effect_job is not None:
                        self.root.after_cancel(self._effect_job)
                    self._effect = self._effect_job = None
                    self.gauge_label.configure(text="物理内存占用")
                    if self._latest_values is not None:
                        self._paint_memory(self._latest_values)
                if self._effect is not None:
                    if error is None and report.samples:
                        view = _present(report)
                        if view["percent"] is not None:
                            self._latest_values = view["values"]
                    self._effect["pending"] = (report, error)
                else:
                    self._apply_report(report, error)
        if self._closing and self._worker is None and self._memory_worker is None and not self.app.busy:
            self._closed = True
            self.root.destroy()
            return
        self.root.after(50, self.pump)


def run(app, options=None):
    import tkinter as tk

    root = tk.Tk()
    output = (options or {}).get("output_dir") or Path.home() / ".cpu-memory-cleanup" / "runs"
    tray_factory = getattr(app, "tray_factory", None)
    window = MemoryWindow(root, app, tk, output_dir=output, tray_factory=tray_factory)
    try:
        window.start()
        root.mainloop()
    finally:
        window._closing = True
        if window.workspace is not None:
            window.workspace.policy.stop()
        if window._tray is not None:
            window._tray.close()
        # 不对可能已销毁的 Tk 调用 after_cancel，回调由根窗口销毁取消。
        if window._cancel_event is not None:
            window._cancel_event.set()
        app.shutdown()
        for worker in (window._worker, window._memory_worker):
            if worker is not None:
                worker.join()
        window._worker = window._memory_worker = window._cancel_event = None
        if not window._closed:
            window._closed = True
            try:
                root.destroy()
            except tk.TclError:
                pass
    return 0
