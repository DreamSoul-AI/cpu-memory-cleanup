from tests.support import make_app
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from cpu_memory_cleanup.application import core
from cpu_memory_cleanup.system import records
from cpu_memory_cleanup.contracts import ActionRequest, Sample


class FakeBackend:
    def __init__(self):
        self.opens = self.calls = self.reads = self.closes = 0
        self.fail_read = None
        self.on_read = None
        self.ready = True
        self.restored = True
        self.closed = True
        self.outcome = "success"
        self.attempted = True

    def open_observation(self, **options):
        self.opens += 1
        return self

    def open_operation(self, request):
        return self

    def preflight(self):
        return {"supported": self.ready, "privilege_available": self.ready,
                "lock_acquired": True, "scope": "system"}

    def read(self, run_id, sequence):
        self.reads += 1
        if self.on_read:
            self.on_read()
        if self.reads == self.fail_read:
            raise OSError("fake read failure")
        return Sample(run_id, sequence, f"2026-10-21T00:00:{sequence:02d}+00:00",
                      {"physical": {"total_bytes": 100, "available_bytes": 40},
                       "commit": {"used_bytes": 20, "limit_bytes": 100}, "rates": {}})

    def perform_once(self):
        self.calls += 1
        return {"attempted": self.attempted, "call_outcome": self.outcome, "status": 0,
                "privilege_restored": self.restored, "errors": []}

    def close(self):
        self.closes += 1
        return {"closed": self.closed, "errors": []}


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.backend = FakeBackend()
        self.app = make_app(self.backend, self.backend, sleep=lambda seconds: None)
        self.request = ActionRequest("working-set", confirmed=True, confirmation_source="test")

    def maintain(self):
        return self.app.maintain(self.request, self.temp.name)

    def test_inspection_is_read_only(self):
        report = self.app.inspect()
        self.assertEqual(report.completion, "success")
        self.assertEqual(self.backend.calls, 0)

    def test_named_sample_file_does_not_parse_itself_as_actions(self):
        path = Path(self.temp.name) / "operation.jsonl"
        saved = self.app.inspect({"jsonl_path": path})
        reviewed = self.app.review(path)
        self.assertEqual(saved.completion, "success")
        self.assertEqual(reviewed.completion, "success")
        self.assertEqual(reviewed.errors, [])
        self.assertFalse(reviewed.action["attempted"])
        self.assertEqual(self.backend.calls, 0)

    def test_inspection_review_ignores_unrelated_sibling_actions(self):
        path = Path(self.temp.name) / "readonly.jsonl"
        (path.parent / "operation.jsonl").write_text(
            '{"record_type":"intent","run_id":"unrelated"}\n', encoding="utf-8")
        saved = self.app.inspect({"jsonl_path": path})
        reviewed = self.app.review(path)
        self.assertEqual(saved.completion, "success")
        self.assertEqual(reviewed.completion, "success")
        self.assertEqual(reviewed.errors, [])
        self.assertFalse(reviewed.action["attempted"])
        self.assertEqual(self.backend.calls, 0)

    def test_top_is_forwarded_to_analysis_and_saved_conditions(self):
        report = self.app.inspect({"with_processes": True, "top": 2})
        self.assertEqual(report.analysis["process_ranking"]["limit"], 2)
        self.assertEqual(report.metadata["header"]["parameters"]["conditions"]["top"], 2)

    def test_missing_confirmation_prevents_system_access(self):
        report = self.app.maintain(ActionRequest("standby"), self.temp.name)
        self.assertEqual(self.backend.opens, 0)
        self.assertFalse(report.action["attempted"])

    def test_missing_preflight_capability_prevents_call(self):
        self.backend.ready = None
        report = self.maintain()
        self.assertEqual(report.completion, "failed")
        self.assertEqual(self.backend.calls, 0)

    def test_one_maintenance_has_one_native_call(self):
        report = self.maintain()
        self.assertEqual(report.completion, "success")
        self.assertEqual(self.backend.calls, 1)
        self.assertEqual(self.backend.closes, 2)

    def test_after_read_failure_preserves_successful_call(self):
        self.backend.fail_read = 2
        report = self.maintain()
        self.assertEqual(report.action["call_outcome"], "success")
        self.assertEqual(report.completion, "partial")
        self.assertEqual(self.backend.calls, 1)

    def test_intent_write_failure_prevents_call(self):
        append = records.Journal.append

        def fail(journal, kind, value):
            if value["record_type"] == "intent":
                raise OSError("full disk")
            return append(journal, kind, value)

        with patch.object(records.Journal, "append", fail):
            report = self.maintain()
        self.assertFalse(report.action["attempted"])
        self.assertEqual(self.backend.calls, 0)

    def test_result_write_failure_preserves_attempt(self):
        append = records.Journal.append

        def fail(journal, kind, value):
            if value["record_type"] == "action_result":
                raise OSError("full disk")
            return append(journal, kind, value)

        with patch.object(records.Journal, "append", fail):
            report = self.maintain()
        self.assertEqual(report.action["call_outcome"], "success")
        self.assertEqual(report.completion, "partial")
        self.assertEqual(self.backend.calls, 1)

    def test_restore_failure_blocks_next_maintenance(self):
        self.backend.restored = False
        first = self.maintain()
        second = self.maintain()
        self.assertEqual(first.completion, "partial")
        self.assertEqual(second.completion, "failed")
        self.assertEqual(self.backend.calls, 1)

    def test_close_failure_blocks_next_maintenance(self):
        self.backend.closed = False
        self.maintain()
        self.maintain()
        self.assertEqual(self.backend.calls, 1)

    def test_unclosed_resources_block_further_system_reads(self):
        self.backend.closed = False
        self.app.inspect()
        self.app.inspect()
        self.assertEqual(self.backend.opens, 1)

    def test_predispatch_cancel_is_persisted_and_closes_resources(self):
        self.backend.on_read = self.app.request_cancel
        report = self.maintain()
        self.assertEqual(report.completion, "cancelled")
        self.assertEqual(self.backend.calls, 0)
        self.assertEqual(self.backend.closes, 2)
        reader = self.app.records.open_read(report.output_dir)
        self.addCleanup(reader.close)
        action = list(reader.read("operation"))[-1]
        self.assertFalse(action["action"]["attempted"])

    def test_duplicate_request_does_not_overlap(self):
        entered, release = threading.Event(), threading.Event()
        self.backend.on_read = lambda: (entered.set(), release.wait(2))
        worker = threading.Thread(target=self.app.inspect)
        worker.start()
        try:
            self.assertTrue(entered.wait(1))
            report = self.app.inspect()
            self.assertEqual(report.completion, "failed")
            self.assertEqual(self.backend.opens, 1)
        finally:
            release.set()
            worker.join(3)
        self.assertFalse(worker.is_alive())

    def test_review_preserves_saved_analysis_without_backend(self):
        original = self.app.inspect({"output_dir": self.temp.name, "conditions": {"top": 2}})
        opens = self.backend.opens
        report = self.app.review(original.output_dir)
        self.assertEqual(report.analysis, original.analysis)
        self.assertFalse(report.metadata["recomputed"])
        self.assertEqual(self.backend.opens, opens)

    def test_explicit_recompute_keeps_original_conditions(self):
        original = self.app.inspect({"output_dir": self.temp.name, "conditions": {"top": 2}})
        report = self.app.review(original.output_dir, conditions={"top": 1})
        self.assertTrue(report.metadata["recomputed"])
        self.assertEqual(report.metadata["header"]["parameters"]["conditions"]["top"], 2)

    def test_nonfinite_file_is_rejected_without_backend(self):
        path = Path(self.temp.name) / "bad.jsonl"
        path.write_text('{"rates":{"page_reads_per_sec":NaN}}\n', encoding="utf-8")
        report = self.app.review(path)
        self.assertEqual(report.completion, "failed")
        self.assertEqual(self.backend.opens, 0)

    def test_unknown_history_is_not_replayed(self):
        original = self.maintain()
        operation = Path(original.output_dir) / "operation.jsonl"
        lines = operation.read_text(encoding="utf-8").splitlines()
        operation.write_text(lines[0] + "\n", encoding="utf-8")
        report = self.app.review(original.output_dir)
        self.assertIsNone(report.action["attempted"])
        self.assertEqual(self.backend.calls, 1)

    def test_missing_action_file_is_unknown_in_review(self):
        original = self.maintain()
        (Path(original.output_dir) / "operation.jsonl").unlink()
        report = self.app.review(original.output_dir)
        self.assertIsNone(report.action["attempted"])
        self.assertEqual(report.completion, "partial")
        self.assertEqual(self.backend.calls, 1)

    def test_empty_action_file_is_unknown_in_review(self):
        original = self.maintain()
        (Path(original.output_dir) / "operation.jsonl").write_text("", encoding="utf-8")
        report = self.app.review(original.output_dir)
        self.assertEqual(report.action["call_outcome"], "unknown")

    def test_missing_version_cannot_be_complete_review(self):
        original = self.app.inspect({"output_dir": self.temp.name})
        path = Path(original.output_dir) / "samples.jsonl"
        lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        del lines[0]["rule_version"]
        path.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")
        report = self.app.review(original.output_dir)
        self.assertEqual(report.completion, "partial")

    def test_unknown_timestamp_marks_new_record_incomplete(self):
        original = self.app.inspect({"output_dir": self.temp.name})
        path = Path(original.output_dir) / "samples.jsonl"
        lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        lines[1]["sample"]["timestamp"] = None
        path.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")
        self.assertEqual(self.app.review(original.output_dir).completion, "partial")

    def test_contradictory_historical_action_is_rejected(self):
        original = self.maintain()
        path = Path(original.output_dir) / "operation.jsonl"
        lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        lines[-1]["action"]["attempted"] = False
        path.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")
        self.assertEqual(self.app.review(original.output_dir).completion, "failed")

    def test_record_close_failure_leaves_no_successful_terminal(self):
        close = records.Journal.close

        def fail(journal):
            close(journal)
            return {"closed": False, "errors": ["fake close failure"]}

        with patch.object(records.Journal, "close", fail):
            original = self.maintain()
        self.assertEqual(original.completion, "partial")
        report = self.app.review(original.output_dir)
        self.assertEqual(report.completion, "partial")
        self.assertIsNone(report.metadata["terminal"])
        self.maintain()
        self.assertEqual(self.backend.calls, 1)

    def test_legacy_tree_counts_and_signed_residual_survive_review(self):
        path = Path(self.temp.name) / "tree.jsonl"
        record = {"kind": "tree-series", "run_id": "old", "index": 3,
                  "utc": "2026-10-21T00:00:00+00:00", "local": "2026-10-21 08:00:00",
                  "processes": 7, "host": {"free_gib": 3, "commit_used_gib": 5},
                  "commit_residual": {"residual_gib": -0.5},
                  "codex_tree": {"processes": 2, "private_gib": 1}}
        path.write_text(json.dumps(record) + "\n", encoding="utf-8")
        report = self.app.review(path)
        self.assertEqual(report.samples[0].sequence, 3)
        self.assertEqual(report.samples[0].metrics["processes"], 7)
        self.assertEqual(report.samples[0].metrics["commit_residual"]["residual_gib"], -0.5)
        self.assertEqual(self.backend.opens, 0)

    def test_legacy_warnings_survive_review(self):
        path = Path(self.temp.name) / "legacy-warnings.jsonl"
        warnings = ["sampling overrun", "no previous PDH interval"]
        path.write_text(json.dumps({"warnings": warnings}) + "\n", encoding="utf-8")
        report = self.app.review(path)
        self.assertEqual(report.completion, "partial")
        self.assertEqual(report.samples[0].errors[:2], warnings)
        self.assertEqual(report.analysis["warning_count"], 2)
        self.assertEqual(report.analysis["overrun_count"], 1)
        self.assertEqual(report.analysis["first_tick_rate_warning_count"], 1)
        self.assertEqual(self.backend.opens, 0)

    def test_empty_preflight_is_refused(self):
        with patch.object(self.backend, "preflight", return_value={}):
            self.maintain()
        self.assertEqual(self.backend.calls, 0)

    def test_invalid_interval_never_opens_reader(self):
        report = self.app.inspect({"interval": float("nan")})
        self.assertEqual(report.completion, "failed")
        self.assertEqual(self.backend.opens, 0)

    def test_shutdown_before_worker_admission_prevents_call(self):
        self.app.shutdown()
        report = self.maintain()
        self.assertEqual(report.completion, "failed")
        self.assertEqual(self.backend.opens, 0)
        self.assertEqual(self.backend.calls, 0)

    def test_diagnostic_failure_does_not_erase_call(self):
        self.backend.fail_read = 2
        with patch.object(self.app.runtime, "diagnostic", side_effect=OSError("sink unavailable")):
            report = self.maintain()
        self.assertEqual(report.action["call_outcome"], "success")
        self.assertEqual(report.completion, "partial")

    def test_review_accepts_cancel_between_record_reads_and_closes_reader(self):
        original = self.app.inspect({"output_dir": self.temp.name})
        read = records.RunReader.read

        def cancel(reader, kind="samples"):
            for record in read(reader, kind):
                self.assertTrue(self.app.request_cancel())
                yield record

        with patch.object(records.RunReader, "read", cancel):
            report = self.app.review(original.output_dir)
        self.assertEqual(report.completion, "cancelled")
        self.assertTrue(report.cancel_requested)
        self.assertTrue(all(f["closed"] for f in report.metadata["resource_closures"]))
        self.assertFalse(self.app.busy)
        self.assertEqual(self.backend.calls, 0)

    def test_review_cancel_after_analysis_keeps_result_and_closes_resources(self):
        original = self.app.inspect({"output_dir": self.temp.name})
        analyze = core.rules.analyze

        def cancel(*args, **kwargs):
            result = analyze(*args, **kwargs)
            self.app.request_cancel()
            return result

        with patch.object(core.rules, "analyze", cancel):
            report = self.app.review(original.output_dir, conditions={})
        self.assertEqual(report.completion, "cancelled")
        self.assertTrue(report.cancel_requested)
        self.assertEqual(len(report.samples), 1)
        self.assertIn("cpu", report.analysis)

    def test_close_failure_preserves_details_and_blocks_next_system_read(self):
        with patch.object(self.backend, "close", return_value={"closed": False, "errors": ["query close failed"]}):
            report = self.app.inspect()
        self.assertEqual(report.metadata["resource_closures"][0]["errors"], ["query close failed"])
        self.app.inspect()
        self.assertEqual(self.backend.opens, 1)

    def test_unadvertised_mechanism_is_rejected_before_opening_system(self):
        report = self.app.maintain(ActionRequest("different-mechanism", confirmed=True, confirmation_source="test"), self.temp.name)
        self.assertEqual(report.completion, "failed")
        self.assertEqual(self.backend.opens, 0)
        self.assertEqual(self.backend.calls, 0)
