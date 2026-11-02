"""仅重放合成数据，禁止通过核验模块读取系统。"""
import copy
import unittest
from cpu_memory_cleanup.application.verification import diagnostics, memory_timeline, working_set_changes, validate_attachment
from cpu_memory_cleanup.contracts import Sample


def sample(second, available):
    return Sample("r", second, f"2026-11-02T00:00:{second:02d}+00:00",
                  {"physical": {"total_bytes": 100, "available_bytes": available}})


def snapshot(rows, errors=()):
    return {"processes": [{"pid": p, "created_at": identity, "working_set_bytes": size}
                          for p, identity, size in rows], "errors": list(errors)}


class VerificationTests(unittest.TestCase):
    def test_observer_classifies_in_call_drop_and_later_rebound(self):
        from cpu_memory_cleanup.application.verification import observer_summary
        trace = {"version": 1, "basis": "observer_monotonic", "events": [
            {"name": "action_started", "offset_ms": 10}, {"name": "action_finished", "offset_ms": 200}],
            "samples": [{"observed_offset_ms": ms, "sample": {"metrics": {"physical": {"available_bytes": value}}}}
                        for ms, value in [(0, 40), (100, 70), (250, 60)]]}
        result = observer_summary(trace)
        self.assertEqual(result["first_observed_gain_phase"], "during_action")
        self.assertEqual(result["rebound_used_bytes"], 10)
        self.assertIsNone(result["first_significant_gain_ms"])
        self.assertFalse(result["exact_drop_time_known"])

    def test_observer_distinguishes_post_call_drop_and_unknown_baseline(self):
        from cpu_memory_cleanup.application.verification import observer_summary
        trace = {"version": 1, "basis": "observer_monotonic", "events": [
            {"name": "action_started", "offset_ms": 10}, {"name": "action_finished", "offset_ms": 50}],
            "samples": [{"observed_offset_ms": ms, "sample": {"metrics": {"physical": {"available_bytes": value}}}}
                        for ms, value in [(0, 40), (100, 70)]]}
        self.assertEqual(observer_summary(trace)["first_observed_gain_phase"], "after_action")
        trace["samples"][0]["sample"] = None
        self.assertIsNone(observer_summary(trace)["available_delta_bytes"])

    def test_batch_missing_native_timing_is_not_zero_or_parent_duration(self):
        from cpu_memory_cleanup.application.verification import batch_summary
        items = [{"status": "success", "action": {"attempted": True, "call_outcome": "success"},
                  "comparison_evidence": {"business_ms": 80, "native_call_ms": None}}]
        result = batch_summary(items, selected_count=1, timing={"elapsed_ms": 100})
        self.assertIsNone(result["native_call_ms"])
        self.assertEqual(result["native_timed_count"], 0)
        self.assertEqual(result["batch_wall_ms"], 100)

    def test_unresolved_batch_items_cannot_be_reported_as_zero_native_time(self):
        from cpu_memory_cleanup.application.verification import batch_summary
        result = batch_summary([], selected_count=1)
        self.assertEqual(result["unresolved_items_count"], 1)
        self.assertIsNone(result["native_call_ms"])
    def test_reused_pid_is_appeared_and_disappeared_not_a_reduction(self):
        a = snapshot([(1, "100", 800), (2, "200", 500)])
        b = snapshot([(1, "101", 10), (2, "200", 300)])
        result = working_set_changes(a, b)
        self.assertEqual(result["matched"], 1)
        self.assertEqual(result["items"][0]["delta_bytes"], -200)
        self.assertEqual(result["appeared"], [{"pid": 1, "created_at": "101"}])
        self.assertEqual(result["disappeared"], [{"pid": 1, "created_at": "100"}])
        self.assertIsNone(result["system_released_bytes"])
        self.assertIsNone(result["all_eligible_pages_trimmed"])

    def test_partial_coverage_preserves_unchanged_and_increased_processes(self):
        result = working_set_changes(snapshot([(1, "100", 20), (2, "200", 30)], ["one inaccessible"]),
                                     snapshot([(1, "100", 20), (2, "200", 40)]))
        self.assertEqual(result["coverage"], "partial")
        self.assertEqual([r["status"] for r in result["items"]], ["unchanged", "increased"])
        self.assertEqual(result["errors"], ["one inaccessible"])

    def test_drop_then_rebound_preserves_window_resolution_and_unknown_significance(self):
        rows = [sample(0, 40), sample(1, 55), sample(2, 70), sample(3, 60)]
        header = {"started_at": rows[0].timestamp, "post_observation_seconds": 3}
        result = memory_timeline(rows, header)
        self.assertEqual(result["available_delta_bytes"], 20)
        self.assertEqual(result["peak_observed_available_bytes"], 70)
        self.assertEqual(result["rebound_used_bytes"], 10)
        self.assertEqual(result["first_observed_gain_delay_ms"], 1000)
        self.assertEqual(result["sample_interval_ms"], {"min": 1000, "max": 1000})
        self.assertIsNone(result["first_significant_gain_at"])
        self.assertFalse(result["exact_drop_time_known"])
        self.assertEqual(memory_timeline(rows, header, threshold_bytes=25)["first_significant_gain_at"], rows[2].timestamp)

    def test_two_legacy_snapshots_do_not_invent_native_timing_or_full_process_coverage(self):
        rows = [sample(0, 40), sample(6, 60)]
        value = diagnostics(rows, {"run_id": "r", "started_at": rows[0].timestamp}, source="legacy_replay")
        self.assertIsNone(value["timing"])
        self.assertIsNone(value["verification"]["native_call_ms"])
        self.assertEqual(value["verification"]["working_sets"]["coverage"], "not_observed")
        self.assertEqual(value["verification"]["timeline"]["sample_interval_ms"]["max"], 6000)
        self.assertEqual(value["verification"]["performance_verdict"], "not_evaluated")

    def test_bad_time_order_does_not_report_a_valid_drop_delay(self):
        value = memory_timeline([sample(2, 40), sample(1, 60)], {"started_at": sample(0, 1).timestamp})
        self.assertFalse(value["time_order_valid"])
        self.assertIsNone(value["first_observed_gain_delay_ms"])
        self.assertIsNone(value["sample_interval_ms"])

    def test_attachment_checks_identity_and_nonfinite_timing(self):
        value = diagnostics([], {"run_id": "r"})
        with self.assertRaises(ValueError):
            validate_attachment(value, "other")
        value["timing"] = {"unit": "ms", "basis": "run_monotonic", "stages": [], "events": [], "errors": [], "elapsed_ms": float("nan")}
        with self.assertRaises(ValueError):
            validate_attachment(value, "r")

    def test_repeated_process_identity_is_rejected(self):
        rows = snapshot([(1, "100", 20), (1, "100", 10)])
        with self.assertRaises(ValueError):
            working_set_changes(rows, copy.deepcopy(rows))
