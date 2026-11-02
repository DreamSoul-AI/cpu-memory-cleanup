"""离线测试共享装配，运行信息为合成数据，不读取主机指标。"""

import logging
from cpu_memory_cleanup.application.core import Application
from cpu_memory_cleanup.contracts import Capabilities
from cpu_memory_cleanup.system.records import RecordStore


class FakeRuntime:
    def now(self):
        return "2026-10-22T00:00:00+00:00"

    def information(self):
        return {"build_id": "synthetic-build", "environment_id": "synthetic-environment",
                "environment": {"system": "fake", "version": "test", "python": "test"}}

    def diagnostic(self, event):
        logging.getLogger("cpu_memory_cleanup").warning(event["event"])


class FakeMonotonic:
    """显式推进阶段耗时，不睡眠也不使用主机指标。"""
    def __init__(self):
        self.seconds = 0.0

    def __call__(self):
        return self.seconds

    def advance_ms(self, milliseconds):
        self.seconds += milliseconds / 1000


def create_application(*args, **kwargs):
    kwargs.setdefault("records", RecordStore())
    kwargs.setdefault("runtime", FakeRuntime())
    kwargs.setdefault("capabilities", Capabilities("fake", "available", ("working-set", "standby")))
    return Application(*args, **kwargs)


class ScenarioBackend:
    """实际窗口与安装验收共用的合成接入，不包含ctypes或系统查询。"""
    def __init__(self):
        import threading
        self.lock = threading.Lock()
        self.available = int(16 * 1024**3 * 0.24)
        self.rows = [{"pid": p, "created_at": str(100000 + p), "name": name,
                      "image_path": path, "working_set_bytes": size * 1024**2}
                     for p, name, path, size in (
                         (101, "browser.exe", r"C:\Demo\Browser\browser.exe", 960),
                         (102, "browser.exe", r"C:\Demo\Browser\browser.exe", 640),
                         (201, "meeting.exe", r"C:\Demo\Meeting\meeting.exe", 420),
                         (301, "editor.exe", r"C:\Demo\Editor\editor.exe", 540))]
        self.actions = []

    def read_memory(self):
        from cpu_memory_cleanup.contracts import Sample
        with self.lock:
            available = self.available
        return Sample("synthetic-display", 0, FakeRuntime().now(), {
            "physical": {"total_bytes": 16 * 1024**3, "available_bytes": available},
            "commit": {"used_bytes": 18 * 1024**3, "limit_bytes": 32 * 1024**3}})

    def process_targets(self):
        with self.lock:
            return [dict(row) for row in self.rows], []

    def open_memory_observation(self):
        backend = self
        class Observation:
            def read(self, run_id, sequence):
                from cpu_memory_cleanup.contracts import Sample
                value = backend.read_memory()
                return Sample(run_id, sequence, value.timestamp, value.metrics)
            def close(self):
                return {"closed": True, "errors": []}
        return Observation()

    def open_operation(self, request):
        backend = self
        class Operation:
            consumed = False
            def preflight(self):
                if request.scope == "process":
                    self.row()
                return {"supported": True, "privilege_available": True, "lock_acquired": True, "scope": request.scope}
            def row(self):
                match = next((r for r in backend.rows if r["pid"] == request.target.pid and
                              r["created_at"] == request.target.created_at), None)
                if match is None or (request.target.image_path and match["image_path"] != request.target.image_path):
                    raise ValueError("合成目标身份不匹配")
                return match
            def read_target(self):
                with backend.lock:
                    row = self.row()
                    return {key: row[key] for key in ("pid", "created_at", "working_set_bytes")}
            def perform_once(self):
                if self.consumed:
                    raise ValueError("合成动作重复")
                self.consumed = True
                with backend.lock:
                    rows = [self.row()] if request.scope == "process" else backend.rows
                    for row in rows:
                        row["working_set_bytes"] = int(row["working_set_bytes"] * 0.6)
                    backend.available = min(12 * 1024**3, backend.available + 200 * 1024**2)
                    backend.actions.append({"scope": request.scope, "pid": request.target.pid if request.target else None})
                return {"attempted": True, "call_outcome": "success", "status": 0, "privilege_restored": True, "errors": []}
            def close(self):
                return {"closed": True, "errors": []}
        return Operation()
