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
