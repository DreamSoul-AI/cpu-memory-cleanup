"""单目标原生适配与包旅程，全部DLL与进程均为假对象。"""

import tempfile
import threading
import unittest
from unittest.mock import patch

from cpu_memory_cleanup import api
from cpu_memory_cleanup.system.platforms import windows
from tests.fixtures import FakeRuntime
from tests.fixtures import FakeMonotonic
from .test_windows import FakeSystem, Function


class ProcessTests(unittest.TestCase):
    def setUp(self):
        guard = patch.object(windows.c, "WinDLL", side_effect=AssertionError("不得加载真实DLL"), create=True)
        guard.start()
        self.addCleanup(guard.stop)
        self.fake = FakeSystem()
        self.actions = []
        self.fake.psapi.EmptyWorkingSet = Function(lambda handle: self.actions.append(handle) or True)
        guard = patch.object(windows, "_maintenance_lock", threading.Lock())
        guard.start()
        self.addCleanup(guard.stop)
        self.backend = windows.WindowsBackend(self.fake.load, now=FakeRuntime().now)
        self.app = api.create_application(reader=self.backend, maintainer=self.backend, runtime=FakeRuntime(),
            capabilities=api.Capabilities("synthetic", "available", ("working-set",), scopes=("system", "process")))
        self.target = api.ProcessTarget(11, str((1 << 40) + 11), "example.exe")

    def test_native_timing_excludes_target_identity_query(self):
        clock = FakeMonotonic()
        self.app._monotonic = clock
        original_identity = self.fake.kernel.GetProcessTimes.function
        def identity(*args):
            clock.advance_ms(30)
            return original_identity(*args)
        def native(handle):
            clock.advance_ms(80)
            self.actions.append(handle)
            return True
        self.fake.psapi.EmptyWorkingSet = Function(native)
        self.fake.kernel.GetProcessTimes = Function(identity)
        with tempfile.TemporaryDirectory() as directory:
            report = self.app.memory_process_cleanup(self.target, directory, confirmed=True, confirmation_source="synthetic")
        self.assertEqual(report.completion, "success", report.errors)
        self.assertEqual(self.actions, [11])
        self.assertEqual(self.fake.nt_calls, [])
        self.assertAlmostEqual(report.metadata["diagnostics"]["verification"]["native_call_ms"], 80)
        stages = report.metadata["diagnostics"]["timing"]["stages"]
        self.assertAlmostEqual(next(row["duration_ms"] for row in stages if row["name"] == "target_verify"), 30)
        self.assertEqual(report.metadata["diagnostics"]["verification"]["working_sets"]["matched"], 1)

    def test_target_cleanup_and_offline_replay_keep_identity_and_single_call(self):
        with tempfile.TemporaryDirectory() as directory:
            report = self.app.memory_process_cleanup(self.target, directory, confirmed=True,
                                                     confirmation_source="synthetic:explicit")
            self.assertEqual(report.completion, "success", report.errors)
            self.assertEqual(report.metadata["header"]["scope"], "process")
            self.assertEqual(report.samples[0].metrics["target"]["working_set_bytes"], 1100)
            self.assertEqual(self.actions, [11])
            self.assertEqual(self.fake.nt_calls, [])
            self.assertEqual(self.fake.adjustments, [])
            self.assertEqual(self.fake.closed.count(11), 1)
            loads = list(self.fake.loads)
            replay = api.create_application(offline=True).memory_result(report.output_dir)
            self.assertEqual(replay.completion, "success", replay.errors)
            self.assertEqual(replay.metadata["header"]["target"]["created_at"], self.target.created_at)
            self.assertEqual(replay.samples[1].metrics["target"], report.samples[1].metrics["target"])
            self.assertEqual(replay.action, report.action)
            self.assertEqual(self.fake.loads, loads)
            self.assertEqual(self.actions, [11])

    def test_changed_creation_identity_never_calls_global_or_target_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            report = self.app.memory_process_cleanup(api.ProcessTarget(11, "123"), directory,
                confirmed=True, confirmation_source="synthetic:explicit")
        self.assertEqual(report.completion, "failed")
        self.assertFalse(report.action["attempted"])
        self.assertEqual(self.actions, [])
        self.assertEqual(self.fake.nt_calls, [])
        self.assertEqual(self.fake.closed.count(11), 1)

    def test_unconfirmed_request_has_zero_native_access(self):
        with tempfile.TemporaryDirectory() as directory:
            report = self.app.memory_process_cleanup(self.target, directory)
        self.assertEqual(report.completion, "failed")
        self.assertEqual(self.fake.loads, [])

    def test_target_discovery_retains_partial_coverage_and_never_maintains(self):
        report = self.app.memory_targets()
        self.assertEqual(report.completion, "partial")
        self.assertEqual([p["pid"] for p in report.samples[0].processes], [11, 12])
        self.assertTrue(report.samples[0].errors)
        self.assertEqual(self.actions, [])
        self.assertEqual(self.fake.nt_calls, [])
        self.assertEqual(self.fake.closed.count(11), 1)
        self.assertEqual(self.fake.closed.count(12), 1)

    def test_cli_target_journey_preserves_scope_and_replays_without_actions(self):
        import contextlib
        import io
        import json
        from cpu_memory_cleanup.__main__ import main
        def call(argv, app):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main(argv, app=app)
            return code, json.loads(output.getvalue())
        code, targets = call(["--tool", "memory_targets"], self.app)
        self.assertEqual(code, 1)  # 假进程13不可读，列表仍保留可读目标。
        self.assertEqual(targets["samples"][0]["processes"][0]["pid"], 11)
        with tempfile.TemporaryDirectory() as directory:
            code, result = call(["--tool", "memory_process_cleanup", "--pid", "11",
                "--created-at", self.target.created_at, "--confirm", "--output-dir", directory], self.app)
            self.assertEqual(code, 0, result)
            self.assertEqual(result["analysis"]["scope"], "process")
            code, replay = call(["--tool", "memory_result", "--run-dir", result["output_dir"]],
                                api.create_application(offline=True))
            self.assertEqual(code, 0)
            self.assertEqual(replay["analysis"], result["analysis"])
        self.assertEqual(self.actions, [11])

    def test_fake_window_selects_target_without_changing_global_button(self):
        from cpu_memory_cleanup.interaction import desktop
        from cpu_memory_cleanup.interaction.workspace import Workspace
        from cpu_memory_cleanup.application.targets import targets_from_rows
        from ...interaction.test_desktop import FakeTk, Root, DeferredThread
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as directory, patch.object(desktop.threading, "Thread", DeferredThread):
            window = desktop.MemoryWindow(Root(), self.app, FakeTk, output_dir=directory)
            report = self.app.memory_targets()
            self.assertEqual(self.actions, [])
            workspace = Workspace.__new__(Workspace)
            workspace.owner, workspace.app = window, self.app
            workspace.settings = {"protections": []}
            workspace.window = None
            workspace.messagebox = Mock()
            workspace.messagebox.askyesno.return_value = True
            workspace.submit_batch(targets_from_rows(report.samples[0].processes[:1]))
            window._worker.finish()
            window.pump()
            self.assertEqual(self.actions, [11])
            self.assertEqual(window.button.cget("text"), "一键整理工作集")
            self.assertIn("逐项整理", window.result.cget("text"))
            self.assertEqual(window._latest_report.metadata["items"][0]["target"]["pid"], 11)

    def test_discovery_close_failure_blocks_later_maintenance(self):
        original = self.fake.close_handle
        self.fake.kernel.CloseHandle = Function(lambda handle: False if handle == 11 else original(handle))
        report = self.app.memory_targets()
        self.assertEqual(report.completion, "failed")
        with tempfile.TemporaryDirectory() as directory:
            result = self.app.memory_process_cleanup(self.target, directory, confirmed=True,
                confirmation_source="synthetic:explicit")
        self.assertEqual(result.completion, "failed")
        self.assertEqual(self.actions, [])

    def test_process_request_rejects_missing_or_invalid_identity(self):
        with self.assertRaises(ValueError):
            api.ActionRequest("working-set", scope="process")
        with self.assertRaises(ValueError):
            api.ProcessTarget(True, "123")
        with self.assertRaises(ValueError):
            api.ProcessTarget(11, "1.25")

    def test_replay_rejects_samples_for_a_different_target(self):
        import json
        from pathlib import Path
        with tempfile.TemporaryDirectory() as directory:
            result = self.app.memory_process_cleanup(self.target, directory, confirmed=True,
                confirmation_source="synthetic:explicit")
            path = Path(result.output_dir) / "samples.jsonl"
            records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            for record in records:
                if record.get("record_type") == "sample":
                    record["sample"]["metrics"]["target"]["pid"] = 12
                    break
            path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
            replay = api.create_application(offline=True).memory_result(result.output_dir)
        self.assertNotEqual(replay.completion, "success")
        self.assertEqual(self.actions, [11])
