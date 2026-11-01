"""独立采集窗口仅接入合成读取，验证动作期间可采样及安全收束。"""

import threading
import unittest
from cpu_memory_cleanup.application.experiments import MemoryObserver
from cpu_memory_cleanup.contracts import Sample


class ObserverTests(unittest.TestCase):
    def test_sampler_observes_changes_while_caller_is_busy(self):
        changed = threading.Event()
        state = {"available": 40}
        def read():
            value = state["available"]
            if value == 70:
                changed.set()
            return Sample("synthetic", 0, "2026-11-02T00:00:00+08:00", {"physical": {"total_bytes": 100, "available_bytes": value}})
        observer = MemoryObserver(read, max_seconds=2)
        with observer:
            observer.mark("action_started")
            state["available"] = 70
            self.assertTrue(changed.wait(.8), "调用方仍忙时观察线程应已读取变化")
            state["available"] = 60
            observer.mark("action_finished")
        trace = observer.trace(run_id="synthetic")
        self.assertEqual([r["sample"]["metrics"]["physical"]["available_bytes"] for r in trace["samples"]], [40, 70, 60])
        self.assertEqual([e["name"] for e in trace["events"]], ["action_started", "action_finished"])
        self.assertFalse(observer._thread.is_alive())
        self.assertFalse(trace["exact_drop_time_known"])

    def test_read_failure_is_unknown_and_final_sample_can_recover(self):
        def read():
            if not observer.rows:
                raise OSError("synthetic-read-failure")
            return Sample("synthetic", 0, "2026-11-02T00:00:00+08:00", {"physical": {"available_bytes": 20}})
        observer = MemoryObserver(read)
        with observer:
            pass
        trace = observer.trace()
        self.assertIsNone(trace["samples"][0]["sample"])
        self.assertEqual(trace["errors"][0]["type"], "OSError")
        self.assertEqual(trace["samples"][-1]["sample"]["metrics"]["physical"]["available_bytes"], 20)

    def test_exception_in_caller_stops_observer_without_owning_action(self):
        observer = MemoryObserver(lambda: Sample("s", 0, "2026-11-02T00:00:00+08:00", {}))
        with self.assertRaisesRegex(ValueError, "action"):
            with observer:
                raise ValueError("action")
        self.assertFalse(observer._thread.is_alive())
        self.assertEqual(observer.trace()["events"], [])

    def test_sampling_budget_stops_without_stopping_caller(self):
        from tests.fixtures import FakeMonotonic
        clock = FakeMonotonic()
        def read():
            clock.advance_ms(300)
            return Sample("s", 0, "2026-11-02T00:00:00+08:00", {})
        observer = MemoryObserver(read, max_seconds=.25, monotonic=clock)
        with observer:
            observer.mark("action_started")
            observer.mark("action_finished")
        trace = observer.trace()
        self.assertEqual(len(trace["samples"]), 1)
        self.assertTrue(trace["budget_exceeded"])
        self.assertEqual(len(trace["events"]), 2)

    def test_constructor_is_inert_and_rejects_unbounded_sampling(self):
        from unittest.mock import Mock
        read = Mock()
        observer = MemoryObserver(read)
        read.assert_not_called()
        with self.assertRaises(ValueError):
            MemoryObserver(read, max_seconds=61)
        with self.assertRaises(ValueError):
            MemoryObserver(read, fast_interval=.01)
        with self.assertRaises(RuntimeError):
            observer.trace()


class LoadTests(unittest.TestCase):
    def test_fixed_touch_and_readback_use_only_owned_buffer(self):
        import io
        import json
        from cpu_memory_cleanup.application.experiments import controlled_load
        out = io.StringIO()
        controlled_load(1, io.StringIO("read\nquit\n"), out)
        ready, result = [json.loads(line) for line in out.getvalue().splitlines()]
        self.assertEqual(ready["bytes"], 1024 * 1024)
        self.assertTrue(result["matches"])
        self.assertEqual(result["checksum"], ready["checksum"])

    def test_load_protocol_rejects_unbounded_size_before_allocation(self):
        import io
        from cpu_memory_cleanup.application.experiments import controlled_load
        with self.assertRaises(ValueError):
            controlled_load(257, io.StringIO(), io.StringIO())

    def test_module_load_child_exits_naturally_after_readback(self):
        import json
        import os
        from pathlib import Path
        import subprocess
        import sys
        import cpu_memory_cleanup
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(Path(cpu_memory_cleanup.__file__).resolve().parent.parent)
        result = subprocess.run([sys.executable, "-X", "utf8", "-m", "cpu_memory_cleanup.application.experiments",
            "--load-mib", "1"], input="read\nquit\n", text=True, capture_output=True, timeout=5, env=environment)
        self.assertEqual(result.returncode, 0, result.stderr)
        rows = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(rows[0]["kind"], "ready")
        self.assertTrue(rows[1]["matches"])
