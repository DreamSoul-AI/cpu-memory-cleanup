"""内存清理小窗口。打开只读，绿色按钮显式执行一次系统工作集整理。"""

from pathlib import Path
import queue
import threading
import time

from ..contracts import ActionRequest, RunReport

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
    if report.mode == "maintain":
        outcome = {"success": "已执行清理", "failure": "清理调用失败", "not_started": "未执行清理"}.get(
            report.action.get("call_outcome"), "清理结果未知，请核对")
        lines.append(outcome)
        if len(report.samples) >= 2:
            before = report.samples[0].metrics
            for title, category, key in (("可用内存", "physical", "available_bytes"), ("提交量", "commit", "used_bytes")):
                lines.append(f"{title}：{_gib((before.get(category) or {}).get(key))} → {_gib((metrics.get(category) or {}).get(key))}")
        if report.action.get("privilege_restored") is not True and report.action.get("attempted"):
            lines.append("权限恢复未确认，请查看详情。")
        lines.append("数值变化不代表实际提速。")
    else:
        lines.append("仅更新内存状态，尚未执行清理。")
    errors = (report.samples[-1].errors if report.samples else []) + report.errors
    details = [str(e.get("message") or e.get("reason") or e) if isinstance(e, dict) else str(e) for e in errors]
    if percent is None:
        details.append("物理内存读数缺失或不一致，占用保持未知。")
    if report.output_dir:
        details.append("本次记录：" + report.output_dir)
    timestamp = report.samples[-1].timestamp if report.samples else None
    return {"percent": percent, "headline": "未知" if percent is None else f"{percent:.0f}%",
            "values": {"total": total, "available": available, "commit_used": commit.get("used_bytes"), "commit_limit": commit.get("limit_bytes")},
            "available": f"可用 {_gib(available)} / 共 {_gib(total)}",
            "commit": f"提交量 {_gib(commit.get('used_bytes'))} / 上限 {_gib(commit.get('limit_bytes'))}",
            "context": f"最近更新：{timestamp or '未知'}", "result": "\n".join(lines),
            "details": "\n".join(details) or "本次没有报告读取或记录错误。"}


class MemoryWindow:
    def __init__(self, root, app, tk, *, output_dir, clock=None):
        self.root, self.app = root, app
        self._output_dir = Path(output_dir)
        self._events = queue.Queue()
        self._worker = self._cancel_event = None
        self._closing = self._closed = False
        self._supported = "working-set" in app.capabilities.mechanisms
        self._percent = None
        self._clock = clock or time.monotonic
        self._shown = None
        self._animation_job = self._memory_job = None
        self._memory_worker = None
        self._memory_events = queue.Queue()
        self._live = bool(getattr(app, "memory_display_available", False))
        self._memory_context = "最近更新：未知"
        root.title("内存清理")
        root.geometry("460x660")
        root.minsize(420, 600)
        root.configure(bg=BACKGROUND)
        body = tk.Frame(root, bg=BACKGROUND, padx=24, pady=18)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(8, weight=1)
        tk.Label(body, text="内存清理", bg=BACKGROUND, fg="#183428", font=("Microsoft YaHei UI", 18, "bold")).grid(row=0, column=0)
        self.percent = tk.Label(body, text="未知", bg=BACKGROUND, fg="#183428", font=("Microsoft YaHei UI", 62, "bold"))
        self.percent.grid(row=1, column=0, pady=(12, 0))
        tk.Label(body, text="物理内存占用", bg=BACKGROUND, fg="#52645B").grid(row=2, column=0)
        self.bar = tk.Canvas(body, width=360, height=12, bg="#DEE8E1", highlightthickness=0)
        self.bar.grid(row=3, column=0, sticky="ew", pady=12)
        self._bar_fill = self.bar.create_rectangle(0, 0, 0, 12, fill=GREEN, width=0)
        self.bar.bind("<Configure>", lambda event: self._draw_bar(event.width))
        self.available = tk.Label(body, text="可用 未知 / 共 未知", bg=BACKGROUND, fg="#183428", font=("Microsoft YaHei UI", 13))
        self.available.grid(row=4, column=0, pady=4)
        self.commit = tk.Label(body, text="提交量 未知", bg=BACKGROUND, fg="#52645B", wraplength=380)
        self.commit.grid(row=5, column=0, pady=4)
        self.button = tk.Button(body, text="一键清理", command=self.maintain, bg=GREEN, fg="white",
                                activebackground="#06643E", activeforeground="white", relief="flat",
                                font=("Microsoft YaHei UI", 20, "bold"), cursor="hand2", takefocus=True, pady=12)
        self.button.grid(row=6, column=0, sticky="ew", pady=14)
        self.status = tk.Label(body, text="打开只读，点击绿色按钮才清理。", bg=BACKGROUND, fg="#355A46", wraplength=380)
        self.status.grid(row=7, column=0, sticky="ew")
        self.result = tk.Label(body, text="清理后在这里显示前后数值。", bg=BACKGROUND, fg="#183428", justify="left", wraplength=380)
        self.result.grid(row=8, column=0, sticky="nw", pady=10)
        self.context = tk.Label(body, text="最近更新：未知", bg=BACKGROUND, fg="#52645B")
        self.context.grid(row=9, column=0)
        controls = tk.Frame(body, bg=BACKGROUND)
        controls.grid(row=10, column=0, pady=8)
        self.refresh_button = tk.Button(controls, text="刷新内存", command=self.refresh)
        self.refresh_button.pack(side="left", padx=4)
        self.cancel_button = tk.Button(controls, text="取消当前请求", command=self.cancel, state="disabled")
        self.cancel_button.pack(side="left", padx=4)
        tk.Button(controls, text="详情", command=self.toggle_details).pack(side="left", padx=4)
        self.details = tk.Text(body, height=4, width=40, wrap="word", state="disabled")
        self._details_open = False
        tk.Label(body, text="点击执行一次系统工作集整理。不结束进程、不清 Standby。\n可能产生缺页和 I/O 代价，不保证提速。",
                 bg=BACKGROUND, fg="#52645B", justify="left", wraplength=380).grid(row=12, column=0, sticky="w", pady=6)
        root.protocol("WM_DELETE_WINDOW", self.close)
        self._controls()
        if not self._supported:
            self.status.configure(text=app.capabilities.reason or "当前系统不支持工作集整理，仅能查看内存。")

    def _draw_bar(self, width):
        self.bar.coords(self._bar_fill, 0, 0, 0 if self._percent is None else width * self._percent / 100, 12)

    def _paint_memory(self, values):
        self._shown = values
        total, available = values["total"], values["available"]
        self._percent = ((total - available) / total * 100
                         if total and available is not None and 0 <= available <= total else None)
        self.percent.configure(text="未知" if self._percent is None else f"{self._percent:.0f}%")
        self.available.configure(text=f"可用 {_gib(available)} / 共 {_gib(total)}")
        self.commit.configure(text=f"提交量 {_gib(values['commit_used'])} / 上限 {_gib(values['commit_limit'])}")
        self._draw_bar(self.bar.winfo_width())

    def _target_memory(self, view):
        if self._animation_job is not None:
            self.root.after_cancel(self._animation_job)
            self._animation_job = None
        target = view["values"]
        self._memory_context = view["context"]
        self.context.configure(text=self._memory_context + " · 平滑显示")
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
        if self._closing or self._closed:
            return
        if self._memory_worker is None:
            def read():
                try:
                    self._memory_events.put((self.app.memory_snapshot(), None))
                except Exception as error:
                    self._memory_events.put((None, error))
            try:
                self._memory_worker = threading.Thread(target=read, daemon=False)
                self._memory_worker.start()
            except Exception:
                self._memory_worker = None
                self.context.configure(text=self._memory_context + " · 显示读取线程未启动，保留上次读数。")
        self._memory_job = self.root.after(500, self._poll_memory)

    def toggle_details(self):
        self._details_open = not self._details_open
        if self._details_open:
            self.details.grid(row=11, column=0, sticky="ew")
        else:
            self.details.grid_remove()

    def _controls(self):
        busy = self._closing or self._worker is not None or self.app.busy
        self.button.configure(state="normal" if self._supported and not busy else "disabled")
        self.refresh_button.configure(state="disabled" if busy else "normal")
        self.cancel_button.configure(state="normal" if self._worker is not None and not self._closing else "disabled")

    def start(self):
        if self._live:
            self._poll_memory()
        else:
            self.refresh()
        self.root.after(50, self.pump)

    def _submit(self, function, *args, **options):
        if self._closing or self._worker is not None or self.app.busy:
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
        request = ActionRequest("working-set", confirmed=True, confirmation_source="desktop:memory-clean-button")
        if self._submit(self.app.maintain, request, self._output_dir, **({"lightweight": True} if self._live else {})):
            self.button.configure(text="正在清理…")
            self.status.configure(text="正在整理系统工作集，请等待收尾…")

    def cancel(self):
        if self._cancel_event is not None:
            self._cancel_event.set()
        accepted = self.app.request_cancel()
        self.status.configure(text="核心已接受派发前取消；等待收尾。" if accepted else "取消请求已登记；最终状态以本次报告为准。")

    def close(self):
        if self._closing:
            return
        self._closing = True
        for job in (self._animation_job, self._memory_job):
            if job is not None:
                self.root.after_cancel(job)
        self._animation_job = self._memory_job = None
        if self._cancel_event is not None:
            self._cancel_event.set()
        self.app.shutdown()
        self._controls()
        self.status.configure(text="正在等待当前流程安全收尾…")
        if self._worker is None and self._memory_worker is None and not self.app.busy:
            self._closed = True
            self.root.destroy()

    def pump(self):
        if self._closed:
            return
        if self._memory_worker is not None and not self._memory_worker.is_alive():
            self._memory_worker.join()
            self._memory_worker = None
            try:
                sample, error = self._memory_events.get_nowait()
            except queue.Empty:
                sample, error = None, RuntimeError("显示读取线程未返回结果")
            if not self._closing:
                if error is None:
                    self._target_memory(_present(RunReport("display", "inspect", samples=[sample])))
                else:
                    self.context.configure(text=self._memory_context + " · 最近读取失败，保留旧读数。")
        if self._worker is not None and not self._worker.is_alive():
            self._worker.join()
            self._worker = self._cancel_event = None
            if not self._closing:
                try:
                    report, error = self._events.get_nowait()
                except queue.Empty:
                    report, error = None, RuntimeError("工作线程未返回结果")
                self.button.configure(text="一键清理")
                if error is not None:
                    self.status.configure(text="本次未完成，保留旧读数与更新时间。")
                    self.result.configure(text=str(error))
                else:
                    view = _present(report)
                    if not self._live:
                        self._target_memory(view)
                    self.result.configure(text=view["result"])
                    self.details.configure(state="normal")
                    self.details.delete("1.0", "end")
                    self.details.insert("1.0", view["details"])
                    self.details.configure(state="disabled")
                    completion = {"success": "完成", "partial": "结果不完整", "failed": "失败", "cancelled": "已取消", "unknown": "结果未知"}.get(report.completion, report.completion)
                    self.status.configure(text=("清理：" if report.mode == "maintain" else "内存刷新：") + completion)
                self._controls()
        if self._closing and self._worker is None and self._memory_worker is None and not self.app.busy:
            self._closed = True
            self.root.destroy()
            return
        self.root.after(50, self.pump)


def run(app, options=None):
    import tkinter as tk

    root = tk.Tk()
    output = (options or {}).get("output_dir") or Path.home() / ".cpu-memory-cleanup" / "runs"
    window = MemoryWindow(root, app, tk, output_dir=output)
    try:
        window.start()
        root.mainloop()
    finally:
        window._closing = True
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
