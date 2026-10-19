"""Small one-window memory button. Tkinter, no extra packages.

The big button calls perform_release("working-set") — the same function as
``python -m cpu_memory_cleanup --release --release-what working-set``.
Standby is not called. There is no confirmation and no menu.
No process is killed, no service is stopped, and nothing is written to the
registry, startup, or system settings. Privilege handling stays inside
perform_release.
"""

import queue
import threading
import time
import tkinter as tk
from tkinter import font as tkfont

RELEASE_WHAT = "working-set"
POLL_MS = 200
ANIM_MS = 800

EVIDENCE_NOTE = (
    "日常桌面负载下收益未证实（cosmetic-or-harmful），释放后会有硬缺页代价。"
    "可用内存变高不等于收益。本次只清空 working set，不清除 standby"
    "（standby 单独 A/B：可用内存仅 +0.24/+0.30 GiB，硬缺页约 3 倍于 A/A 上限）。"
    "不结束进程，不改系统设置，不提权。"
)

BG = "#F4F7F4"
INK = "#1C2830"
MUTED = "#5C6B73"
GREEN = "#12B76A"
GREEN_DARK = "#0E9A58"
TRACK = "#E2E8E4"
BAR = "#12B76A"


def format_gib(value):
    return "不可用" if value is None else f"{value / 1024 ** 3:.3f} GiB"


def format_signed(value):
    return "不可用" if value is None else f"{value / 1024 ** 3:+.3f} GiB"


def used_percent(levels):
    """Physical memory used percent: (total - available) / total. Not a composite score."""
    total = levels.get("total_bytes")
    available = levels.get("available_bytes")
    if not total or available is None:
        return None
    return max(0.0, min(100.0, (total - available) / total * 100.0))


def smoothstep(start, end, t):
    """Interpolate start→end. t is 0..1 over the animation; outside that it clamps."""
    if t <= 0:
        return start
    if t >= 1:
        return end
    s = t * t * (3.0 - 2.0 * t)
    return start + (end - start) * s


def format_result(record):
    before = record.get("before") or {}
    after = record.get("after") or {}
    deltas = record.get("deltas") or {}
    lines = [
        f"可用 {format_gib(before.get('available_bytes'))} → {format_gib(after.get('available_bytes'))}"
        f"（{format_signed(deltas.get('available_bytes_delta'))}）",
        f"Commit {format_gib(before.get('commit_used_bytes'))} → {format_gib(after.get('commit_used_bytes'))}"
        f"（{format_signed(deltas.get('commit_used_bytes_delta'))}）",
    ]
    for action in record.get("actions") or []:
        if action.get("error"):
            lines.append(f"{action.get('what')}: {action['error']}")
        else:
            state = "OK" if action.get("ok") else "FAILED"
            lines.append(f"{action.get('what')} {state} {action.get('ntstatus_hex')} {action.get('ntstatus_name')}")
    if not record.get("cleanup_performed"):
        lines.append("释放没有成功。没有提权。")
    return "\n".join(lines)


class MemoryWindow:
    def __init__(self, root, levels_fn, release_fn, poll_ms=POLL_MS, anim_ms=ANIM_MS, clock=None):
        self.root = root
        self.levels_fn = levels_fn
        self.release_fn = release_fn
        self.poll_ms = poll_ms
        self.anim_ms = anim_ms
        self._clock = clock or time.monotonic
        self._events = queue.Queue()
        self._polling = False
        self._poll_busy = False
        self._releasing = False
        self._shown = None
        self._anim_from = None
        self._anim_to = None
        self._anim_t0 = None
        self._anim_job = None
        root.title("内存加速")
        root.configure(bg=BG)
        root.geometry("440x680")
        root.minsize(400, 620)
        family = "Microsoft YaHei UI"
        self.percent = tk.Label(root, text="--%", fg=INK, bg=BG, font=(family, 72, "bold"))
        self.percent.pack(pady=(22, 0))
        self.caption = tk.Label(root, text="内存占用 · 仅物理内存", fg=INK, bg=BG, font=(family, 14))
        self.caption.pack()
        self.formula = tk.Label(
            root, text="（总量 − 可用）/ 总量，不是 CPU 或综合评分",
            fg=MUTED, bg=BG, font=(family, 10),
        )
        self.formula.pack()
        self.bar = tk.Canvas(root, width=360, height=18, bg=TRACK, highlightthickness=0)
        self.bar.pack(pady=(14, 8))
        self.bar_fill = self.bar.create_rectangle(0, 0, 0, 18, fill=BAR, width=0)
        self.available = tk.Label(root, text="可用 — / 共 —", fg=INK, bg=BG, font=(family, 14))
        self.available.pack(pady=(10, 0))
        self.commit = tk.Label(root, text="Commit — / —", fg=INK, bg=BG, font=(family, 14))
        self.commit.pack(pady=(4, 16))
        self.button = tk.Button(
            root, text="一键加速", command=self.accelerate,
            bg=GREEN, fg="white", activebackground=GREEN_DARK, activeforeground="white",
            relief="flat", font=(family, 20, "bold"), cursor="hand2",
            padx=24, pady=14,
        )
        self.button.pack(fill="x", padx=36, pady=(4, 12))
        self.status = tk.Label(root, text="正在读取…", fg=MUTED, bg=BG, font=(family, 11))
        self.status.pack()
        self.result = tk.Label(
            root, text="点按钮后这里显示可用内存和 Commit 的前后值。",
            fg=INK, bg=BG, font=(family, 11), justify="left", wraplength=380,
        )
        self.result.pack(padx=28, pady=(12, 8), anchor="w")
        self.note = tk.Label(
            root, text=EVIDENCE_NOTE, fg=MUTED, bg=BG, font=(family, 10),
            justify="left", wraplength=380,
        )
        self.note.pack(padx=28, pady=(4, 16), anchor="w")

    def start(self):
        self._polling = True
        self.root.after(0, self._poll)
        self.root.after(50, self._drain)

    def _drain(self):
        self.pump()
        if self._polling:
            self.root.after(50, self._drain)

    def stop(self):
        self._polling = False

    def show_levels(self, levels):
        self.available.configure(
            text=f"可用 {format_gib(levels.get('available_bytes'))} / 共 {format_gib(levels.get('total_bytes'))}"
        )
        self.commit.configure(
            text=f"Commit {format_gib(levels.get('commit_used_bytes'))} / {format_gib(levels.get('commit_limit_bytes'))}"
        )
        self._set_target(used_percent(levels))
        if not self._releasing:
            self.status.configure(text="约每 0.2 秒刷新。百分比和用量条会平滑过渡。按钮只清空 working set。")

    def _set_target(self, target):
        if target is None:
            self._cancel_anim()
            self._shown = None
            self._paint_percent(None)
            return
        if self._shown is None:
            self._shown = target
            self._paint_percent(target)
            return
        if self._anim_job is None and abs(self._shown - target) < 0.05:
            self._shown = target
            self._paint_percent(target)
            return
        self._anim_to = target
        if self._anim_job is None:
            self._anim_from = self._shown
            self._anim_t0 = self._clock()
            self._anim_job = self.root.after(16, self._anim_frame)

    def _anim_frame(self):
        self._anim_job = None
        duration = self.anim_ms / 1000.0
        t = 0.0 if not duration else (self._clock() - self._anim_t0) / duration
        self._shown = smoothstep(self._anim_from, self._anim_to, t)
        if t >= 1:
            self._shown = self._anim_to
        self._paint_percent(self._shown)
        if t < 1:
            self._anim_job = self.root.after(16, self._anim_frame)

    def _cancel_anim(self):
        if self._anim_job is not None:
            self.root.after_cancel(self._anim_job)
            self._anim_job = None

    def _paint_percent(self, percent):
        self.percent.configure(text="--%" if percent is None else f"{percent:.0f}%")
        width = int(self.bar.cget("width"))
        height = int(self.bar.cget("height"))
        fill = 0 if not percent else int(width * max(0.0, min(100.0, percent)) / 100.0)
        self.bar.coords(self.bar_fill, 0, 0, fill, height)

    def show_levels_error(self, exc):
        self.status.configure(text=f"读取失败：{exc}")

    def accelerate(self):
        if self._releasing:
            return
        self._releasing = True
        self.button.configure(state="disabled", text="正在加速…")
        self.status.configure(text="正在加速… 只清空 working set。用量会跟着变。standby 不会动。")

        def work():
            try:
                record = self.release_fn(RELEASE_WHAT)
                error = None
            except Exception as exc:
                record = None
                error = exc
            self._events.put(("release", (record, error)))

        threading.Thread(target=work, daemon=True).start()

    def _finish(self, record, error):
        self._releasing = False
        self.button.configure(state="normal", text="一键加速")
        if error is not None:
            self.result.configure(text=f"释放失败，没有提权。\n{type(error).__name__}: {error}")
            self.status.configure(text="未完成")
            return
        self.result.configure(text=format_result(record))
        self.status.configure(text="这一次的前后值在上面。数字仍在刷新。")

    def pump(self):
        """Apply worker results. Safe to call from the Tk thread without mainloop."""
        while True:
            try:
                kind, payload = self._events.get_nowait()
            except queue.Empty:
                return
            if kind == "levels":
                self.show_levels(payload)
            elif kind == "levels-error":
                self.show_levels_error(payload)
            elif kind == "release":
                self._finish(*payload)

    def _schedule_poll(self):
        if self._polling:
            self.root.after(self.poll_ms, self._poll)

    def _poll(self):
        self.pump()
        if not self._polling or self._poll_busy:
            self._schedule_poll()
            return
        self._poll_busy = True

        def work():
            try:
                self._events.put(("levels", self.levels_fn()))
            except Exception as exc:
                self._events.put(("levels-error", exc))
            finally:
                self._poll_busy = False

        threading.Thread(target=work, daemon=True).start()
        self._schedule_poll()


def main():
    from .release import perform_release
    from .windows import host_levels

    root = tk.Tk()
    try:
        tkfont.nametofont("TkDefaultFont").configure(family="Microsoft YaHei UI")
    except tk.TclError:
        pass
    window = MemoryWindow(root, host_levels, perform_release)
    window.start()
    root.mainloop()


if __name__ == "__main__":
    main()
