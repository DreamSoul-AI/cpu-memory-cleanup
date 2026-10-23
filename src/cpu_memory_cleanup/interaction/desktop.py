"""Tk 交互与单工作线程；业务状态与取消语义由核心持有。"""

from pathlib import Path
import queue
import threading

from ..contracts import ActionRequest


def _gib(value):
    return "未知" if value is None else f"{value / 1024 ** 3:.3f} GiB"


def _present(report):
    metrics = report.samples[-1].metrics if report.samples else {}
    physical, commit = metrics.get("physical") or {}, metrics.get("commit") or {}
    intervals = (report.analysis.get("cpu") or {}).get("intervals") or []
    cpu = intervals[-1] if intervals else {}
    value = cpu.get("busy_pct")
    scope = {"system": "全机", "group": "当前处理器组"}.get(cpu.get("scope"), "范围未知")
    coverage = {"complete": "覆盖完整", "partial": "覆盖不全"}.get(cpu.get("coverage"), "覆盖未知")
    cards = {"cpu": ("未知" if value is None else f"{value:.1f}%", f"{scope} · {coverage}"),
             "physical": (_gib(physical.get("available_bytes")), f"物理总量 {_gib(physical.get('total_bytes'))}"),
             "commit": (_gib(commit.get("used_bytes")), f"提交上限 {_gib(commit.get('limit_bytes'))}")}
    lines = []
    if cpu.get("scope") == "group":
        lines.append("CPU 仅覆盖当前处理器组，不能代表全机。")
    if cpu.get("missing_reason"):
        lines.append("CPU：" + cpu["missing_reason"])
    errors = (report.samples[-1].errors if report.samples else []) + report.errors
    lines.extend(str(error.get("message") or error.get("reason") or error) if isinstance(error, dict) else str(error)
                 for error in errors)
    if not lines:
        lines.append("当前样本未报告读取缺口。" if report.samples else "尚未取得样本，指标保持未知。")
    if report.mode == "maintain":
        action = report.action
        outcome = {"success": "调用成功", "failure": "调用失败", "not_started": "未调用"}.get(action.get("call_outcome"), "结果未知")
        restored = {True: "已确认", False: "恢复失败", None: "未知"}.get(action.get("privilege_restored"), "未知")
        lines.insert(0, f"维护：{outcome} · 特权恢复：{restored}")
    timestamp = report.samples[-1].timestamp if report.samples else None
    return {"cards": cards, "context": f"最近样本 {timestamp or '未知'} · 本次 {len(report.samples)} 拍",
            "details": "\n".join(lines)}


class MemoryWindow:
    def __init__(self, root, app, tk, choose_directory, *, output_dir=None):
        self.root = root
        self.app = app
        self._choose_directory = choose_directory
        self._output_dir = output_dir
        self._events = queue.Queue()
        self._worker = None
        self._cancel_event = None
        self._pending = None
        self._closing = False
        self._closed = False
        self._poll_job = None
        root.title("CPU 内存清理")
        root.geometry("820x760")
        root.minsize(800, 700)
        root.configure(bg="#f3f5f9")
        self._mechanisms = app.capabilities.mechanisms
        self.mechanism = tk.StringVar(value=self._mechanisms[0] if self._mechanisms else "")
        body = tk.Frame(root, bg="#f3f5f9", padx=24, pady=18)
        body.pack(fill="both", expand=True)
        tk.Label(body, text="CPU 内存清理", bg="#f3f5f9", fg="#17233b",
                 font=("Microsoft YaHei UI", 24, "bold")).pack(anchor="w")
        tk.Label(body, text="默认只读 · CPU 诊断与内存观察", bg="#f3f5f9", fg="#52627a").pack(anchor="w", pady=(4, 12))
        self.status = tk.Label(body, text="只读诊断：等待采样", bg="#e7eefc", fg="#244982", anchor="w", padx=12, pady=9)
        self.status.pack(fill="x")
        dashboard = tk.Frame(body, bg="#f3f5f9")
        dashboard.pack(fill="x", pady=14)
        self.metric_labels, self.metric_notes = {}, {}
        for key, title in (("cpu", "CPU 占用"), ("physical", "可用物理内存"), ("commit", "系统提交量")):
            card = tk.Frame(dashboard, bg="white", padx=14, pady=14, relief="solid", bd=1)
            card.pack(side="left", fill="both", expand=True, padx=4)
            tk.Label(card, text=title, bg="white", fg="#52627a").pack(anchor="w")
            self.metric_labels[key] = tk.Label(card, text="未知", bg="white", fg="#17233b",
                                               font=("Microsoft YaHei UI", 20, "bold"))
            self.metric_labels[key].pack(anchor="w", pady=8)
            self.metric_notes[key] = tk.Label(card, text="等待有效样本", bg="white", fg="#52627a", wraplength=200, justify="left")
            self.metric_notes[key].pack(anchor="w")
        self.context = tk.Label(body, text="最近样本：未知", bg="#f3f5f9", fg="#52627a", anchor="w")
        self.context.pack(fill="x")
        controls = tk.Frame(body, bg="#f3f5f9")
        controls.pack(fill="x", pady=10)
        self.refresh_button = tk.Button(controls, text="刷新只读诊断", command=self.refresh, bg="#244982", fg="white", padx=14, pady=6)
        self.refresh_button.pack(side="left")
        self.cancel_button = tk.Button(controls, text="取消当前请求", command=self.cancel, state="disabled", padx=12, pady=6)
        self.cancel_button.pack(side="left", padx=10)
        diagnostics = tk.LabelFrame(body, text="诊断说明与数据缺口", bg="white", fg="#17233b", padx=12, pady=8)
        diagnostics.pack(fill="both", expand=True, pady=(0, 12))
        self.result = tk.Label(diagnostics, text="尚未取得样本，指标保持未知。", bg="white", fg="#33435d",
                               justify="left", anchor="nw", wraplength=700)
        self.result.pack(fill="both", expand=True)
        maintenance = tk.LabelFrame(body, text="单项维护 · 需主动选择", bg="#fff8ee", fg="#80521c", padx=12, pady=8)
        maintenance.pack(fill="x")
        tk.Label(maintenance, text="可用量上升不代表加速。维护可能带来缺页和 I/O 代价。",
                 bg="#fff8ee", fg="#80521c", wraplength=690).pack(anchor="w")
        choices = tk.Frame(maintenance, bg="#fff8ee")
        choices.pack(fill="x", pady=6)
        for mechanism in self._mechanisms:
            label = {"working-set": "系统工作集", "standby": "Standby 缓存"}.get(mechanism, mechanism)
            tk.Radiobutton(choices, text=label, variable=self.mechanism, value=mechanism, bg="#fff8ee").pack(side="left", padx=(0, 14))
        self.button = tk.Button(maintenance, text="选择目录并执行单项维护", command=self.maintain, state="normal" if self._mechanisms else "disabled", padx=12, pady=5)
        self.button.pack(anchor="w")
        root.protocol("WM_DELETE_WINDOW", self.close)

    def start(self):
        self.inspect()
        self.root.after(50, self.pump)

    def _submit(self, function, *args):
        if self._closing or self._worker is not None or self.app.busy:
            return False
        cancel_event = threading.Event()
        self._cancel_event = cancel_event

        def work():
            try:
                self._events.put((function(*args, cancel_event=cancel_event), None))
            except Exception as error:
                self._events.put((None, error))

        self.refresh_button.configure(state="disabled")
        self.cancel_button.configure(state="normal")
        try:
            self._worker = threading.Thread(target=work, daemon=False)
            self._worker.start()
        except Exception as error:
            self._worker = None
            self._cancel_event = None
            self.refresh_button.configure(state="normal")
            self.cancel_button.configure(state="disabled")
            self.button.configure(state="normal" if self._mechanisms else "disabled")
            self.status.configure(text=f"无法启动工作线程：{error}。本次请求未执行。")
            return False
        return True

    def inspect(self):
        self._poll_job = None
        if self._submit(self.app.inspect, {"count": 2, "interval": 1}):
            self.status.configure(text="只读诊断：读取中…")
        else:
            if not self._closing:
                self._poll_job = self.root.after(1000, self.inspect)

    def refresh(self):
        if self._closing or self._pending is not None or self._worker is not None or self.app.busy:
            return
        if self._poll_job is not None:
            self.root.after_cancel(self._poll_job)
            self._poll_job = None
        self.inspect()

    def maintain(self):
        if self._closing or not self._mechanisms or self._pending is not None or self.button.cget("state") == "disabled":
            return
        if self._worker is None and self.app.busy:
            self.status.configure(text="核心尚未确认收尾，不能接受新维护。")
            return
        directory = self._output_dir or self._choose_directory()
        if not directory or self._closing:
            return
        request = ActionRequest(self.mechanism.get(), confirmed=True, confirmation_source="desktop:button")
        if self._poll_job is not None:
            self.root.after_cancel(self._poll_job)
            self._poll_job = None
        self._pending = (request, Path(directory))
        self.button.configure(state="disabled")
        self.status.configure(text="维护请求已提交；等待当前只读流程收尾。")
        self._dispatch_pending()

    def _dispatch_pending(self):
        if self._pending is not None and self._worker is None and not self.app.busy and not self._closing:
            request, directory = self._pending
            self._pending = None
            if self._submit(self.app.maintain, request, directory):
                self.status.configure(text="单项维护：执行中，请等待收尾…")
            else:
                self._poll_job = self.root.after(1000, self.inspect)

    def cancel(self):
        cancelled_pending = self._pending is not None
        if self._pending is not None:
            self._pending = None
            self.button.configure(state="normal")
        if self._cancel_event is not None:
            self._cancel_event.set()
        accepted = self.app.request_cancel()
        text = "核心已接受派发前取消；等待收尾。" if accepted else "未接受派发前取消；最终状态以本次报告为准。"
        if not accepted and self._cancel_event is not None:
            text = "取消请求已登记；最终状态以本次报告为准。"
        self.status.configure(text=("待执行维护已撤销。" if cancelled_pending else "") + text)

    def close(self):
        if self._closing:
            return
        self._closing = True
        self._pending = None
        if self._poll_job is not None:
            self.root.after_cancel(self._poll_job)
            self._poll_job = None
        if self._cancel_event is not None:
            self._cancel_event.set()
        self.app.shutdown()
        self.button.configure(state="disabled")
        self.refresh_button.configure(state="disabled")
        self.cancel_button.configure(state="disabled")
        self.status.configure(text="正在等待当前流程安全收尾…")
        if self._worker is None and not self.app.busy:
            self._closed = True
            self.root.destroy()

    def pump(self):
        # 工作线程只投递数据，Tk 控件始终在主线程更新。
        if self._closed:
            return
        if self._worker is not None and not self._worker.is_alive():
            self._worker.join()
            self._worker = None
            self._cancel_event = None
            try:
                report, error = self._events.get_nowait()
            except queue.Empty:
                report, error = None, RuntimeError("工作线程未返回结果")
            if not self._closing:
                if error is not None:
                    self.status.configure(text=f"流程异常：{error}。已有数据未更新，最终状态须核对。")
                else:
                    mode = {"inspect": "只读诊断", "maintain": "单项维护", "review": "离线回看"}.get(report.mode, report.mode)
                    completion = {"success": "已完成", "partial": "结果不完整", "failed": "失败", "cancelled": "已取消", "unknown": "结果未知"}.get(report.completion, report.completion)
                    self.status.configure(text=f"{mode}：{completion}" + (" · 已收到取消请求" if report.cancel_requested else ""))
                    presentation = _present(report)
                    for key, (value, note) in presentation["cards"].items():
                        self.metric_labels[key].configure(text=value)
                        self.metric_notes[key].configure(text=note)
                    self.context.configure(text=presentation["context"])
                    self.result.configure(text=presentation["details"])
                self.button.configure(state="normal" if self._mechanisms and self._pending is None and not self.app.busy else "disabled")
                self._dispatch_pending()
                self.refresh_button.configure(state="normal" if self._worker is None and not self.app.busy else "disabled")
                self.cancel_button.configure(state="normal" if self._worker is not None or self._pending is not None else "disabled")
                if self._worker is None and self._pending is None and self._poll_job is None:
                    self._poll_job = self.root.after(1000, self.inspect)
        if self._closing:
            if self._worker is None and not self.app.busy:
                self._closed = True
                self.root.destroy()
                return
        self.root.after(50, self.pump)


def run(app, options=None):
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    window = MemoryWindow(root, app, tk, lambda: filedialog.askdirectory(title="选择维护记录的保存目录"),
                          output_dir=(options or {}).get("output_dir"))
    try:
        window.start()
        root.mainloop()
    finally:
        window._closing = True
        window._pending = None
        if window._cancel_event is not None:
            window._cancel_event.set()
        app.shutdown()
        if window._worker is not None:
            window._worker.join()
            window._worker = None
            window._cancel_event = None
        if not window._closed:
            window._closed = True
            try:
                root.destroy()
            except tk.TclError:
                pass  # Tk 已销毁不能覆盖退出原因。
    return 0
