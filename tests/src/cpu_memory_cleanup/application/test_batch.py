"""包→假原生→批次记录→离线回看的完整受控旅程。"""
import json
from pathlib import Path
import tempfile
import threading
import unittest
from cpu_memory_cleanup import api
from ..system.platforms import test_windows_process as native


class BatchTests(unittest.TestCase):
    setUp = native.ProcessTests.setUp

    def test_batch_comparison_separates_native_cost_and_rebuilds_on_replay(self):
        from tests.fixtures import FakeMonotonic
        clock = FakeMonotonic()
        self.app._monotonic = clock
        self.fake.psapi.EmptyWorkingSet = native.Function(lambda handle: clock.advance_ms(80) or self.actions.append(handle) or True)
        with tempfile.TemporaryDirectory() as directory:
            report = self.app.memory_batch_cleanup([self.target, api.ProcessTarget(12, str((1 << 40) + 12))],
                directory, protections=[{"pid": 12, "created_at": str((1 << 40) + 12)}],
                confirmed=True, confirmation_source="synthetic")
            summary = report.analysis["comparison"]
            self.assertEqual((summary["selected_count"], summary["skipped_count"], summary["confirmed_calls"]), (2, 1, 1))
            self.assertAlmostEqual(summary["native_call_ms"], 80)
            self.assertGreaterEqual(summary["batch_wall_ms"], summary["native_call_ms"])
            self.assertIsNone(summary["system_released_bytes"])
            replay = api.create_application(offline=True).memory_result(report.output_dir)
            self.assertEqual(replay.analysis["comparison"], summary)
        self.assertEqual(self.actions, [11])

    def test_legacy_batch_has_unknown_wall_time_and_does_not_replay_actions(self):
        with tempfile.TemporaryDirectory() as directory:
            report = self.app.memory_batch_cleanup([self.target], directory, confirmed=True, confirmation_source="synthetic")
            (Path(report.output_dir) / "diagnostics.json").unlink()
            replay = api.create_application(offline=True).memory_result(report.output_dir)
            self.assertEqual(replay.completion, "success", replay.errors)
            self.assertIsNone(replay.analysis["comparison"]["batch_wall_ms"])
        self.assertEqual(self.actions, [11])

    def test_batch_native_attachment_failure_preserves_completed_business(self):
        from unittest.mock import patch
        original = self.app.records.write_diagnostics
        def save(path, value):
            if value.get("kind") == "batch":
                raise OSError("synthetic-sidecar-failure")
            return original(path, value)
        with tempfile.TemporaryDirectory() as directory, patch.object(self.app.records, "write_diagnostics", side_effect=save):
            report = self.app.memory_batch_cleanup([self.target], directory, confirmed=True, confirmation_source="synthetic")
            self.assertEqual(report.completion, "success", report.errors)
            self.assertEqual(report.metadata["diagnostics_errors"][0]["type"], "OSError")
            replay = api.create_application(offline=True).memory_result(report.output_dir)
            self.assertEqual(replay.completion, "success", replay.errors)
            self.assertIsNone(replay.analysis["comparison"]["batch_wall_ms"])
        self.assertEqual(self.actions, [11])

    def test_batch_skips_protection_and_replays_only_saved_child(self):
        with tempfile.TemporaryDirectory() as directory:
            report = self.app.memory_batch_cleanup([self.target, api.ProcessTarget(12, str((1 << 40) + 12))],
                directory, protections=[{"pid": 12, "created_at": str((1 << 40) + 12)}],
                confirmed=True, confirmation_source="test:user")
            self.assertEqual(report.completion, "success", report.errors)
            self.assertEqual(self.actions, [11])
            self.assertEqual([i["status"] for i in report.metadata["items"]], ["success", "skipped"])
            replay = api.create_application(offline=True).memory_result(report.output_dir)
            self.assertEqual(replay.completion, "success", replay.errors)
            self.assertEqual(self.actions, [11])

    def test_cancel_stops_unstarted_targets_and_keeps_completed_action(self):
        cancel = threading.Event()
        original = self.fake.psapi.EmptyWorkingSet
        def clean(handle):
            result = original(handle)
            cancel.set()
            return result
        self.fake.psapi.EmptyWorkingSet = native.Function(clean)
        with tempfile.TemporaryDirectory() as directory:
            report = self.app.memory_batch_cleanup([self.target, api.ProcessTarget(12, str((1 << 40) + 12))],
                directory, confirmed=True, confirmation_source="test:user", cancel_event=cancel)
            self.assertEqual(report.completion, "cancelled")
            self.assertEqual(self.actions, [11])
            self.assertEqual(report.metadata["items"][1]["status"], "not_started")

    def test_unconfirmed_batch_never_creates_directory_or_loads_dll(self):
        with tempfile.TemporaryDirectory() as directory:
            report = self.app.memory_batch_cleanup([self.target], directory)
            self.assertEqual(report.completion, "failed")
            self.assertEqual(list(Path(directory).iterdir()), [])
        self.assertEqual(self.fake.loads, [])

    def test_cli_request_and_offline_batch_use_shared_package(self):
        from cpu_memory_cleanup.interaction.cli import parse_args, run
        import contextlib
        import io
        with tempfile.TemporaryDirectory() as directory:
            request = Path(directory) / "request.json"
            request.write_text(json.dumps({"targets": [{"pid": 11, "created_at": self.target.created_at}]}), encoding="utf-8")
            args = parse_args(["--tool", "memory_batch_cleanup", "--request-file", str(request), "--confirm", "--output-dir", directory])
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = run(self.app, args)
            self.assertEqual(code, 0, output.getvalue())
            result = json.loads(output.getvalue())
            self.assertEqual(api.create_application(offline=True).memory_result(result["output_dir"]).completion, "success")
        self.assertEqual(self.actions, [11])
