"""只使用合成样本验证规则，不采样、不创建界面、不执行维护。"""

from copy import deepcopy
import math
import unittest

from cpu_memory_cleanup.contracts import Sample
from cpu_memory_cleanup.application import rules

GIB = 1024 ** 3


class RankingTests(unittest.TestCase):
    def test_top_limits_ranking_without_discarding_cpu_input(self):
        processes = [{"pid": i, "created_at": str(i), "working_set_bytes": i * 100,
                      "name": str(i), "cpu_seconds": i} for i in range(5)]
        sample = _sample(0, processes=processes)
        ranking = rules.analyze([sample], conditions={"top": 2})["process_ranking"]
        self.assertEqual([p["pid"] for p in ranking["ranked"]], [4, 3])
        self.assertEqual(len(sample.processes), 5)
        self.assertEqual(ranking["total_readable"], 5)

    def test_boolean_top_is_rejected(self):
        with self.assertRaises(ValueError):
            rules.analyze([_sample(0)], conditions={"top": True})


def _sample(index, *, run="r", available=8, commit=60, standby=5, reads=1,
            idle_step=3, scope="system", coverage="complete", logical=4, processes=None):
    return Sample(run, index, f"2026-10-21T00:00:{index:02d}+00:00", {
        "physical": {"available_bytes": available * GIB},
        "commit": {"used_bytes": commit * GIB, "limit_bytes": 100 * GIB},
        "cache": {"standby_core_bytes": 0, "standby_normal_bytes": standby * GIB,
                  "standby_reserve_bytes": 0},
        "rates": {"page_reads_per_sec": reads, "pages_input_per_sec": 10,
                  "disk_read_bytes_per_sec": 1e6, "disk_write_bytes_per_sec": 1e6},
        "cpu": {"total_seconds": 100 + index * 4, "idle_seconds": 80 + index * idle_step,
                "logical_processors": logical, "processor_group": 0, "scope": scope, "coverage": coverage},
    }, processes=processes or [])


def _window(run="a", **kwargs):
    return [_sample(index, run=run, **kwargs) for index in range(3)]


def _metadata(*, action=False, conditions=None):
    return {"header": {"schema_version": 1, "program_version": "1", "rule_version": "1", "build_id": "synthetic-build",
                       "environment_id": "synthetic", "workload_id": "fixed-load",
                       "experiment_id": "experiment", "expected_count": 3, "interval_s": 1,
                       "conditions": conditions or {}, "pre_registered": True,
                       "mechanism": "working-set"},
            "terminal": {"completion": "success", "sample_count": 3, "recovery_complete": True},
            "action": {"attempted": action, "call_outcome": "success" if action else "not_started",
                       "privilege_restored": True, "recording_complete": True},
            "application": {"metric": "throughput", "unit": "tasks/sec", "direction": "higher", "value": 10}}


def _null_pair(**kwargs):
    return {"a": _window("null-a", **kwargs), "b": _window("null-b", **kwargs),
            "metadata_a": _metadata(), "metadata_b": _metadata()}


def _compare(a=None, b=None, *, metadata_a=None, metadata_b=None, null_pairs=None, conditions=None):
    return rules.compare(_window("a") if a is None else a,
                         _window("b", available=10) if b is None else b,
                         conditions=conditions,
                         metadata_a=_metadata() if metadata_a is None else metadata_a,
                         metadata_b=_metadata(action=True) if metadata_b is None else metadata_b,
                         null_pairs=[_null_pair()] if null_pairs is None else null_pairs)


def _tree(index, *, run="tree", free=10, used=50, limit=100, processes=600,
          private=4, gt3=None, residual=None, owners=None):
    record = {"kind": "tree-series", "run_id": run, "index": index, "count": 3, "interval_s": 60,
              "local": f"2026-10-21 00:{index:02d}:00", "processes": processes,
              "host": {"free_gib": free, "commit_used_gib": used, "commit_limit_gib": limit},
              "born_since_prev": 2, "exited_since_prev": 1,
              "codex_tree": {"processes": 200, "private_gib": private, "server_mjs": 10,
                             "server_mjs_private_gib": 1, "born_since_prev": 1, "exited_since_prev": 3},
              "top_owners": [{"owner": name, "private_gib": amount}
                             for name, amount in (owners or {}).items()]}
    if gt3 is not None:
        record["codex_tree"]["age_buckets"] = [{"kind": "server_mjs", "oldest_minutes": 100 + index,
                                               "gt3h": {"processes": gt3, "private_gib": gt3 * 0.07}}]
    if residual is not None:
        record["commit_residual"] = {"residual_gib": residual, "residual_pct_of_commit": residual / 60 * 100}
    return record


class StatisticsTests(unittest.TestCase):
    def test_interpolated_percentile(self):
        self.assertAlmostEqual(rules.describe([None, 1, 2, 3, 4])["p90"], 3.7)

    def test_missing_is_counted(self):
        self.assertEqual(rules.describe([None, 1])["missing"], 1)

    def test_all_missing_median_stays_unknown(self):
        self.assertIsNone(rules.describe([None])["median"])

    def test_boolean_is_not_a_number(self):
        self.assertEqual(rules.describe([True, False])["count"], 0)

    def test_nan_is_not_a_measurement(self):
        self.assertEqual(rules.describe([math.nan])["count"], 0)

    def test_infinity_is_not_a_measurement(self):
        self.assertEqual(rules.describe([math.inf])["count"], 0)

    def test_negative_capacity_is_unknown(self):
        self.assertIsNone(rules.metric_values({"physical": {"available_bytes": -1}})["available_bytes"])

    def test_partial_standby_is_not_zero(self):
        self.assertIsNone(rules.metric_values({"cache": {"standby_core_bytes": 1}})["standby_total_bytes"])

    def test_boolean_standby_is_unknown(self):
        self.assertIsNone(rules.metric_values({"cache": {"standby_core_bytes": True,
                                                       "standby_normal_bytes": 2,
                                                       "standby_reserve_bytes": 3}})["standby_total_bytes"])

    def test_bad_nested_shape_does_not_crash(self):
        self.assertIsNone(rules.metric_values({"rates": [1]})["page_reads_per_sec"])

    def test_gib_conversion_overflow_stays_unknown(self):
        self.assertIsNone(rules.metric_values({"host": {"free_gib": 1e308}})["available_bytes"])

    def test_transition_keeps_threshold_equality(self):
        self.assertEqual(len(rules.detect_transitions([_sample(0), _sample(1, available=9)])), 1)

    def test_transition_does_not_bridge_missing_sequence(self):
        self.assertEqual(rules.detect_transitions([_sample(0), _sample(2, available=20)]), [])

    def test_transition_does_not_bridge_runs(self):
        self.assertEqual(rules.detect_transitions([_sample(0, run="a"), _sample(1, run="b", available=20)]), [])

    def test_largest_same_name_instance_is_not_summed(self):
        sample = _sample(0, processes=[{"name": "app", "working_set_bytes": 3 * GIB},
                                       {"name": "app", "working_set_bytes": 2 * GIB}])
        self.assertEqual(rules.summarize_process_owners([sample])["owners"][0]["largest_instance_max_bytes"], 3 * GIB)

    def test_warnings_remain_countable(self):
        sample = _sample(0)
        sample.errors = ["Previous interval overrun"]
        self.assertEqual(rules.summarize_records([sample])["overrun_count"], 1)

    def test_analysis_is_read_only(self):
        self.assertFalse(rules.analyze(_window())["cleanup_performed"])

    def test_analysis_does_not_mutate_inputs(self):
        samples = _window()
        before = deepcopy(samples)
        rules.analyze(samples)
        self.assertEqual(samples, before)

    def test_boolean_conditions_are_rejected(self):
        with self.assertRaises(ValueError):
            rules.analyze([], conditions=False)


class CpuTests(unittest.TestCase):
    def test_first_sample_has_no_rate(self):
        self.assertIsNone(rules.cpu_intervals(_window())[0]["busy_pct"])

    def test_total_includes_idle(self):
        self.assertEqual(rules.cpu_intervals(_window())[1]["system_busy_pct"], 25)

    def test_partial_coverage_is_not_global_cpu(self):
        self.assertIsNone(rules.cpu_intervals(_window(coverage="partial"))[1]["busy_pct"])

    def test_group_percentage_is_not_global(self):
        self.assertIsNone(rules.cpu_intervals(_window(scope="group"))[1]["system_busy_pct"])

    def test_group_percentage_is_labeled(self):
        self.assertEqual(rules.cpu_intervals(_window(scope="group"))[1]["group_busy_pct"], 25)

    def test_equal_timestamps_are_not_an_interval(self):
        samples = _window()
        samples[1].timestamp = samples[0].timestamp
        self.assertIsNone(rules.cpu_intervals(samples)[1]["busy_pct"])

    def test_counter_regression_is_unknown(self):
        samples = _window()
        samples[1].metrics["cpu"]["total_seconds"] = 90
        self.assertIsNone(rules.cpu_intervals(samples)[1]["busy_pct"])

    def test_idle_delta_cannot_exceed_total(self):
        samples = _window(idle_step=6)
        self.assertIsNone(rules.cpu_intervals(samples)[1]["busy_pct"])

    def test_processor_group_change_invalidates_interval(self):
        samples = _window()
        samples[1].metrics["cpu"]["processor_group"] = 1
        self.assertIsNone(rules.cpu_intervals(samples)[1]["busy_pct"])

    def test_process_can_consume_more_than_one_core(self):
        samples = [_sample(i, processes=[{"pid": 10, "created_at": "instance", "cpu_seconds": i * 2}]) for i in range(2)]
        self.assertEqual(rules.cpu_intervals(samples)[1]["processes"][0]["single_core_pct"], 200)

    def test_process_normalization_uses_logical_processor_count(self):
        samples = [_sample(i, processes=[{"pid": 10, "created_at": "instance", "cpu_seconds": i * 2}]) for i in range(2)]
        self.assertEqual(rules.cpu_intervals(samples)[1]["processes"][0]["system_normalized_pct"], 50)

    def test_pid_reuse_is_not_same_process(self):
        samples = [_sample(i, processes=[{"pid": 10, "created_at": str(i), "cpu_seconds": i * 2}]) for i in range(2)]
        self.assertIsNone(rules.cpu_intervals(samples)[1]["processes"][0]["single_core_pct"])

    def test_unknown_coverage_disables_process_normalization(self):
        samples = [_sample(i, coverage="unknown", processes=[{"pid": 10, "created_at": "instance", "cpu_seconds": i}]) for i in range(2)]
        self.assertIsNone(rules.cpu_intervals(samples)[1]["processes"][0]["system_normalized_pct"])

    def test_process_counter_regression_is_unknown(self):
        samples = [_sample(i, processes=[{"pid": 10, "created_at": "instance", "cpu_seconds": 5 - i}]) for i in range(2)]
        self.assertIsNone(rules.cpu_intervals(samples)[1]["processes"][0]["single_core_pct"])


class PressureAndTreeTests(unittest.TestCase):
    def test_pressure_streak_requires_sustain(self):
        self.assertEqual(rules.evaluate([_tree(0, free=2), _tree(1, free=2)], sustain=3)["runs"][0]["episodes"], [])

    def test_pressure_episode_closes_at_end(self):
        self.assertEqual(rules.evaluate([_tree(0), _tree(1, used=90), _tree(2, used=95)], sustain=2)["runs"][0]["episodes"][0]["max_commit_pct"], 95)

    def test_pressure_does_not_bridge_missing_sample(self):
        self.assertEqual(rules.evaluate([_tree(0, free=2), _tree(2, free=2)], sustain=2)["runs"][0]["episodes"], [])

    def test_missing_host_is_not_zero(self):
        record = _tree(0)
        record["host"] = {}
        self.assertIsNone(rules.evaluate([record], sustain=1)["runs"][0]["min_free_gib"])

    def test_boolean_sustain_is_rejected(self):
        with self.assertRaises(ValueError):
            rules.evaluate([], sustain=True)

    def test_nonfinite_threshold_is_rejected(self):
        with self.assertRaises(ValueError):
            rules.evaluate([], free_below_gib=math.nan)

    def test_owner_growth_uses_overlap_only(self):
        before = rules.sample_view(_tree(0, owners={"A": 1}))
        after = rules.sample_view(_tree(1, owners={"A": 3, "B": 5}))
        self.assertEqual(rules.owner_growth(before, after)["grown"], [{"owner": "A", "private_gib": 3, "delta_gib": 2}])

    def test_entering_owner_is_not_growth_from_zero(self):
        before = rules.sample_view(_tree(0, owners={"A": 1}))
        after = rules.sample_view(_tree(1, owners={"B": 5}))
        self.assertEqual(rules.owner_growth(before, after)["entered_top_list"], ["B"])

    def test_age_bucket_retains_maximum(self):
        result = rules.summarize([_tree(0, gt3=0), _tree(1, gt3=4), _tree(2, gt3=8)])
        self.assertEqual(result["runs"][0]["age"]["server_mjs"]["buckets"]["gt3h"]["processes"]["max"], 8)

    def test_missing_age_is_unknown(self):
        self.assertIsNone(rules.summarize([_tree(0)])["runs"][0]["age"]["server_mjs"]["oldest_minutes"]["max"])

    def test_birth_totals_exclude_first_sample(self):
        self.assertEqual(rules.summarize([_tree(0), _tree(1), _tree(2)])["runs"][0]["born_total"], 4)

    def test_missing_birth_is_not_zero(self):
        records = [_tree(0), _tree(1)]
        records[1].pop("born_since_prev")
        self.assertIsNone(rules.summarize(records)["runs"][0]["born_total"])

    def test_residual_correlation_retains_signal(self):
        records = [_tree(i, processes=600 + i * 100, residual=9 + i / 2) for i in range(3)]
        self.assertEqual(rules.summarize(records)["runs"][0]["commit_residual"]["pearson_residual_vs_processes"], 1)

    def test_signed_residual_is_not_reclaimable_capacity(self):
        self.assertEqual(rules.summarize([_tree(0, residual=-1)])["runs"][0]["commit_residual"]["residual_gib"]["first"], -1)

    def test_extreme_signed_residual_correlation_stays_finite(self):
        records = [_tree(i, processes=i + 1, residual=value) for i, value in enumerate([-1e308, 0, 1e308])]
        self.assertEqual(rules.summarize(records)["runs"][0]["commit_residual"]["pearson_residual_vs_processes"], 1)

    def test_interleaved_runs_are_not_spliced(self):
        self.assertEqual(len(rules.summarize([_tree(0, run="a"), _tree(0, run="b"), _tree(1, run="a")])["runs"]), 3)


class ComparisonTests(unittest.TestCase):
    def test_uncalibrated_gain_is_insufficient(self):
        self.assertEqual(_compare(null_pairs=[])["verdict"], "insufficient_evidence")

    def test_missing_metadata_is_insufficient(self):
        self.assertEqual(_compare(metadata_a={})["verdict"], "insufficient_evidence")

    def test_missing_application_metric_is_insufficient(self):
        metadata = _metadata(action=True)
        metadata.pop("application")
        self.assertEqual(_compare(metadata_b=metadata)["verdict"], "insufficient_evidence")

    def test_incomplete_recovery_is_insufficient(self):
        metadata = _metadata(action=True)
        metadata["terminal"].pop("recovery_complete")
        self.assertEqual(_compare(metadata_b=metadata)["verdict"], "insufficient_evidence")

    def test_failed_action_is_insufficient(self):
        metadata = _metadata(action=True)
        metadata["action"]["call_outcome"] = "failure"
        self.assertEqual(_compare(metadata_b=metadata)["verdict"], "insufficient_evidence")

    def test_restore_failure_is_insufficient(self):
        metadata = _metadata(action=True)
        metadata["action"]["privilege_restored"] = False
        self.assertEqual(_compare(metadata_b=metadata)["verdict"], "insufficient_evidence")

    def test_mixed_runs_are_insufficient(self):
        samples = _window("b", available=10)
        samples[1].run_id = "other"
        self.assertEqual(_compare(b=samples)["verdict"], "insufficient_evidence")

    def test_missing_sample_is_insufficient(self):
        self.assertEqual(_compare(b=[_sample(0, run="b"), _sample(2, run="b")])["verdict"], "insufficient_evidence")

    def test_partial_missing_rate_is_insufficient(self):
        samples = _window("b", available=10)
        samples[1].metrics["rates"]["page_reads_per_sec"] = None
        self.assertEqual(_compare(b=samples)["verdict"], "insufficient_evidence")

    def test_tail_missing_value_does_not_move_window_boundary(self):
        samples = _window("b", available=10)
        samples[2].metrics["physical"]["available_bytes"] = None
        self.assertIsNone(_compare(b=samples)["B"]["metrics"]["available_bytes"]["tail_median"])

    def test_unknown_cpu_coverage_is_insufficient(self):
        self.assertEqual(_compare(b=_window("b", available=10, coverage="unknown"))["verdict"], "insufficient_evidence")

    def test_task_slowdown_overrides_memory_gain(self):
        metadata = _metadata(action=True)
        metadata["application"]["value"] = 5
        self.assertEqual(_compare(metadata_b=metadata)["verdict"], "cosmetic-or-harmful")

    def test_refault_spike_overrides_gain(self):
        self.assertEqual(_compare(b=_window("b", available=10, reads=40))["verdict"], "cosmetic-or-harmful")

    def test_cpu_cost_overrides_memory_gain(self):
        self.assertEqual(_compare(b=_window("b", available=10, idle_step=1))["verdict"], "cosmetic-or-harmful")

    def test_complete_calibrated_gain_is_per_workload_benefit(self):
        self.assertEqual(_compare()["verdict"], "benefit-for-this-workload")

    def test_commit_drop_can_be_benefit(self):
        self.assertEqual(_compare(b=_window("b", commit=58))["verdict"], "benefit-for-this-workload")

    def test_standby_movement_alone_is_cosmetic(self):
        self.assertEqual(_compare(b=_window("b", standby=1))["verdict"], "cosmetic")

    def test_level_drift_within_calibration_is_not_benefit(self):
        pair = _null_pair()
        pair["b"] = _window("null-b", available=11)
        self.assertEqual(_compare(null_pairs=[pair])["verdict"], "cosmetic")

    def test_commit_median_drift_with_equal_tails_is_not_benefit(self):
        pair = _null_pair()
        pair["b"] = [_sample(index, run="null-b", commit=58 if index < 2 else 60)
                     for index in range(3)]
        result = _compare(b=_window("b", commit=58), null_pairs=[pair])
        self.assertEqual(result["null_calibration"]["level_noise_bytes"]["commit_used_bytes"], 2 * GIB)
        self.assertEqual(result["verdict"], "cosmetic")

    def test_calibration_same_run_is_rejected(self):
        pair = _null_pair()
        pair["b"] = _window("null-a")
        self.assertEqual(_compare(null_pairs=[pair])["verdict"], "insufficient_evidence")

    def test_calibration_build_or_schema_mismatch_is_insufficient(self):
        for key, value in (("build_id", "other-build"), ("schema_version", 2)):
            pair = _null_pair()
            pair["metadata_a"]["header"][key] = value
            pair["metadata_b"]["header"][key] = value
            self.assertEqual(_compare(null_pairs=[pair])["verdict"], "insufficient_evidence")

    def test_environment_mismatch_is_insufficient(self):
        metadata = _metadata(action=True)
        metadata["header"]["environment_id"] = "other"
        self.assertEqual(_compare(metadata_b=metadata)["verdict"], "insufficient_evidence")

    def test_empty_windows_are_insufficient(self):
        self.assertEqual(_compare(a=[], b=[])["verdict"], "insufficient_evidence")

    def test_observed_delta_survives_insufficient_evidence(self):
        self.assertEqual(_compare(metadata_b={})["deltas"]["available_tail_median_bytes"], 2 * GIB)

    def test_comparison_never_reports_its_own_cleanup(self):
        self.assertFalse(_compare()["cleanup_performed"])

    def test_boolean_comparison_threshold_is_rejected(self):
        with self.assertRaises(ValueError):
            rules.compare([], [], conditions={"gain_gib": True})

    def test_capacity_threshold_overflow_is_rejected(self):
        with self.assertRaises(ValueError):
            rules.compare([], [], conditions={"gain_gib": 1e308})

    def test_rate_threshold_overflow_is_evidence_gap(self):
        samples = _window("b", reads=1e308)
        result = _compare(a=_window("a", reads=1e308), b=samples,
                          conditions={"spike_factor": 1e308})
        self.assertIsNone(result["refault"]["page_reads_per_sec"])


if __name__ == "__main__":
    unittest.main()
