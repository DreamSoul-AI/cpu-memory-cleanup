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
        self.assertEqual(first.completion, "partial")
        self.assertEqual(len(first.samples), 1)
        self.assertEqual(self.backend.reads, 1)
        self.assertEqual(self.backend.closes, 2)
        reader = self.app.records.open_read(first.output_dir)
        self.addCleanup(reader.close)
        saved = list(reader.read("operation"))[-1]
        self.assertEqual(saved["record_type"], "action_result")
        self.assertEqual(saved["action"]["call_outcome"], "success")
        self.assertFalse(saved["action"]["privilege_restored"])
        second = self.maintain()
        self.assertEqual(second.completion, "failed")
        self.assertEqual(self.app.inspect().completion, "failed")
        reviewed = self.app.review(first.output_dir)
        self.assertEqual(reviewed.completion, "partial")
        self.assertEqual(len(reviewed.samples), 1)
        self.assertEqual(reviewed.action, first.action)
        self.assertEqual(self.backend.opens, 1)
        self.assertEqual(self.backend.reads, 1)
        self.assertEqual(self.backend.calls, 1)

    def test_unverified_action_stops_system_reads(self):
        with patch.object(self.backend, "perform_once", side_effect=OSError("fake action failure")) as perform:
            report = self.maintain()
        self.assertEqual(perform.call_count, 1)
        self.assertEqual(report.action["call_outcome"], "unknown")
        self.assertIsNone(report.action["attempted"])
        self.assertEqual(len(report.samples), 1)
        self.assertEqual(self.backend.reads, 1)
        self.assertEqual(self.backend.closes, 2)
        reader = self.app.records.open_read(report.output_dir)
        self.addCleanup(reader.close)
        saved = list(reader.read("operation"))[-1]
        self.assertEqual(saved["record_type"], "action_result")
        self.assertEqual(saved["action"], report.action)

    def test_inspection_interrupt_preserves_incomplete_terminal(self):
        interruption = KeyboardInterrupt("fake inspection interruption")

        def interrupt_second_read():
            if self.backend.reads == 2:
                raise interruption

        self.backend.on_read = interrupt_second_read
        report = self.app._report("inspect")
        with patch.object(self.app, "_report", return_value=report):
            with self.assertRaises(KeyboardInterrupt) as caught:
                self.app.inspect({"count": 3, "interval": 0, "output_dir": self.temp.name})
        self.assertIs(caught.exception, interruption)
        self.assertFalse(self.app.busy)
        self.assertEqual(self.backend.closes, 1)
        reviewed = self.app.review(report.output_dir)
        self.assertEqual(len(reviewed.samples), 1)
        self.assertEqual(reviewed.metadata["terminal"]["completion"], "failed")
        self.assertEqual(reviewed.metadata["terminal"]["errors"][0]["type"], "KeyboardInterrupt")

    def test_after_read_interrupt_preserves_call_and_incomplete_terminal(self):
        interruption = KeyboardInterrupt("fake after-read interruption")

        def interrupt_second_read():
            if self.backend.reads == 2:
                raise interruption

        self.backend.on_read = interrupt_second_read
        report = self.app._report("maintain")
        with patch.object(self.app, "_report", return_value=report):
            with self.assertRaises(KeyboardInterrupt) as caught:
                self.maintain()
        self.assertIs(caught.exception, interruption)
        self.assertFalse(self.app.busy)
        self.assertEqual((self.backend.calls, self.backend.closes), (1, 2))
        reviewed = self.app.review(report.output_dir)
        self.assertEqual(reviewed.action["call_outcome"], "success")
        self.assertEqual(reviewed.metadata["terminal"]["completion"], "partial")
        self.assertEqual(reviewed.metadata["terminal"]["errors"][0]["type"], "KeyboardInterrupt")

    def test_dispatched_interrupt_saves_unknown_and_blocks_system_requests(self):
        interruption = KeyboardInterrupt("fake dispatched interruption")
        report = self.app._report("maintain")
        with patch.object(self.app, "_report", return_value=report):
            with patch.object(self.backend, "perform_once", side_effect=interruption) as perform:
                with self.assertRaises(KeyboardInterrupt) as caught:
                    self.maintain()
        self.assertIs(caught.exception, interruption)
        self.assertEqual(perform.call_count, 1)
        self.assertFalse(self.app.busy)
        self.assertEqual((self.backend.reads, self.backend.closes), (1, 2))
        reader = self.app.records.open_read(report.output_dir)
        self.addCleanup(reader.close)
        saved = list(reader.read("operation"))[-1]
        self.assertEqual(saved["record_type"], "action_result")
        self.assertEqual(saved["action"]["call_outcome"], "unknown")
        self.assertIsNone(saved["action"]["attempted"])
        self.assertIsNone(saved["action"]["privilege_restored"])
        self.assertEqual(self.app.inspect().completion, "failed")
        self.assertEqual(self.maintain().completion, "failed")
        reviewed = self.app.review(report.output_dir)
        self.assertEqual(reviewed.completion, "partial")
        self.assertEqual(reviewed.action, saved["action"])
        self.assertEqual(reviewed.metadata["terminal"]["completion"], "unknown")
        self.assertEqual(self.backend.reads, 1)

    def test_dispatched_interrupt_survives_action_record_failure(self):
        interruption = KeyboardInterrupt("fake dispatched interruption")
        append = records.Journal.append

        def fail_result(journal, kind, value):
            if value["record_type"] == "action_result":
                raise OSError("fake full disk")
            return append(journal, kind, value)

        report = self.app._report("maintain")
        with patch.object(self.app, "_report", return_value=report):
            with patch.object(self.backend, "perform_once", side_effect=interruption):
                with patch.object(records.Journal, "append", fail_result):
                    with self.assertRaises(KeyboardInterrupt) as caught:
                        self.maintain()
        self.assertIs(caught.exception, interruption)
        self.assertEqual(report.action["call_outcome"], "unknown")
        self.assertEqual([error["type"] for error in report.errors], ["KeyboardInterrupt", "OSError"])
        self.assertFalse(self.app.busy)
        self.assertEqual((self.backend.reads, self.backend.closes), (1, 2))
        self.assertEqual(self.app.inspect().completion, "failed")
        reviewed = self.app.review(report.output_dir)
        self.assertEqual(reviewed.action["call_outcome"], "unknown")
        self.assertEqual(reviewed.metadata["terminal"]["completion"], "unknown")

    def test_close_interrupt_finishes_other_resources_and_blocks_system_requests(self):
        interruption = KeyboardInterrupt("fake close interruption")
        close = self.backend.close
        close_attempts = []

        def interrupt_first_close():
            close_attempts.append(True)
            if len(close_attempts) == 1:
                raise interruption
            return close()

        report = self.app._report("maintain")
        with patch.object(self.app, "_report", return_value=report):
            with patch.object(self.backend, "close", interrupt_first_close):
                with self.assertRaises(KeyboardInterrupt) as caught:
                    self.maintain()
        self.assertIs(caught.exception, interruption)
        self.assertEqual(len(close_attempts), 2)
        self.assertFalse(self.app.busy)
        self.assertEqual(self.app.inspect().completion, "failed")
        reviewed = self.app.review(report.output_dir)
        self.assertEqual(reviewed.action["call_outcome"], "success")
        self.assertEqual(reviewed.metadata["terminal"]["completion"], "partial")
        self.assertEqual(reviewed.metadata["terminal"]["errors"][0]["phase"], "close")

    def test_close_failure_blocks_next_maintenance(self):
        self.backend.closed = False
        self.maintain()
        self.maintain()
        self.assertEqual(self.backend.calls, 1)

    def test_reported_unknown_action_stops_after_sample_without_error_list(self):
        action = {"attempted": True, "call_outcome": "unknown", "status": None,
                  "privilege_restored": True, "errors": []}
        with patch.object(self.backend, "perform_once", return_value=action) as perform:
            report = self.maintain()
        self.assertEqual(perform.call_count, 1)
        self.assertEqual(report.completion, "unknown")
        self.assertEqual(report.action, action)
        self.assertEqual(len(report.samples), 1)
        self.assertEqual(self.backend.reads, 1)
        self.assertEqual(self.backend.closes, 2)
        reader = self.app.records.open_read(report.output_dir)
        self.addCleanup(reader.close)
        saved = list(reader.read("operation"))[-1]
        self.assertEqual(saved["action"], action)

    def test_reported_unknown_action_blocks_new_system_requests_but_allows_review(self):
        action = {"attempted": None, "call_outcome": "unknown", "status": None,
                  "privilege_restored": True, "errors": ["native result unavailable"]}
        with patch.object(self.backend, "perform_once", return_value=action) as perform:
            report = self.maintain()
            self.assertEqual(self.app.inspect().completion, "failed")
            self.assertEqual(self.maintain().completion, "failed")
            reviewed = self.app.review(report.output_dir)
        self.assertEqual(perform.call_count, 1)
        self.assertEqual(reviewed.completion, "partial")
        self.assertEqual(reviewed.action, action)
        self.assertEqual(reviewed.metadata["terminal"]["completion"], "unknown")
        self.assertEqual((self.backend.opens, self.backend.reads, self.backend.calls), (1, 1, 0))

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

    def test_terminal_commit_rejects_late_cancel_and_keeps_busy(self):
        commit = records.Journal.commit_end
        observed = {}

        def commit_with_cancel(journal, terminal):
            observed["cancel_accepted"] = self.app.request_cancel()
            observed["busy"] = self.app.busy
            observed["overlap"] = self.app.inspect().completion
            return commit(journal, terminal)

        with patch.object(records.Journal, "commit_end", commit_with_cancel):
            report = self.app.inspect({"output_dir": self.temp.name})
        self.assertEqual(observed, {"cancel_accepted": False, "busy": True, "overlap": "failed"})
        self.assertEqual(self.backend.opens, 1)
        self.assertEqual(report.completion, "success")
        self.assertFalse(report.cancel_requested)
        reviewed = self.app.review(report.output_dir)
        self.assertEqual(reviewed.metadata["terminal"]["completion"], report.completion)
        self.assertEqual(reviewed.metadata["terminal"]["cancel_requested"], report.cancel_requested)
        self.backend.on_read = self.app.request_cancel
        self.assertEqual(self.app.inspect().completion, "cancelled")

    def test_shutdown_during_terminal_commit_keeps_committed_outcome(self):
        commit = records.Journal.commit_end

        def commit_with_shutdown(journal, terminal):
            self.app.shutdown()
            return commit(journal, terminal)

        with patch.object(records.Journal, "commit_end", commit_with_shutdown):
            report = self.app.inspect({"output_dir": self.temp.name})
        self.assertEqual(report.completion, "success")
        self.assertFalse(report.cancel_requested)
        reviewed = make_app().review(report.output_dir)
        self.assertEqual(reviewed.metadata["terminal"]["completion"], report.completion)
        self.assertEqual(reviewed.metadata["terminal"]["cancel_requested"], report.cancel_requested)
        self.assertEqual(self.app.inspect().completion, "failed")
        self.assertEqual(self.backend.opens, 1)

    def test_review_close_rejects_late_cancel_and_keeps_busy(self):
        original = self.app.inspect({"output_dir": self.temp.name})
        close = records.RunReader.close
        observed = {}

        def close_with_cancel(reader):
            observed["cancel_accepted"] = self.app.request_cancel()
            observed["busy"] = self.app.busy
            observed["overlap"] = self.app.inspect().completion
            return close(reader)

        with patch.object(records.RunReader, "close", close_with_cancel):
            report = self.app.review(original.output_dir)
        self.assertEqual(observed, {"cancel_accepted": False, "busy": True, "overlap": "failed"})
        self.assertEqual(report.completion, "success")
        self.assertFalse(report.cancel_requested)
        self.assertEqual(self.backend.opens, 1)
        self.assertFalse(self.app.busy)

    def test_analysis_before_header_is_rejected(self):
        original = self.app.inspect({"output_dir": self.temp.name})
        path = Path(original.output_dir) / "samples.jsonl"
        lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        foreign = {"record_type": "analysis", "run_id": "foreign-run", "analysis": {"source": "foreign-run"}}
        lines = [foreign] + [line for line in lines if line["record_type"] != "analysis"]
        path.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")
        report = self.app.review(original.output_dir)
        self.assertEqual(report.completion, "failed")
        self.assertEqual(report.analysis, {})
        self.assertEqual(report.errors[0]["type"], "ValueError")
        self.assertEqual(self.backend.opens, 1)

    def test_sample_after_saved_analysis_is_rejected(self):
        original = self.app.inspect({"output_dir": self.temp.name, "count": 2, "interval": 0})
        path = Path(original.output_dir) / "samples.jsonl"
        lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        analysis = next(line for line in lines if line["record_type"] == "analysis")
        analysis["analysis"] = core.rules.analyze(original.samples[:1])
        lines.remove(analysis)
        lines.insert(2, analysis)
        path.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")
        report = self.app.review(original.output_dir)
        self.assertEqual(report.completion, "failed")
        self.assertEqual(report.analysis, {})
        self.assertEqual(report.errors[0]["type"], "ValueError")
        self.assertEqual((self.backend.opens, self.backend.calls), (1, 0))

    def test_review_corrupt_tail_preserves_confirmed_action_and_samples(self):
        original = self.maintain()
        path = Path(original.output_dir) / "operation.jsonl"
        path.write_text(path.read_text(encoding="utf-8") + "{broken JSON\n", encoding="utf-8")
        report = self.app.review(original.output_dir)
        self.assertEqual(report.completion, "failed")
        self.assertEqual(report.action, original.action)
        self.assertEqual(report.samples, original.samples)
        self.assertEqual(report.metadata["header"]["run_id"], original.run_id)
        self.assertEqual(report.analysis, {})
        self.assertTrue(all(fact["closed"] for fact in report.metadata["resource_closures"]))
        self.assertFalse(self.app.busy)
        self.assertEqual((self.backend.opens, self.backend.calls), (1, 1))

    def test_review_unreadable_action_is_unknown_instead_of_not_started(self):
        original = self.maintain()
        path = Path(original.output_dir) / "operation.jsonl"
        path.write_text("{broken JSON\n", encoding="utf-8")
        report = self.app.review(original.output_dir)
        self.assertEqual(report.completion, "failed")
        self.assertIsNone(report.action["attempted"])
        self.assertEqual(report.action["call_outcome"], "unknown")
        self.assertEqual(report.samples, original.samples)
        self.assertEqual(report.metadata["header"]["mode"], "maintain")
        self.assertEqual(self.backend.calls, 1)

    def test_review_invalid_utf8_tail_preserves_confirmed_action(self):
        original = self.maintain()
        path = Path(original.output_dir) / "operation.jsonl"
        path.write_bytes(path.read_bytes() + b"\xff\n")
        report = self.app.review(original.output_dir)
        self.assertEqual(report.completion, "failed")
        self.assertEqual(report.action, original.action)
        self.assertEqual(report.samples, original.samples)
        self.assertEqual(report.analysis, {})
        self.assertIn("第 3 行", report.errors[0]["message"])
        self.assertEqual((self.backend.opens, self.backend.calls), (1, 1))

    def test_comparison_read_failure_keeps_primary_action_and_samples(self):
        original = self.maintain()
        other = self.app.inspect({"output_dir": self.temp.name})
        (Path(other.output_dir) / "samples.jsonl").write_text("{broken JSON\n", encoding="utf-8")
        report = self.app.review(original.output_dir, comparison=other.output_dir)
        self.assertEqual(report.completion, "failed")
        self.assertEqual(report.action, original.action)
        self.assertEqual(report.samples, original.samples)
        self.assertEqual(report.metadata["header"]["run_id"], original.run_id)
        self.assertEqual((self.backend.opens, self.backend.calls), (2, 1))

    def test_review_invalid_samples_preserve_confirmed_action(self):
        for source_case in ("corrupt_tail", "header_only"):
            with self.subTest(source_case=source_case):
                original = self.maintain()
                path = Path(original.output_dir) / "samples.jsonl"
                text = path.read_text(encoding="utf-8")
                expected_samples = original.samples
                if source_case == "corrupt_tail":
                    text += "{broken JSON\n"
                else:
                    text = text.splitlines()[0] + "\n"
                    expected_samples = []
                path.write_text(text, encoding="utf-8")
                calls_before = (self.backend.opens, self.backend.calls)
                report = self.app.review(original.output_dir)
                self.assertEqual(report.completion, "failed")
                self.assertEqual(report.action, original.action)
                self.assertEqual(report.samples, expected_samples)
                self.assertEqual(report.metadata["header"]["run_id"], original.run_id)
                self.assertEqual(report.analysis, {})
                self.assertTrue(all(fact["closed"] for fact in report.metadata["resource_closures"]))
                self.assertFalse(self.app.busy)
                self.assertEqual((self.backend.opens, self.backend.calls), calls_before)

    def test_review_mixed_legacy_runs_preserves_only_matching_prefix(self):
        path = Path(self.temp.name) / "mixed-legacy.jsonl"
        rows = [{"run_id": "main-run", "physical": {"available_bytes": 40}},
                {"run_id": "foreign-run", "physical": {"available_bytes": 90}}]
        path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
        report = self.app.review(path)
        self.assertEqual(report.completion, "failed")
        self.assertEqual([sample.run_id for sample in report.samples], ["main-run"])
        self.assertEqual(report.samples[0].metrics["physical"]["available_bytes"], 40)
        self.assertEqual(report.analysis, {})
        self.assertIn("不能混合运行", report.errors[0]["message"])
        self.assertEqual((self.backend.opens, self.backend.calls), (0, 0))

    def test_external_cancel_during_commit_keeps_frozen_terminal(self):
        event = threading.Event()
        commit = records.Journal.commit_end
        observed = {}

        def cancel_during_commit(journal, terminal):
            event.set()
            observed["accepted"] = self.app.request_cancel()
            return commit(journal, terminal)

        with patch.object(records.Journal, "commit_end", cancel_during_commit):
            report = self.app.inspect({"output_dir": self.temp.name}, cancel_event=event)
        self.assertFalse(observed["accepted"])
        self.assertEqual(report.completion, "success")
        self.assertFalse(report.cancel_requested)
        reviewed = self.app.review(report.output_dir)
        self.assertEqual(reviewed.metadata["terminal"]["completion"], report.completion)
        self.assertEqual(reviewed.metadata["terminal"]["cancel_requested"], report.cancel_requested)
        self.assertEqual(self.app.inspect().completion, "success")

    def test_review_read_interrupt_survives_close_interrupt_and_keeps_prefix(self):
        interruption = KeyboardInterrupt("fake read interruption")
        close_interruption = SystemExit("fake close interruption")

        class InterruptedReader:
            def read(self, kind="samples"):
                yield {"run_id": "same-run", "physical": {"available_bytes": 40}}
                raise interruption

            def close(self):
                raise close_interruption

        report = self.app._report("review")
        with patch.object(self.app, "_report", return_value=report):
            with patch.object(self.app.records, "open_read", return_value=InterruptedReader()):
                with self.assertRaises(KeyboardInterrupt) as caught:
                    self.app.review("fake-source")
        self.assertIs(caught.exception, interruption)
        self.assertEqual([sample.run_id for sample in report.samples], ["same-run"])
        self.assertEqual([error["type"] for error in report.errors], ["KeyboardInterrupt", "SystemExit"])
        self.assertEqual(report.analysis, {})
        self.assertFalse(self.app.busy)
        self.assertEqual(self.app.inspect().completion, "failed")
        self.assertEqual((self.backend.opens, self.backend.calls), (0, 0))

    def test_invalid_cancel_event_is_rejected_before_admission(self):
        report = self.app.inspect(cancel_event=object())
        self.assertEqual(report.completion, "failed")
        self.assertEqual(report.errors[0]["type"], "ValueError")
        self.assertFalse(self.app.busy)
        self.assertEqual((self.backend.opens, self.backend.calls), (0, 0))

    def test_review_sample_limit_preserves_only_accepted_prefix(self):
        path = Path(self.temp.name) / "too-many-samples.jsonl"
        path.write_text("".join(json.dumps({"run_id": "same-run", "index": index}) + "\n"
                                for index in range(core.MAX_SAMPLES + 1)), encoding="utf-8")
        report = self.app.review(path)
        self.assertEqual(report.completion, "failed")
        self.assertEqual(len(report.samples), core.MAX_SAMPLES)
        self.assertEqual(report.samples[-1].sequence, core.MAX_SAMPLES - 1)
        self.assertEqual(report.analysis, {})
        self.assertIn("样本数量超出上限", report.errors[0]["message"])
        self.assertEqual((self.backend.opens, self.backend.calls), (0, 0))

    def test_review_close_interrupt_releases_busy_and_blocks_system_requests(self):
        original = self.app.inspect({"output_dir": self.temp.name})
        interruption = KeyboardInterrupt("fake reader close interruption")
        close = records.RunReader.close

        def interrupt_close(reader):
            close(reader)
            raise interruption

        with patch.object(records.RunReader, "close", interrupt_close):
            with self.assertRaises(KeyboardInterrupt) as caught:
                self.app.review(original.output_dir)
        self.assertIs(caught.exception, interruption)
        self.assertFalse(self.app.busy)
        self.assertEqual(self.app.inspect().completion, "failed")
        self.assertEqual((self.backend.opens, self.backend.calls), (1, 0))

    def test_review_invalid_header_run_id_never_reads_actions(self):
        original = self.maintain()
        path = Path(original.output_dir) / "samples.jsonl"
        operation = Path(original.output_dir) / "operation.jsonl"
        header = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
        actions = [json.loads(line) for line in operation.read_text(encoding="utf-8").splitlines()]
        read = records.RunReader.read
        for identity_case, value in (("missing", None), ("empty", ""), ("null", None), ("nonstring", 7)):
            with self.subTest(identity_case=identity_case):
                changed = {**header, "run_id": value}
                if identity_case == "missing":
                    del changed["run_id"]
                path.write_text(json.dumps(changed) + "\n", encoding="utf-8")
                operation.write_text("\n".join(json.dumps({**row, "run_id": value}) for row in actions) + "\n", encoding="utf-8")
                channels = []

                def track(reader, kind="samples"):
                    channels.append(kind)
                    yield from read(reader, kind)

                with patch.object(records.RunReader, "read", track):
                    report = self.app.review(original.output_dir)
                self.assertEqual(report.completion, "failed")
                self.assertIsNone(report.metadata["header"])
                self.assertEqual(channels, ["samples"])
                self.assertEqual(report.samples, [])
                self.assertEqual(report.analysis, {})
                self.assertIsNone(report.action["attempted"])
                self.assertEqual(report.action["call_outcome"], "unknown")
                self.assertEqual((self.backend.opens, self.backend.calls), (1, 1))

    def test_terminal_clock_failure_closes_records_and_releases_busy(self):
        for mode in ("inspect", "maintain"):
            with self.subTest(mode=mode):
                self.backend = FakeBackend()
                self.app = make_app(self.backend, self.backend, sleep=lambda seconds: None)
                journals = []
                open_write = self.app.records.open_write
                stamp = self.app._clock()
                clock_calls = []

                def open_and_track(*args, **kwargs):
                    journal = open_write(*args, **kwargs)
                    journals.append(journal)
                    return journal

                def fail_terminal_clock():
                    clock_calls.append(True)
                    if len(clock_calls) == 2:
                        raise OSError("fake terminal clock failure")
                    return stamp

                with patch.object(self.app.records, "open_write", open_and_track):
                    with patch.object(self.app, "_clock", fail_terminal_clock):
                        report = (self.app.inspect({"output_dir": self.temp.name})
                                  if mode == "inspect" else self.maintain())
                self.assertEqual(report.completion, "failed" if mode == "inspect" else "partial")
                self.assertEqual(len(report.samples), 1 if mode == "inspect" else 2)
                self.assertEqual(report.errors[-1]["phase"], "terminal_record")
                self.assertNotIn("finished_at", report.metadata["terminal"])
                self.assertFalse(self.app.busy)
                self.assertTrue(journals[0].sealed)
                self.assertEqual(journals[0].streams, {})
                self.assertFalse(journals[0].committed)
                rows = [json.loads(line) for line in journals[0].sample_path.read_text(encoding="utf-8").splitlines()]
                self.assertFalse(any(row["record_type"] == "end" for row in rows))
                self.assertEqual(self.app.inspect().completion, "success")

    def test_terminal_clock_failure_preserves_original_interrupt(self):
        interruption = KeyboardInterrupt("fake read interruption")
        self.backend.on_read = lambda: (_ for _ in ()).throw(interruption)
        report = self.app._report("inspect")
        with patch.object(self.app, "_report", return_value=report):
            with patch.object(self.app, "_clock", side_effect=[self.app._clock(), OSError("fake clock failure")]):
                with self.assertRaises(KeyboardInterrupt) as caught:
                    self.app.inspect({"output_dir": self.temp.name})
        self.assertIs(caught.exception, interruption)
        self.assertEqual([error["type"] for error in report.errors], ["KeyboardInterrupt", "OSError"])
        self.assertFalse(self.app.busy)
        self.assertEqual(self.backend.closes, 1)
        self.assertTrue(all(fact["closed"] for fact in report.metadata["resource_closures"]))

    def test_terminal_clock_interrupt_closes_records_before_rethrow(self):
        interruption = KeyboardInterrupt("fake terminal clock interruption")
        report = self.app._report("inspect")
        with patch.object(self.app, "_report", return_value=report):
            with patch.object(self.app, "_clock", side_effect=[self.app._clock(), interruption]):
                with self.assertRaises(KeyboardInterrupt) as caught:
                    self.app.inspect({"output_dir": self.temp.name})
        self.assertIs(caught.exception, interruption)
        self.assertFalse(self.app.busy)
        self.assertEqual(report.completion, "failed")
        self.assertEqual(report.errors[-1]["type"], "KeyboardInterrupt")
        self.assertEqual(len(report.samples), 1)
        self.assertTrue(all(fact["closed"] for fact in report.metadata["resource_closures"]))
        self.assertEqual(self.app.inspect().completion, "success")

    def test_review_rejects_contradictory_action_intent(self):
        for field, value in (("mechanism", "standby"), ("scope", "process")):
            with self.subTest(field=field):
                original = self.maintain()
                path = Path(original.output_dir) / "operation.jsonl"
                rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
                rows[0][field] = value
                content = "\n".join(json.dumps(row) for row in rows) + "\n"
                path.write_text(content, encoding="utf-8")
                calls_before = (self.backend.opens, self.backend.calls)
                report = self.app.review(original.output_dir)
                self.assertEqual(report.completion, "failed")
                self.assertIsNone(report.action["attempted"])
                self.assertEqual(report.action["call_outcome"], "unknown")
                self.assertEqual(report.samples, original.samples)
                self.assertEqual(report.metadata["header"], original.metadata["header"])
                self.assertEqual(report.analysis, {})
                self.assertEqual(report.errors[0]["type"], "ValueError")
                self.assertEqual(path.read_text(encoding="utf-8"), content)
                self.assertEqual((self.backend.opens, self.backend.calls), calls_before)

    def test_review_accepts_legacy_undeclared_intent_fields(self):
        for missing_fields in ("intent", "header_mechanism"):
            with self.subTest(missing_fields=missing_fields):
                original = self.maintain()
                path = Path(original.output_dir) / ("operation.jsonl" if missing_fields == "intent" else "samples.jsonl")
                rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
                rows[0].pop("mechanism")
                if missing_fields == "intent":
                    rows[0].pop("scope")
                path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
                calls_before = (self.backend.opens, self.backend.calls)
                report = self.app.review(original.output_dir)
                self.assertEqual(report.completion, "success")
                self.assertEqual(report.action, original.action)
                self.assertEqual(report.samples, original.samples)
                self.assertEqual(report.analysis, original.analysis)
                self.assertFalse(report.metadata["recomputed"])
                self.assertEqual((self.backend.opens, self.backend.calls), calls_before)

    def test_review_rejects_success_terminal_with_errors(self):
        for source_role in ("primary", "comparison"):
            with self.subTest(source_role=source_role):
                original = self.maintain()
                source = original if source_role == "primary" else self.app.inspect({"output_dir": self.temp.name})
                path = Path(source.output_dir) / "samples.jsonl"
                rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
                rows[-1]["errors"] = [{"phase": "close", "type": "OSError", "message": "fake close failure"}]
                content = "\n".join(json.dumps(row) for row in rows) + "\n"
                path.write_text(content, encoding="utf-8")
                calls_before = (self.backend.opens, self.backend.calls)
                report = self.app.review(original.output_dir, comparison=source.output_dir if source_role == "comparison" else None)
                self.assertEqual(report.completion, "failed")
                self.assertEqual(report.action, original.action)
                self.assertEqual(report.samples, original.samples)
                self.assertEqual(report.analysis, {})
                self.assertEqual(report.errors[0]["type"], "ValueError")
                if source_role == "primary":
                    self.assertEqual(report.metadata["terminal"], rows[-1])
                self.assertEqual(path.read_text(encoding="utf-8"), content)
                self.assertEqual((self.backend.opens, self.backend.calls), calls_before)
