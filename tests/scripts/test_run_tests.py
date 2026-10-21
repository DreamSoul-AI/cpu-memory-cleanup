"""测试入口自检；只启动自身创建的合成测试工作单元。"""

from contextlib import redirect_stderr, redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

_SPEC = importlib.util.spec_from_file_location("project_test_runner", Path(__file__).resolve().parents[2] / "scripts" / "run-tests.py")
runner = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(runner)

MIB = 1024 ** 2


def _entry(method="test_pass", **updates):
    result = {"test_id": method, "unittest_id": "tests.fixture_cases.Cases." + method,
              "file": "tests/fixture_cases.py", "target": "product.py:function",
              "level": "unit", "type": "content", "priority": "p1", "cost_class": "c1",
              "result_type": "categorical", "result_detail": "summary", "tags": ["offline"],
              "resources": [], "timeout_s": 1, "memory_bytes": 256 * MIB,
              "quality_assertion": True, "cost_estimate": {"estimated_wall_time_ms": 50, "estimate_source": "合成自检"}}
    result.update(updates)
    return result


def _catalog(entries=None):
    entries = [_entry()] if entries is None else entries
    ids = [entry["test_id"] for entry in entries]
    return {"schema_version": 1, "tests": entries,
            "cost_classes": {"c1": {"timeout_s": 1, "memory_bytes": 256 * MIB},
                             "c2": {"timeout_s": 60, "memory_bytes": 2 * 1024 * MIB}},
            "plans": {"quick": {"test_ids": ids, "required": ids, "batch_cost_class": "c1",
                                  "budget": {"wall_seconds": 10, "memory_bytes": 256 * MIB},
                                  "resources": [], "dependencies": [], "trigger": "self-test"}}}


_FIXTURE = '''import unittest
import time
from pathlib import Path

class Cases(unittest.TestCase):
    def test_pass(self):
        self.assertEqual(1 + 1, 2)

    def test_failure(self):
        self.assertEqual("actual", "expected")

    def test_no_assertion(self):
        pass

    def test_numeric(self):
        self.metrics = [{"name":"latency", "value":12.0, "unit":"ms", "statistic":"single",
                         "samples":1, "warmup_iterations":0,
                         "context":{"data_size":1,"measurement_scope":"synthetic"}}]

    def test_nan_numeric(self):
        self.metrics = [{"name":"latency", "value":float("nan"), "unit":"ms", "statistic":"single",
                         "samples":1, "warmup_iterations":0, "context":{"data_size":1}}]

    def test_category(self):
        self.category = "healthy"

    def test_sleep(self):
        time.sleep(5)
        self.assertTrue(True)

    def test_flaky(self):
        marker = Path(__file__).with_name("attempt-marker")
        if not marker.exists():
            marker.write_text("created by own synthetic test", encoding="utf-8")
            self.assertFalse(True)
        self.assertTrue(True)

    @unittest.skip("tracked test condition")
    def test_skip(self):
        self.assertTrue(True)

    @unittest.expectedFailure
    def test_expected_failure(self):
        self.assertEqual(1, 2)

    @unittest.expectedFailure
    def test_unexpected_success(self):
        self.assertTrue(True)

    def test_subtests(self):
        for value in (1,2):
            with self.subTest(value=value):
                self.assertGreater(value, 0)

    def test_src_import(self):
        from synthetic_src_product import VALUE
        self.assertEqual(VALUE, "from src")

class SetupFailure(unittest.TestCase):
    def setUp(self):
        raise RuntimeError("setup failed")
    def test_case(self):
        self.assertTrue(True)

class DoubleFailure(unittest.TestCase):
    def test_case(self):
        self.assertEqual("actual", "expected")
    def tearDown(self):
        raise RuntimeError("teardown failed")

class ClassCleanupFailure(unittest.TestCase):
    def test_case(self):
        self.assertTrue(True)
    @classmethod
    def tearDownClass(cls):
        raise RuntimeError("class cleanup failed")
'''


class _Project(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "tests").mkdir()
        (self.root / "tests" / "__init__.py").write_text("", encoding="utf-8")
        (self.root / "tests" / "fixture_cases.py").write_text(_FIXTURE, encoding="utf-8")

    def execute(self, entry=None, timeout=1):
        return runner._execute(_entry() if entry is None else entry, self.root, timeout, 256 * MIB)

    def invoke(self, catalog=None, arguments=None):
        manifest = self.root / "tests" / "manifest.json"
        manifest.write_text(json.dumps(_catalog() if catalog is None else catalog), encoding="utf-8")
        output = io.StringIO()
        with redirect_stdout(output), redirect_stderr(io.StringIO()):
            code = runner.main(["--manifest", str(manifest), *(arguments or [])], root=self.root)
        return code, output.getvalue()


class CatalogTests(unittest.TestCase):
    def test_readonly_acceptance_remains_planned_outside_executable_plans(self):
        catalog = runner.validate_catalog(runner._read_json(runner.ROOT / "tests" / "manifest.json"))
        acceptance = catalog["acceptance_plans"]["readonly_diagnostics"]
        self.assertFalse(acceptance["enabled"])
        self.assertEqual(acceptance["status"], "planned_not_executed")
        self.assertNotIn(acceptance["plan_id"], catalog["plans"])
        with self.assertRaises(ValueError):
            runner.select_tests(catalog, runner.parser().parse_args(["--plan", acceptance["plan_id"]]))
        identities = {entry["test_id"] for entry in catalog["tests"]}
        for check in acceptance["checks"]:
            self.assertEqual(check["status"], "not_run")
            self.assertNotIn(check["check_id"], identities)
            self.assertTrue(set(check["offline_test_ids"]) <= identities)
        _, selected, _ = runner.select_tests(catalog, runner.parser().parse_args(["--all"]))
        self.assertTrue(selected)
        self.assertFalse(any(entry["resources"] for entry in selected))

    def test_defaults_are_resolved_before_selection(self):
        entry = _entry()
        catalog = _catalog()
        catalog["defaults"] = {key: value for key, value in entry.items() if key not in ("test_id", "unittest_id", "file", "target")}
        catalog["file_defaults"] = {entry["file"]: {"target": "product.py:function", "priority": "p2"}}
        catalog["tests"] = [{"test_id": entry["test_id"], "unittest_id": entry["unittest_id"],
                             "file": entry["file"], "priority": "p1"}]
        self.assertEqual(runner.validate_catalog(catalog)["tests"][0], entry)

    def test_conflicting_tag_is_rejected(self):
        with self.assertRaises(ValueError):
            runner.validate_catalog(_catalog([_entry(tags=["integration"])]))

    def test_integration_location_is_rejected(self):
        with self.assertRaises(ValueError):
            runner.validate_catalog(_catalog([_entry(level="integration", type="location")]))

    def test_numeric_summary_is_rejected(self):
        with self.assertRaises(ValueError):
            runner.validate_catalog(_catalog([_entry(result_type="numeric")]))

    def test_cost_budget_cannot_be_silently_expanded(self):
        with self.assertRaises(ValueError):
            runner.validate_catalog(_catalog([_entry(timeout_s=2)]))

    def test_duplicate_identity_is_rejected(self):
        with self.assertRaises(ValueError):
            runner.validate_catalog(_catalog([_entry(), _entry()]))

    def test_target_and_tags_combine(self):
        catalog = _catalog([_entry(), _entry("test_failure", target="other.py:target")])
        args = runner.parser().parse_args(["--target", "product.py", "--tag", "offline", "--level", "unit"])
        self.assertEqual([entry["test_id"] for entry in runner.select_tests(catalog, args)[1]], ["test_pass"])

    def test_resource_selector_is_not_an_authorization(self):
        catalog = _catalog([_entry(resources=["network"])])
        args = runner.parser().parse_args(["--resource", "network"])
        self.assertIsNone(args.allow_resource)

    def test_all_does_not_drop_required_plan_checks(self):
        catalog = _catalog([_entry(), _entry("test_failure")])
        args = runner.parser().parse_args(["--all", "--method", "test_pass"])
        self.assertIn("test_failure", runner.select_tests(catalog, args)[0]["required"])

    def test_dependency_cycle_is_rejected(self):
        catalog = _catalog([_entry(depends_on=["test_failure"]), _entry("test_failure", depends_on=["test_pass"])])
        with self.assertRaises(ValueError):
            runner.select_tests(catalog, runner.parser().parse_args([]))

    def test_plan_stages_precede_downstream_priority(self):
        catalog = _catalog([_entry("test_failure", priority="p2"), _entry()])
        catalog["plans"]["quick"]["test_ids"] = ["test_failure"]
        catalog["plans"]["quick"]["required"] = ["test_failure"]
        catalog["plans"]["regression"] = {**catalog["plans"]["quick"], "test_ids": ["test_pass"],
                                          "required": ["test_pass"], "dependencies": ["quick"]}
        _, entries, _ = runner.select_tests(catalog, runner.parser().parse_args(["--plan", "regression"]))
        self.assertEqual([entry["test_id"] for entry in entries], ["test_failure", "test_pass"])


class WorkerTests(_Project):
    def test_worker_imports_synthetic_product_from_src(self):
        (self.root / "src").mkdir()
        (self.root / "src" / "synthetic_src_product.py").write_text('VALUE = "from src"\n', encoding="utf-8")
        (self.root / "synthetic_src_product.py").write_text('VALUE = "from root"\n', encoding="utf-8")
        self.assertEqual(self.execute(_entry("test_src_import"))["status"], "passed")

    def test_real_synthetic_worker_reports_assertion_pass(self):
        self.assertEqual(self.execute()["status"], "passed")

    def test_noop_is_not_quality_pass(self):
        self.assertEqual(self.execute(_entry("test_no_assertion"))["status"], "error")

    def test_numeric_completion_is_measured(self):
        self.assertEqual(self.execute(_entry("test_numeric", result_type="numeric", result_detail="metrics", quality_assertion=False))["status"], "measured")

    def test_numeric_completion_has_no_quality_verdict(self):
        self.assertIsNone(self.execute(_entry("test_numeric", result_type="numeric", result_detail="metrics", quality_assertion=False))["verdict"])

    def test_nonfinite_measurement_is_error(self):
        self.assertEqual(self.execute(_entry("test_nan_numeric", result_type="numeric", result_detail="metrics", quality_assertion=False))["status"], "error")

    def test_unmapped_classification_is_completed(self):
        self.assertEqual(self.execute(_entry("test_category", quality_assertion=False, category_rule="synthetic classifier"))["status"], "completed")

    def test_mapped_classification_uses_declared_rule(self):
        entry = _entry("test_category", quality_assertion=False, category_rule="synthetic classifier",
                       quality_mapping={"healthy": "pass"}, quality_mapping_source="fixed contract")
        self.assertEqual(self.execute(entry)["verdict"], "pass")

    def test_setup_failure_is_execution_error(self):
        self.assertEqual(self.execute(_entry(unittest_id="tests.fixture_cases.SetupFailure.test_case"))["status"], "error")

    def test_teardown_error_retains_original_failure(self):
        self.assertEqual(len(self.execute(_entry(unittest_id="tests.fixture_cases.DoubleFailure.test_case"))["failure"]), 2)

    def test_class_cleanup_error_blocks_success(self):
        self.assertEqual(self.execute(_entry(unittest_id="tests.fixture_cases.ClassCleanupFailure.test_case"))["status"], "error")

    def test_skip_preserves_reason(self):
        self.assertEqual(self.execute(_entry("test_skip"))["failure"][0]["message"], "tracked test condition")

    def test_known_expected_failure_stays_xfail(self):
        self.assertEqual(self.execute(_entry("test_expected_failure", known_defect="synthetic-1"))["status"], "xfail")

    def test_unexpected_success_is_not_pass(self):
        self.assertEqual(self.execute(_entry("test_unexpected_success", known_defect="synthetic-1"))["status"], "xpass")

    def test_undeclared_parameter_cases_are_error(self):
        self.assertEqual(self.execute(_entry("test_subtests"))["status"], "error")

    def test_declared_parameters_remain_individually_visible(self):
        entry = _entry("test_subtests", subtests=[{"value": 1}, {"value": 2}])
        self.assertEqual(len(self.execute(entry)["subtests"]), 2)

    def test_deadline_terminates_only_own_worker(self):
        self.assertEqual(self.execute(_entry("test_sleep"), timeout=0.2)["status"], "timeout")

    def test_windows_job_reports_own_committed_memory(self):
        result = self.execute()
        if runner.os.name != "nt":
            self.skipTest("Windows Job 合同")
        self.assertGreater(result["peak_memory_bytes"], 0)

    def test_worker_cleanup_overrun_is_timeout_and_keeps_evidence(self):
        if runner.os.name != "nt":
            self.skipTest("Windows Job 合同")
        clock = [0.0]
        original_failure = {"phase": "test", "type": "AssertionError", "message": "synthetic failure"}
        measured = [{"name": "latency", "value": 12}]
        observed = {"status": "failed", "verdict": "fail", "category": "fail", "failure": [original_failure],
                    "metrics": measured, "subtests": [], "assertion_count": 1}
        process = Mock(returncode=0)
        process.communicate.return_value = json.dumps(observed), ""
        process.poll.return_value = 0
        job = Mock()
        job.peak.return_value = 1
        job.close.side_effect = lambda: clock.__setitem__(0, 2.0)
        with patch.object(runner, "_WindowsJob", return_value=job), \
             patch.object(runner.subprocess, "Popen", return_value=process), \
             patch.object(runner.time, "monotonic", side_effect=lambda: clock[0]):
            result = self.execute(timeout=1)
        self.assertEqual(result["status"], "timeout")
        self.assertIsNone(result["verdict"])
        self.assertEqual(result["duration_ms"], 2000)
        self.assertEqual(result["assertion_count"], 1)
        self.assertEqual(result["metrics"], measured)
        self.assertEqual(result["failure"][0], original_failure)
        self.assertEqual(result["failure"][-1]["type"], "Timeout")


class PersistenceTests(_Project):
    def test_src_source_change_invalidates_resume(self):
        directory = self.root / "result"
        (self.root / "src").mkdir()
        source = self.root / "src" / "synthetic_src_product.py"
        source.write_text("VALUE = 1\n", encoding="utf-8")
        self.invoke(arguments=["--output", str(directory)])
        source.write_text("VALUE = 2\n", encoding="utf-8")
        code, _ = self.invoke(arguments=["--resume", str(directory)])
        self.assertEqual(code, 2)

    def test_pyproject_change_invalidates_resume(self):
        directory = self.root / "result"
        config = self.root / "pyproject.toml"
        config.write_text('[project]\nname = "synthetic-v1"\n', encoding="utf-8")
        self.invoke(arguments=["--output", str(directory)])
        config.write_text('[project]\nname = "synthetic-v2"\n', encoding="utf-8")
        code, _ = self.invoke(arguments=["--resume", str(directory)])
        self.assertEqual(code, 2)

    def test_list_never_imports_test_modules(self):
        path = self.root / "tests" / "fixture_cases.py"
        path.write_text("raise RuntimeError('module imported')\n", encoding="utf-8")
        code, _ = self.invoke(arguments=["--list"])
        self.assertEqual(code, 0)

    def test_success_has_immediate_event_file(self):
        directory = self.root / "result"
        self.invoke(arguments=["--output", str(directory)])
        self.assertEqual(json.loads((directory / "events.jsonl").read_text(encoding="utf-8"))["status"], "passed")

    def test_report_rebuild_reads_persisted_events(self):
        directory = self.root / "result"
        self.invoke(arguments=["--output", str(directory)])
        (directory / "report.md").unlink()
        self.assertTrue(runner.rebuild_report(directory)["release_allowed"])

    def test_required_filtered_out_case_blocks_release(self):
        directory = self.root / "result"
        code, _ = self.invoke(_catalog([_entry(), _entry("test_failure")]), ["--method", "test_pass", "--output", str(directory)])
        self.assertEqual(code, 1)

    def test_unavailable_resource_never_starts_test(self):
        directory = self.root / "result"
        with patch.object(runner, "_execute") as execute:
            code, _ = self.invoke(_catalog([_entry(resources=["real_os"])]), ["--output", str(directory)])
        execute.assert_not_called()
        self.assertEqual(code, 1)
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["unexecuted"]["test_pass"], "缺少已允许资源：real_os")

    def test_missing_hard_cap_blocks_before_import(self):
        directory = self.root / "result"
        with patch.object(runner, "_check_caps", side_effect=OSError("no containment")), patch.object(runner, "_execute") as execute:
            code, _ = self.invoke(arguments=["--output", str(directory)])
        execute.assert_not_called()
        self.assertEqual(code, 1)
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        self.assertIn("no containment", manifest["run_error"])

    def test_resume_keeps_failed_and_successful_attempts(self):
        directory = self.root / "result"
        catalog = _catalog([_entry("test_flaky")])
        self.invoke(catalog, ["--output", str(directory)])
        self.invoke(catalog, ["--resume", str(directory)])
        events = [json.loads(line) for line in (directory / "events.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual([event["status"] for event in events], ["failed", "passed"])

    def test_retry_success_is_flagged_as_recovered(self):
        directory = self.root / "result"
        catalog = _catalog([_entry("test_flaky")])
        self.invoke(catalog, ["--output", str(directory)])
        self.invoke(catalog, ["--resume", str(directory)])
        self.assertEqual(runner.rebuild_report(directory)["recovered"], ["test_flaky"])

    def test_result_write_failure_cannot_pass(self):
        directory = self.root / "result"
        with patch.object(runner, "_append_event", side_effect=OSError("synthetic full disk")):
            code, _ = self.invoke(arguments=["--output", str(directory)])
        self.assertNotEqual(code, 0)

    def test_numeric_timeout_keeps_numeric_signal_type(self):
        directory = self.root / "result"
        entry = _entry("test_sleep", result_type="numeric", result_detail="metrics", quality_assertion=False, timeout_s=0.2)
        self.invoke(_catalog([entry]), ["--output", str(directory)])
        self.assertEqual(json.loads((directory / "events.jsonl").read_text(encoding="utf-8"))["result_type"], "numeric")

    def test_parameter_results_have_independent_identifiers(self):
        directory = self.root / "result"
        entry = _entry("test_subtests", subtests=[{"value": 1}, {"value": 2}])
        self.invoke(_catalog([entry]), ["--output", str(directory)])
        events = [json.loads(line) for line in (directory / "events.jsonl").read_text(encoding="utf-8").splitlines()]
        cases = [event["test_id"] for event in events if event.get("record_type") != "group_result"]
        self.assertEqual(cases, ['test_subtests[{"value":1}]', 'test_subtests[{"value":2}]'])

    def test_parameter_case_count_does_not_double_count_group(self):
        directory = self.root / "result"
        entry = _entry("test_subtests", subtests=[{"value": 1}, {"value": 2}])
        self.invoke(_catalog([entry]), ["--output", str(directory)])
        self.assertEqual(runner.rebuild_report(directory)["cases"], 2)

    def test_interrupted_attempt_is_preserved_on_resume(self):
        directory = self.root / "result"
        with patch.object(runner, "_execute", side_effect=KeyboardInterrupt):
            self.invoke(arguments=["--output", str(directory)])
        self.invoke(arguments=["--resume", str(directory)])
        events = [json.loads(line) for line in (directory / "events.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual([event["attempt"] for event in events], [1, 2])

    def test_compatibility_script_change_invalidates_resume(self):
        directory = self.root / "result"
        self.invoke(arguments=["--output", str(directory)])
        (self.root / "scripts").mkdir()
        (self.root / "scripts" / "entry.cmd").write_text("@echo off\n", encoding="utf-8")
        code, _ = self.invoke(arguments=["--resume", str(directory)])
        self.assertEqual(code, 2)

    def test_failed_required_plan_dependency_prevents_downstream_worker(self):
        directory = self.root / "result"
        catalog = _catalog([_entry("test_failure"), _entry()])
        catalog["plans"]["quick"]["test_ids"] = ["test_failure"]
        catalog["plans"]["quick"]["required"] = ["test_failure"]
        catalog["plans"]["regression"] = {**catalog["plans"]["quick"], "test_ids": ["test_pass"],
                                          "required": ["test_pass"], "dependencies": ["quick"], "stop_on_failure": False}
        with patch.object(runner, "_execute", wraps=runner._execute) as execute:
            self.invoke(catalog, ["--plan", "regression", "--output", str(directory)])
        self.assertEqual(execute.call_count, 1)

    def test_last_result_persistence_overrun_blocks_batch_release(self):
        directory = self.root / "result"
        catalog = _catalog()
        catalog["plans"]["quick"]["budget"]["wall_seconds"] = 1
        clock = [0.0]
        record_result = runner._record_result

        def execute(*args):
            clock[0] = 0.5
            return {"status": "passed", "verdict": "pass", "category": "pass", "failure": None,
                    "metrics": [], "subtests": [], "duration_ms": 500, "started_at": "synthetic"}

        def record(*args):
            emitted = record_result(*args)
            clock[0] = 1.1
            return emitted

        with patch.object(runner, "_check_caps"), \
             patch.object(runner, "_source_info", return_value={"fingerprint": "synthetic", "commit": None}), \
             patch.object(runner, "_execute", side_effect=execute), \
             patch.object(runner, "_record_result", side_effect=record), \
             patch.object(runner.time, "monotonic", side_effect=lambda: clock[0]):
            code, output = self.invoke(catalog, ["--output", str(directory)])
        summary = json.loads(output)
        self.assertEqual(code, 1)
        self.assertEqual(summary["quality_passes"], 1)
        self.assertTrue(summary["budget_exceeded"])
        self.assertFalse(summary["release_allowed"])
        self.assertFalse(runner.rebuild_report(directory)["release_allowed"])
        self.assertIn("整批墙钟预算超限：True", (directory / "report.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
