"""内部实验的有限只读观察窗口，不持有维护方法或选择真实后端。"""

from dataclasses import asdict
import threading

from ..contracts import Sample, StageTimings, finite_number


class MemoryObserver:
    """调用方在上下文内自行执行一次动作；观察器只调用传入的读取函数。"""

    def __init__(self, read_sample, *, max_seconds=10, fast_seconds=10,
                 fast_interval=.25, slow_interval=1, max_samples=241, monotonic=None):
        if not callable(read_sample):
            raise ValueError("观察窗口需要显式只读回调")
        for name, value in (("max_seconds", max_seconds), ("fast_seconds", fast_seconds),
                            ("fast_interval", fast_interval), ("slow_interval", slow_interval)):
            finite_number(value, name)
        if not 0 < max_seconds <= 60 or not 0 <= fast_seconds <= 60:
            raise ValueError("观察窗口最多60秒")
        if not .25 <= fast_interval <= slow_interval <= 60:
            raise ValueError("采样间隔至少250毫秒，慢段不能更快")
        if type(max_samples) is not int or not 2 <= max_samples <= 241:
            raise ValueError("观察窗口最多241份样本")
        self.read_sample = read_sample
        self.max_seconds, self.fast_seconds = max_seconds, fast_seconds
        self.fast_interval, self.slow_interval = fast_interval, slow_interval
        self.max_samples, self.monotonic = max_samples, monotonic
        self.rows, self.errors = [], []
        self._stop = threading.Event()
        self._clock_lock = threading.Lock()
        self._thread = None
        self._timing = None
        self._closed = False

    def _offset(self):
        with self._clock_lock:
            return self._timing.offset()

    def _capture(self):
        if len(self.rows) >= self.max_samples:
            return
        requested = self._offset()
        if requested is None or requested >= self.max_seconds * 1000:
            return
        row = {"requested_offset_ms": requested, "observed_offset_ms": None, "sample": None}
        try:
            sample = self.read_sample()
            if not isinstance(sample, Sample):
                raise ValueError("只读回调未返回Sample")
            row["sample"] = asdict(sample)
        except Exception as error:
            row["error"] = {"type": type(error).__name__, "message": str(error)}
            self.errors.append(row["error"])
        row["observed_offset_ms"] = self._offset()
        self.rows.append(row)

    def _run(self):
        while not self._stop.is_set() and len(self.rows) < self.max_samples - 1:
            elapsed = self._offset()
            if elapsed is None:
                break
            remaining = self.max_seconds - elapsed / 1000
            interval = self.fast_interval if elapsed < self.fast_seconds * 1000 else self.slow_interval
            if remaining <= 0 or self._stop.wait(min(remaining, interval)):
                break
            self._capture()

    def __enter__(self):
        if self._timing is not None:
            raise RuntimeError("观察窗口只能启动一次")
        self._timing = StageTimings(self.monotonic)
        self._capture()
        self._thread = threading.Thread(target=self._run, name="memory-trial-observer", daemon=False)
        self._thread.start()
        return self

    def mark(self, name):
        if self._timing is None or self._closed or name not in ("action_started", "action_finished"):
            raise ValueError("无效观察标记或窗口状态")
        with self._clock_lock:
            if any(e["name"] == name for e in self._timing.events):
                raise ValueError("观察标记不能重复")
            self._timing.mark(name)

    def __exit__(self, *_):
        self._stop.set()
        self._thread.join()  # 等待在途只读调用返回，不强制结束线程。
        self._capture()
        self._closed = True
        self._ended_ms = self._offset()

    def trace(self, *, run_id=None):
        if not self._closed:
            raise RuntimeError("观察关闭后才能取得固定证据")
        observed = [r["observed_offset_ms"] for r in self.rows if r["observed_offset_ms"] is not None]
        gaps = [b - a for a, b in zip(observed, observed[1:])]
        value = {"version": 1, "run_id": run_id, "basis": "observer_monotonic", "samples": self.rows.copy(),
                "events": [dict(e) for e in self._timing.events],
                "errors": self.errors.copy() + self._timing.errors.copy(),
                "budget": {"max_seconds": self.max_seconds, "max_samples": self.max_samples,
                           "fast_seconds": self.fast_seconds, "fast_interval": self.fast_interval,
                           "slow_interval": self.slow_interval},
                "actual_interval_ms": {"min": min(gaps) if gaps else None, "max": max(gaps) if gaps else None},
                "elapsed_ms": self._ended_ms,
                "budget_exceeded": self._ended_ms is None or self._ended_ms > self.max_seconds * 1000,
                "exact_drop_time_known": False,
                "caveat": "偏移为读取完成时刻；只读回调阻塞无法被硬取消，超出窗口或预算如实保留"}
        from .verification import observer_summary
        value["summary"] = observer_summary(value)
        return value


def controlled_load(size_mib, commands, output):
    """实验自有进程的固定触页/读回任务，不接入系统采样或维护。"""
    import json
    import mmap
    import os
    import time
    if type(size_mib) is not int or not 1 <= size_mib <= 256:
        raise ValueError("自有负载大小必须为1..256 MiB")
    data = bytearray(size_mib * 1024 * 1024)
    page = mmap.PAGESIZE
    for offset in range(0, len(data), page):
        data[offset] = offset // page % 251
    expected = sum(offset // page % 251 for offset in range(0, len(data), page))
    def emit(value):
        output.write(json.dumps(value, ensure_ascii=False, allow_nan=False) + "\n")
        output.flush()
    emit({"kind": "ready", "pid": os.getpid(), "bytes": len(data), "page_bytes": page,
          "checksum": expected, "format_version": 1})
    while True:
        line = commands.readline(65)
        if not line or line.strip() == "quit":
            return
        if len(line) > 64 or line.strip() != "read":
            raise ValueError("负载协议只接受read或quit")
        started = time.perf_counter()
        actual = sum(data[offset] for offset in range(0, len(data), page))
        emit({"kind": "readback", "checksum": actual, "matches": actual == expected,
              "elapsed_ms": (time.perf_counter() - started) * 1000})


if __name__ == "__main__":
    import argparse
    import sys
    parser = argparse.ArgumentParser(description="内部固定负载：显式分配自有内存，标准输入read/quit，不采样或清理主机")
    parser.add_argument("--load-mib", type=int, required=True)
    options = parser.parse_args()
    controlled_load(options.load_mib, sys.stdin, sys.stdout)
