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


if __name__ == "__main__":
    unittest.main()
