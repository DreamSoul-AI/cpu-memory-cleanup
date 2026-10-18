import ast
import contextlib
import ctypes as c
import io
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from cpu_memory_cleanup import windows as api
from cpu_memory_cleanup.__main__ import main, render


class InspectionTests(unittest.TestCase):
    def test_estimate_does_not_add_available_or_working_sets(self):
        cache = dict(zip(api.STANDBY_KEYS, (10, 20, 30)))
        self.assertEqual(api.estimate(cache, 50)["standby_repurposable_upper_bytes"], 50)
        self.assertEqual(api.estimate(cache, 100)["standby_repurposable_upper_bytes"], 60)
        self.assertEqual(api.estimate(cache, 100)["guaranteed_additional_available_bytes"], 0)
        cache[api.STANDBY_KEYS[0]] = None
        self.assertIsNone(api.estimate(cache, 100)["standby_repurposable_upper_bytes"])
        self.assertEqual(api.estimate(dict.fromkeys(api.STANDBY_KEYS, 0), 100)["standby_repurposable_upper_bytes"], 0)

    def test_no_execution_arguments(self):
        for args in (["--execute"], ["--clean"], ["--top", "0"], ["--top", "-1"]):
            with self.subTest(args=args), patch("cpu_memory_cleanup.__main__.snapshot") as snap:
                with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as exc:
                    main(args)
                self.assertEqual(exc.exception.code, 2)
                snap.assert_not_called()

    def test_json_failure_is_machine_readable(self):
        output = io.StringIO()
        with patch("cpu_memory_cleanup.__main__.snapshot", side_effect=OSError("denied")):
            with contextlib.redirect_stdout(output):
                self.assertEqual(main(["--json"]), 1)
        report = json.loads(output.getvalue())
        self.assertFalse(report["cleanup_performed"])
        self.assertEqual(report["error"], "denied")

    def test_native_layout_on_windows(self):
        if api.sys.platform != "win32":
            self.skipTest("Windows ABI required")
        x64 = c.sizeof(c.c_void_p) == 8
        self.assertEqual(c.sizeof(api.PerformanceInfo), 104 if x64 else 56)
        self.assertEqual(c.sizeof(api.ProcessMemory), 72 if x64 else 40)
        self.assertEqual(c.sizeof(api.PageFileInfo), 32 if x64 else 20)
        self.assertEqual(c.sizeof(api.CounterValue), 16)

    def test_bound_api_allowlist(self):
        tree = ast.parse(Path(api.__file__).read_text(encoding="utf-8"))
        bound = {node.args[1].value for node in ast.walk(tree)
                 if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                 and node.func.id == "bind"}
        self.assertEqual(bound, {
            "GetPerformanceInfo", "EnumPageFilesW", "EnumProcesses", "OpenProcess",
            "CloseHandle", "GetProcessMemoryInfo", "QueryFullProcessImageNameW",
            "PdhOpenQueryW", "PdhAddEnglishCounterW", "PdhCollectQueryData",
            "PdhGetFormattedCounterValue", "PdhCloseQuery",
        })

    def test_process_access_denial_and_handle_cleanup(self):
        opened, closed = [], []

        def enum(ids, size, needed):
            for index, pid in enumerate((1, 2, 3, 4)):
                ids[index] = pid
            needed._obj.value = 4 * c.sizeof(api.w.DWORD)
            return True

        def open_process(access, inherit, pid):
            opened.append((access, inherit, pid))
            return None if pid == 2 else pid

        def memory(handle, stats, size):
            stats._obj.WorkingSetSize = handle * 100
            return handle != 3

        def image(handle, flags, name, length):
            name.value = "C:\\private\\python.exe"
            return True

        kernel = SimpleNamespace(OpenProcess=open_process, CloseHandle=lambda h: closed.append(h),
                                 QueryFullProcessImageNameW=image)
        psapi = SimpleNamespace(EnumProcesses=enum, GetProcessMemoryInfo=memory)
        with patch.object(api, "bind", side_effect=lambda dll, name, *args: getattr(dll, name)):
            report = api.read_processes(kernel, psapi, 1)
        self.assertTrue(all(access == 0x1000 and inherit is False for access, inherit, pid in opened))
        self.assertEqual(closed, [1, 3, 4])
        self.assertEqual(report, {"enumerated": 4, "readable": 2, "skipped": 2,
                                  "top": [{"name": "python.exe", "working_set_bytes": 400}]})

    def test_pagefile_units_and_no_pagefiles(self):
        if api.sys.platform != "win32":
            self.skipTest("Windows callback ABI required")

        def enum(callback, context):
            for total, used in ((100, 30), (200, 40)):
                info = api.PageFileInfo(TotalSize=total, TotalInUse=used)
                callback(None, c.pointer(info), "ignored")
            return True

        with patch.object(api, "bind", return_value=enum):
            self.assertEqual(api.read_pagefiles(None, 4096),
                             {"count": 2, "total_bytes": 300 * 4096, "used_bytes": 70 * 4096})
        with patch.object(api, "bind", return_value=lambda *args: True):
            self.assertEqual(api.read_pagefiles(None, 4096),
                             {"count": 0, "total_bytes": 0, "used_bytes": 0})

    def test_missing_optional_data_and_text_output(self):
        if api.sys.platform != "win32":
            self.skipTest("Windows DLL loader required")

        def info(pointer, size):
            for name, value in (("PageSize", 4096), ("PhysicalTotal", 100), ("PhysicalAvailable", 40)):
                setattr(pointer._obj, name, value)
            return True

        with patch.object(api, "bind", return_value=info), \
             patch.object(api, "read_counters", side_effect=OSError("counters denied")), \
             patch.object(api, "read_pagefiles", side_effect=OSError("pagefiles denied")), \
             patch.object(api, "read_processes", side_effect=OSError("processes denied")):
            report = api.snapshot()
        self.assertEqual(report["physical"]["used_bytes"], 60 * 4096)
        self.assertEqual(report["schema_version"], 1)
        self.assertNotIn("rates", report)
        self.assertFalse(report["cleanup_performed"])
        self.assertIsNone(report["estimate"]["standby_repurposable_upper_bytes"])
        self.assertIsNone(report["pagefile"])
        self.assertEqual(len(report["warnings"]), 3)
        text = render(report)
        self.assertIn("unavailable", text)
        self.assertIn("READ ONLY", text)

    def test_pdh_checks_counter_status_and_closes_query(self):
        if api.sys.platform != "win32":
            self.skipTest("Windows DLL loader required")
        closed = []

        def open_query(source, context, query):
            query._obj.value = 1
            return 0

        def add(query, path, context, counter):
            counter._obj.value = 2
            return 0

        def invalid_value(handle, flags, kind, value):
            value._obj.CStatus = 0xc0000bb9
            value._obj.value.largeValue = 999
            return 0

        dll = SimpleNamespace(PdhOpenQueryW=open_query, PdhAddEnglishCounterW=add,
                              PdhCollectQueryData=lambda query: 0,
                              PdhGetFormattedCounterValue=invalid_value,
                              PdhCloseQuery=lambda query: closed.append(query.value))
        warnings = []
        with patch.object(api.c, "WinDLL", return_value=dll), \
             patch.object(api, "bind", side_effect=lambda dll, name, *args: getattr(dll, name)):
            values = api.read_counters(warnings)
        self.assertTrue(all(value is None for value in values.values()))
        self.assertEqual(len(warnings), len(api.COUNTERS))
        self.assertEqual(closed, [1])

    def test_series_flags_rejected_before_inspection(self):
        rejected = (
            ["--execute"], ["--clean"], ["--sample"],
            ["--sample", "--interval", "1"],
            ["--sample", "--interval", "1", "--count", "2"],
            ["--interval", "1", "--count", "2", "--json"],
            ["--with-processes"],
            ["--sample", "--interval", "0.5", "--count", "1", "--json"],
            ["--sample", "--interval", "1", "--count", str(api.MAX_SAMPLE_COUNT + 1), "--json"],
            ["--sample", "--interval", "nan", "--count", "1", "--json"],
        )
        for args in rejected:
            with self.subTest(args=args), patch("cpu_memory_cleanup.__main__.snapshot") as snap, \
                    patch("cpu_memory_cleanup.__main__.iter_snapshots") as series:
                with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as exc:
                    main(args)
                self.assertEqual(exc.exception.code, 2)
                snap.assert_not_called()
                series.assert_not_called()

    def test_bounded_series_sleeps_between_starts_and_closes_query(self):
        sessions = []

        class FakeSession:
            def __init__(self, include_rates=False):
                self.include_rates = include_rates
                self.ready = False
                self.closed = 0
                sessions.append(self)

            def open(self, warnings):
                self.ready = True
                warnings.append("pdh opened")
                return True

            def close(self):
                self.closed += 1
                self.ready = False

        class Clock:
            def __init__(self):
                self.now = 0

            def __call__(self):
                return self.now

        clock = Clock()
        sleeps = []

        def sleeper(delay):
            sleeps.append(delay)
            clock.now += delay

        def fake_snapshot(*args, **kwargs):
            self.assertIs(kwargs["session"], sessions[0])
            self.assertFalse(kwargs["include_processes"])
            return {"schema_version": 1, "mode": "read-only", "cleanup_performed": False, "warnings": ["tick"]}

        with patch.object(api, "CounterSession", FakeSession), \
                patch.object(api, "snapshot", side_effect=fake_snapshot):
            reports = list(api.iter_snapshots(3, 5, include_processes=False, sleeper=sleeper, clock=clock))
        self.assertEqual(sleeps, [5, 5])
        self.assertEqual(sessions[0].closed, 1)
        self.assertTrue(sessions[0].include_rates)
        self.assertEqual([item["series"]["index"] for item in reports], [0, 1, 2])
        self.assertEqual(len({item["series"]["run_id"] for item in reports}), 1)
        self.assertEqual(len(reports[0]["series"]["run_id"]), 32)
        self.assertEqual(reports[0]["schema_version"], 2)
        self.assertEqual(reports[0]["warnings"], ["pdh opened", "tick"])
        self.assertEqual(reports[1]["warnings"], ["tick"])
        self.assertFalse(any(item["series"]["process_ranking"] for item in reports))

    def test_overrun_does_not_burst(self):
        class FakeSession:
            def __init__(self, include_rates=False):
                self.ready = True
                self.closed = 0

            def open(self, warnings):
                return True

            def close(self):
                self.closed += 1

        class Clock:
            def __init__(self):
                self.now = 0

            def __call__(self):
                return self.now

        clock = Clock()
        sleeps = []

        def fake_snapshot(*args, **kwargs):
            clock.now += 10
            return {"schema_version": 1, "cleanup_performed": False, "warnings": []}

        with patch.object(api, "CounterSession", FakeSession), \
                patch.object(api, "snapshot", side_effect=fake_snapshot):
            reports = list(api.iter_snapshots(2, 5, sleeper=lambda delay: sleeps.append(delay), clock=clock))
        self.assertEqual(sleeps, [])
        self.assertTrue(reports[1]["warnings"][0].startswith("Previous interval overrun"))

    def test_rate_counters_are_null_until_the_next_sample(self):
        if api.sys.platform != "win32":
            self.skipTest("Windows DLL loader required")
        closed = []
        double_reads = {"n": 0}

        def open_query(source, context, query):
            query._obj.value = 7
            return 0

        def add(query, path, context, counter):
            counter._obj.value = 3
            return 0

        def get_value(handle, flags, kind, value):
            if flags == api.PDH_FMT_DOUBLE:
                double_reads["n"] += 1
                if double_reads["n"] <= len(api.RATE_COUNTERS):
                    value._obj.CStatus = api.PDH_CSTATUS_INVALID_DATA
                    return api.PDH_INVALID_DATA
                value._obj.CStatus = 0
                value._obj.value.doubleValue = 1.5
                return 0
            value._obj.CStatus = 0
            value._obj.value.largeValue = 10
            return 0

        dll = SimpleNamespace(PdhOpenQueryW=open_query, PdhAddEnglishCounterW=add,
                              PdhCollectQueryData=lambda query: 0, PdhGetFormattedCounterValue=get_value,
                              PdhCloseQuery=lambda query: closed.append(query.value) or 0)
        warnings = []
        with patch.object(api.c, "WinDLL", return_value=dll), \
                patch.object(api, "bind", side_effect=lambda dll, name, *args: getattr(dll, name)):
            session = api.CounterSession(include_rates=True)
            self.assertTrue(session.open(warnings))
            first_capacity, first_rates = session.collect(warnings)
            second_warnings = []
            second_capacity, second_rates = session.collect(second_warnings)
            session.close()
        self.assertEqual(closed, [7])
        self.assertTrue(all(value == 10 for value in first_capacity.values()))
        self.assertTrue(all(value is None for value in first_rates.values()))
        self.assertEqual(len(warnings), len(api.RATE_COUNTERS))
        self.assertTrue(all("next sample" in warning for warning in warnings))
        self.assertEqual(second_warnings, [])
        self.assertTrue(all(value == 1.5 for value in second_rates.values()))
        self.assertTrue(all(value == 10 for value in second_capacity.values()))

    def test_jsonl_write_failure_stops_and_closes(self):
        produced = {"n": 0}
        closed = {"series": 0, "handle": 0}

        def series(*args, **kwargs):
            produced["n"] += 1
            yield {"cleanup_performed": False, "warnings": [], "series": {"index": 0, "count": 2, "interval_seconds": 1},
                   "physical": {"available_bytes": 1}, "commit": {"used_bytes": 1}, "rates": {}}
            produced["n"] += 1
            yield {"cleanup_performed": False}

        class Series:
            def __iter__(self):
                return series()

            def close(self):
                closed["series"] += 1

        class Handle:
            def write(self, data):
                raise OSError("disk full")

            def flush(self):
                pass

            def close(self):
                closed["handle"] += 1

        with patch("cpu_memory_cleanup.__main__.iter_snapshots", return_value=Series()), \
                patch("pathlib.Path.is_dir", return_value=True), \
                patch("builtins.open", return_value=Handle()):
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                code = main(["--sample", "--interval", "1", "--count", "2", "--jsonl", "logs/series.jsonl"])
        self.assertEqual(code, 1)
        self.assertIn("disk full", err.getvalue())
        self.assertIn("after 0 records", err.getvalue())
        self.assertEqual(produced["n"], 1)
        self.assertEqual(closed, {"series": 1, "handle": 1})

    def test_missing_jsonl_directory_does_not_sample(self):
        with patch("cpu_memory_cleanup.__main__.iter_snapshots") as series:
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                code = main(["--sample", "--interval", "1", "--count", "1",
                             "--jsonl", "does-not-exist-sample-dir/out.jsonl"])
        self.assertEqual(code, 1)
        self.assertIn("does not exist", err.getvalue())
        series.assert_not_called()


if __name__ == "__main__":
    unittest.main()
