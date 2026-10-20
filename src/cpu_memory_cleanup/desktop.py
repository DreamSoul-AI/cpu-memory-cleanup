"""Tk 交互与单工作线程；业务状态与取消语义由核心持有。"""

from pathlib import Path
import queue
import threading

from .contracts import ActionRequest, MECHANISMS


def _gib(value):
    return "未知" if value is None else f"{value / 1024 ** 3:.3f} GiB"


def _present(report):
    metrics = report.samples[-1].metrics if report.samples else {}
    physical, commit = metrics.get("physical") or {}, metrics.get("commit") or {}
    lines = [f"可用：{_gib(physical.get('available_bytes'))} / 总量：{_gib(physical.get('total_bytes'))}",
             f"提交：{_gib(commit.get('used_bytes'))} / 上限：{_gib(commit.get('limit_bytes'))}"]
    intervals = (report.analysis.get("cpu") or {}).get("intervals") or []
    if intervals:
        cpu = intervals[-1]
        value = cpu.get("busy_pct")
        lines.append("CPU：" + ("未知" if value is None else f"{value:.1f}%") + f"（{cpu.get('scope', 'unknown')}）")
    if report.mode == "maintain":
        action = report.action
        lines.append(f"维护调用：{action.get('call_outcome', 'unknown')}；特权恢复：{action.get('privilege_restored')}")
    lines.extend(str(error.get("message", error)) if isinstance(error, dict) else str(error) for error in report.errors)
    return "\n".join(lines)


class MemoryWindow:
    def __init__(self, root, app, tk, choose_directory, *, output_dir=None):
        self.root = root
        self.app = app
        self._choose_directory = choose_directory
        self._output_dir = output_dir
        self._events = queue.Queue()
        self._worker = None
        self._pending = None
        self._closing = False
        self._closed = False
        self._poll_job = None
        root.title("CPU 内存清理")
        root.geometry("560x620")
        self.mechanism = tk.StringVar(value="working-set")
        tk.Label(root, text="CPU 内存清理", font=("Microsoft YaHei UI", 24)).pack(pady=16)
        tk.Label(root, text="打开时只读；单次维护需明确选择机制并保存记录。", wraplength=500).pack()
        self.status = tk.Label(root, text="尚未维护", wraplength=500)
        self.status.pack(pady=12)
        self.result = tk.Label(root, text="正在读取…", justify="left", wraplength=500)
        self.result.pack(padx=20, pady=8, fill="x")
        for mechanism in MECHANISMS:
            tk.Radiobutton(root, text=mechanism, variable=self.mechanism, value=mechanism).pack(anchor="w", padx=30)
        self.button = tk.Button(root, text="执行所选单项维护", command=self.maintain)
        self.button.pack(pady=12)
        self.cancel_button = tk.Button(root, text="取消当前请求", command=self.cancel)
        self.cancel_button.pack()
        tk.Label(root, text="可用内存上升不代表加速收益。维护可能带来缺页与 I/O 代价。", wraplength=500).pack(pady=12)
        root.protocol("WM_DELETE_WINDOW", self.close)

    def start(self):
        self.inspect()
        self.root.after(50, self.pump)

    def _submit(self, function, *args):
        if self._closing or self._worker is not None or self.app.busy:
            return False

        def work():
            try:
                self._events.put((function(*args), None))
            except Exception as error:
                self._events.put((None, error))

        self._worker = threading.Thread(target=work, daemon=False)
        self._worker.start()
        return True

    def inspect(self):
        self._poll_job = None
        if not self._submit(self.app.inspect, {"count": 2, "interval": 1}):
            if not self._closing:
                self._poll_job = self.root.after(1000, self.inspect)

    def maintain(self):
        if self._closing or self._pending is not None or self.button.cget("state") == "disabled":
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
            if self._submit(self.app.maintain, request, directory):
                self._pending = None

    def cancel(self):
        if self._pending is not None:
            self._pending = None
            self.button.configure(state="normal")
        accepted = self.app.request_cancel()
        self.status.configure(text="核心已接受派发前取消；等待收尾。" if accepted else "未接受派发前取消；最终状态以本次报告为准。")

    def close(self):
        if self._closing:
            return
        self._closing = True
        self._pending = None
        if self._poll_job is not None:
            self.root.after_cancel(self._poll_job)
            self._poll_job = None
        self.app.shutdown()
        self.button.configure(state="disabled")
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
            try:
                report, error = self._events.get_nowait()
            except queue.Empty:
                report, error = None, RuntimeError("工作线程未返回结果")
            if not self._closing:
                if error is not None:
                    self.status.configure(text=f"流程异常，维护结果须核对：{error}")
                else:
                    self.status.configure(text=f"{report.mode}：{report.completion}；取消请求：{report.cancel_requested}")
                    self.result.configure(text=_present(report))
                self.button.configure(state="normal" if self._pending is None and not self.app.busy else "disabled")
                self._dispatch_pending()
                if self._worker is None and self._pending is None:
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
    window.start()
    root.mainloop()
    return 0
