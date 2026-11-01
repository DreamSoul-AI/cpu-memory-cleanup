"""CLI 参数与旅程验证；所有业务接口为假实现。"""

import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from tests.fixtures import create_application

from cpu_memory_cleanup.interaction import cli
from cpu_memory_cleanup.contracts import RunReport, Sample


class FakeApplication:
    def __init__(self, completion="success"):
        self.calls = []
        self.completion = completion

    def _report(self, mode, *args):
        self.calls.append((mode, *args))
        return RunReport("fake-run", mode, completion=self.completion)

    def inspect(self, options):
        return self._report("inspect", options)

    def review(self, path, comparison=None):
        return self._report("review", path, comparison)

    def maintain(self, request, directory):
        return self._report("maintain", request, directory)

    def memory_status(self):
        return self._report("inspect")

    def memory_cleanup(self, directory, **confirmation):
        return self._report("maintain", directory, confirmation)

    def memory_result(self, directory):
        return self._report("review", directory)


class CliTests(unittest.TestCase):
    def execute(self, argv, app=None):
        app = app or FakeApplication()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = cli.run(app, cli.parse_args(argv))
        return app, code, output.getvalue()

    def rejected(self, argv):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
            cli.parse_args(argv)
        self.assertEqual(caught.exception.code, 2)

    def test_default_only_requests_one_readonly_sample(self):
        app, code, text = self.execute([])
        self.assertEqual(code, 0)
        self.assertEqual(app.calls, [("inspect", {"count": 1, "interval": 1, "with_processes": False, "top": 10})])
        self.assertIn("原生维护调用：False", text)

    def test_text_report_preserves_sample_gap_reasons(self):
        report = RunReport("fake-run", "inspect", completion="success", samples=[
            Sample("fake-run", 0, None, {}, errors=["PDH warmup"]),
            Sample("fake-run", 1, None, {}, errors=["counter unavailable"]),
        ])
        app = FakeApplication()
        app.inspect = lambda _: report
        _, code, text = self.execute([], app)
        self.assertEqual(code, 0)
        self.assertIn("PDH warmup", text)
        self.assertIn("counter unavailable", text)
        self.assertIn('"sequence": 1', text)

    def test_explicit_release_passes_confirmation_and_directory(self):
        app, code, _ = self.execute(["--release", "--release-what", "standby", "--output-dir", "中文运行"])
        _, request, directory = app.calls[0]
        self.assertEqual(code, 0)
        self.assertEqual(request.mechanism, "standby")
        self.assertTrue(request.confirmed)
        self.assertEqual(request.confirmation_source, "cli:--release")
        self.assertEqual(directory, Path("中文运行"))

    def test_both_is_rejected(self):
        self.rejected(["--release", "--release-what", "both", "--output-dir", "out"])

    def test_release_requires_output_directory(self):
        self.rejected(["--release", "--release-what", "working-set"])

    def test_release_requires_explicit_mechanism(self):
        self.rejected(["--release", "--output-dir", "out"])

    def test_release_cannot_mix_with_sampling(self):
        self.rejected(["--release", "--sample"])

    def test_sample_forwards_bounded_options(self):
        app, code, _ = self.execute(["--sample", "--count", "2", "--interval", "1", "--jsonl", "数据.jsonl", "--with-processes", "--top", "4"])
        self.assertEqual(code, 0)
        self.assertEqual(app.calls[0], ("inspect", {"count": 2, "interval": 1.0, "with_processes": True, "top": 4, "jsonl_path": Path("数据.jsonl")}))

    def test_sample_rejects_nonfinite_interval(self):
        self.rejected(["--sample", "--count", "2", "--interval", "nan", "--json"])

    def test_sample_requires_retained_output(self):
        self.rejected(["--sample", "--count", "2", "--interval", "1"])

    def test_sampling_flags_require_sample_mode(self):
        self.rejected(["--count", "2"])

    def test_analyze_alias_only_calls_review(self):
        app, code, _ = self.execute(["--analyze", "历史.jsonl"])
        self.assertEqual(code, 0)
        self.assertEqual(app.calls, [("review", Path("历史.jsonl"), None)])

    def test_pressure_alias_only_calls_review(self):
        app, _, _ = self.execute(["--pressure", "历史.jsonl"])
        self.assertEqual(app.calls, [("review", Path("历史.jsonl"), None)])

    def test_tree_summary_alias_only_calls_review(self):
        app, _, _ = self.execute(["--tree-summary", "历史.jsonl"])
        self.assertEqual(app.calls, [("review", Path("历史.jsonl"), None)])

    def test_ab_compare_only_calls_review_with_comparison(self):
        app, _, _ = self.execute(["--ab-compare", "a.jsonl", "b.jsonl"])
        self.assertEqual(app.calls, [("review", Path("a.jsonl"), Path("b.jsonl"))])

    def test_old_live_protocol_is_explicitly_rejected(self):
        self.rejected(["--ab-protocol", "out", "--ab-live"])

    def test_unconnected_null_calibration_is_explicitly_rejected(self):
        self.rejected(["--ab-compare", "a", "b", "--ab-null", "n1", "n2"])

    def test_exit_code_uses_whole_report_completion(self):
        app, code, text = self.execute(["--json"], FakeApplication("partial"))
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(text)["completion"], "partial")

    def test_output_failure_does_not_claim_no_action(self):
        app = FakeApplication()
        app.maintain = lambda *_: (_ for _ in ()).throw(OSError("record write failed"))
        error = io.StringIO()
        with contextlib.redirect_stderr(error):
            _, code, _ = self.execute(["--release", "--release-what", "standby", "--output-dir", "out", "--json"], app)
        self.assertEqual(code, 1)
        self.assertNotIn("attempted", json.loads(error.getvalue()))

    def test_tool_catalog_describes_three_capabilities_without_calling_application(self):
        app, code, text = self.execute(["--tool-info"])
        catalog = json.loads(text)
        self.assertEqual(code, 0)
        self.assertEqual(catalog["tool_schema_version"], 1)
        self.assertEqual([t["name"] for t in catalog["tools"]], list(cli.TOOL_NAMES))
        self.assertTrue(catalog["tools"][1]["requires_confirmation"])
        self.assertEqual(app.calls, [])

    def test_memory_status_tool_returns_versioned_json_without_legacy_inspection(self):
        app, code, text = self.execute(["--tool", "memory_status"])
        record = json.loads(text)
        self.assertEqual(code, 0)
        self.assertEqual(app.calls, [("inspect",)])
        self.assertEqual((record["tool_schema_version"], record["tool"]), (1, "memory_status"))
        self.assertNotIn("tool", json.loads(self.execute(["--json"])[2]))

    def test_memory_cleanup_tool_forwards_explicit_confirmation(self):
        app, code, text = self.execute(["--tool", "memory_cleanup", "--confirm", "--output-dir", "中文记录"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(text)["tool"], "memory_cleanup")
        self.assertEqual(app.calls, [("maintain", Path("中文记录"), {
            "confirmed": True, "confirmation_source": "cli:--tool memory_cleanup --confirm"})])

    def test_memory_cleanup_tool_requires_confirmation_before_dispatch(self):
        self.rejected(["--tool", "memory_cleanup", "--output-dir", "out"])

    def test_memory_result_tool_only_forwards_existing_run_location(self):
        app, code, text = self.execute(["--tool", "memory_result", "--run-dir", "已有记录"])
        self.assertEqual(code, 0)
        self.assertEqual(app.calls, [("review", Path("已有记录"))])
        self.assertEqual(json.loads(text)["tool"], "memory_result")

    def test_tool_execution_error_does_not_invent_action_fact(self):
        app = FakeApplication()
        app.memory_cleanup = lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("记录返回中断"))
        _, code, text = self.execute(["--tool", "memory_cleanup", "--confirm", "--output-dir", "out"], app)
        result = json.loads(text)
        self.assertEqual(code, 1)
        self.assertEqual(result["completion"], "failed")
        self.assertEqual(result["tool"], "memory_cleanup")
        self.assertNotIn("action", result)


class FakeSession:
    def __init__(self, fail_after=False):
        self.fail_after = fail_after

    def read(self, run_id, sequence):
        if self.fail_after and sequence:
            raise OSError("after snapshot failed")
        return Sample(run_id, sequence, f"2026-10-21T00:00:0{sequence}+00:00",
                      {"physical": {"available_bytes": 8, "total_bytes": 32},
                       "commit": {"used_bytes": 16, "limit_bytes": 64}})

    def close(self):
        return {"closed": True, "errors": []}


class FakeBackend:
    def __init__(self, fail_after=False):
        self.read_calls = 0
        self.action_calls = 0
        self.fail_after = fail_after

    def open_observation(self, **options):
        self.read_calls += 1
        return FakeSession(self.fail_after)

    def open_operation(self, request):
        return self

    def preflight(self):
        return {"supported": True, "privilege_available": True, "lock_acquired": True, "scope": "system"}

    def perform_once(self):
        self.action_calls += 1
        return {"attempted": True, "call_outcome": "success", "status": 0, "privilege_restored": True}

    def close(self):
        return {"closed": True, "errors": []}


class CliJourneyTests(unittest.TestCase):
    def test_synthetic_agent_uses_catalog_status_cleanup_and_original_result(self):
        from cpu_memory_cleanup import api
        from tests.fixtures import FakeRuntime
        from ..test_api import SyntheticMemory

        backend = SyntheticMemory()
        app = api.create_application(reader=backend, maintainer=backend, runtime=FakeRuntime(),
                                     capabilities=api.Capabilities("synthetic", "available", ("working-set",)))

        def call(argv, application=app):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = cli.run(application, cli.parse_args(argv))
            self.assertEqual(code, 0, output.getvalue())
            return json.loads(output.getvalue())

        self.assertEqual([t["name"] for t in call(["--tool-info"])["tools"]], list(cli.TOOL_NAMES))
        self.assertEqual((backend.reads, backend.actions), (0, 0))
        status = call(["--tool", "memory_status"])
        self.assertEqual(status["samples"][0]["metrics"]["physical"]["available_bytes"], 2 * 1024 ** 3)
        with tempfile.TemporaryDirectory() as directory:
            cleaned = call(["--tool", "memory_cleanup", "--confirm", "--output-dir", directory])
            self.assertEqual(cleaned["action"]["call_outcome"], "success")
            self.assertEqual([s["metrics"]["physical"]["available_bytes"] for s in cleaned["samples"]],
                             [2 * 1024 ** 3, 3 * 1024 ** 3])
            app.shutdown()
            replay = call(["--tool", "memory_result", "--run-dir", cleaned["output_dir"]],
                          api.create_application(offline=True, runtime=FakeRuntime()))
        self.assertEqual(replay["action"], cleaned["action"])
        self.assertEqual(replay["metadata"]["header"]["run_id"], cleaned["run_id"])
        self.assertEqual((backend.reads, backend.actions), (3, 1))

    def test_offline_review_uses_saved_run_with_zero_backend_calls(self):
        backend = FakeBackend()
        app = create_application(backend, backend)
        with tempfile.TemporaryDirectory() as directory:
            recorded = app.inspect({"output_dir": directory})
            backend.read_calls = 0
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                result = cli.run(app, cli.parse_args(["--review", recorded.output_dir, "--json"]))
        self.assertEqual(result, 0)
        self.assertEqual(json.loads(output.getvalue())["mode"], "review")
        self.assertEqual((backend.read_calls, backend.action_calls), (0, 0))

    def test_post_read_failure_preserves_successful_action_in_cli_result(self):
        backend = FakeBackend(fail_after=True)
        app = create_application(backend, backend)
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(output):
            code = cli.run(app, cli.parse_args(["--release", "--release-what", "working-set", "--output-dir", directory, "--json"]))
        record = json.loads(output.getvalue())
        self.assertEqual(code, 1)
        self.assertEqual(record["completion"], "partial")
        self.assertTrue(record["action"]["attempted"])
        self.assertEqual(record["action"]["call_outcome"], "success")
        self.assertEqual(backend.action_calls, 1)
