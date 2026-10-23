"""离线平台合同测试：每项原生调用均由假 DLL 实现。"""

import ctypes as c
import importlib
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from cpu_memory_cleanup.system.platforms import windows as api
from cpu_memory_cleanup.contracts import ActionRequest
from tests.support import make_app


class Function:
    def __init__(self, function):
        self.function = function

    def __call__(self, *args):
        return self.function(*args)


def filetime(pointer, ticks):
    pointer._obj.dwLowDateTime = ticks & 0xffffffff
    pointer._obj.dwHighDateTime = ticks >> 32


def handle_value(handle):
    return handle.value if hasattr(handle, "value") else handle


class FakeSystem:
    def __init__(self):
        self.loads, self.closed, self.adjustments, self.nt_calls = [], [], [], []
        self.mutex_status, self.nt_status = 0, 0
        self.attrs, self.fail_enable, self.fail_restore = 0, False, False
        self.fail_release, self.fail_close_query = False, False
        self.fail_close_token = False
        self.fail_memory, self.fail_topology, self.fail_times = False, False, False
        self.nt_error, self.rate, self.collections = None, 1.5, 0
        self.groups, self.total, self.group, self.group_count = 1, 8, 0, 8
        self.counters, self.opens = {}, []
        self.kernel = self.dll(
            GetSystemTimes=self.system_times, GetActiveProcessorGroupCount=lambda: self.groups,
            GetActiveProcessorCount=lambda group: self.total if group == 0xffff else self.group_count,
            GetCurrentThread=lambda: -2, GetThreadGroupAffinity=self.affinity,
            OpenProcess=self.open_process, CloseHandle=self.close_handle,
            QueryFullProcessImageNameW=self.image, GetProcessTimes=self.process_times,
            GetCurrentProcess=lambda: -1, CreateMutexW=lambda *args: 700,
            WaitForSingleObject=lambda handle, timeout: self.mutex_status,
            ReleaseMutex=self.release_mutex)
        self.psapi = self.dll(GetPerformanceInfo=self.performance, EnumProcesses=self.enum_processes,
                              GetProcessMemoryInfo=self.process_memory, EnumPageFilesW=lambda *args: True)
        self.pdh = self.dll(PdhOpenQueryW=self.open_query, PdhAddEnglishCounterW=self.add_counter,
                           PdhCollectQueryData=self.collect, PdhGetFormattedCounterValue=self.counter_value,
                           PdhCloseQuery=self.close_query)
        self.advapi = self.dll(OpenProcessToken=self.open_token, LookupPrivilegeValueW=self.lookup,
                              GetTokenInformation=self.token_info, AdjustTokenPrivileges=self.adjust)
        self.ntdll = self.dll(NtSetSystemInformation=self.nt_call)

    @staticmethod
    def dll(**functions):
        return SimpleNamespace(**{name: Function(function) for name, function in functions.items()})

    def load(self, name):
        self.loads.append(name)
        return {"kernel32.dll": self.kernel, "psapi.dll": self.psapi, "pdh.dll": self.pdh,
                "advapi32.dll": self.advapi, "ntdll.dll": self.ntdll}[name]

    def performance(self, pointer, size):
        if self.fail_memory:
            c.set_last_error(5)
            return False
        values = {"PageSize": 4096, "PhysicalTotal": 1000, "PhysicalAvailable": 300,
                  "CommitTotal": 600, "CommitLimit": 2000, "CommitPeak": 650, "SystemCache": 150}
        for key, value in values.items():
            setattr(pointer._obj, key, value)
        return True

    def affinity(self, thread, pointer):
        if self.fail_topology:
            c.set_last_error(5)
            return False
        pointer._obj.Group = self.group
        pointer._obj.Mask = 0xff
        return True

    def system_times(self, idle, kern, user):
        if self.fail_times:
            c.set_last_error(5)
            return False
        for pointer, seconds in ((idle, 100), (kern, 200), (user, 50)):
            filetime(pointer, seconds * 10_000_000)
        return True

    def enum_processes(self, ids, size, needed):
        for index, pid in enumerate((11, 12, 13)):
            ids[index] = pid
        needed._obj.value = 3 * c.sizeof(api.w.DWORD)
        return True

    def open_process(self, access, inherit, pid):
        self.opens.append((access, inherit, pid))
        return None if pid == 13 else pid

    def close_handle(self, handle):
        self.closed.append(handle_value(handle))
        if handle_value(handle) == 500 and self.fail_close_token:
            c.set_last_error(5)
            return False
        return True

    def process_memory(self, handle, pointer, size):
        pointer._obj.WorkingSetSize = handle * 100
        pointer._obj.PrivateUsage = handle * 200
        return True

    def image(self, handle, flags, name, length):
        name.value = r"C:\private\example.exe"
        return True

    def process_times(self, handle, created, exited, kern, user):
        filetime(created, (1 << 40) + handle)
        filetime(kern, handle * 10_000_000)
        filetime(user, 20_000_000)
        return True

    def open_query(self, source, context, query):
        query._obj.value = 50
        return 0

    def add_counter(self, query, path, context, counter):
        key = len(self.counters) + 100
        self.counters[key] = path
        counter._obj.value = key
        return 0

    def collect(self, query):
        self.collections += 1
        return 0

    def counter_value(self, handle, flags, kind, value):
        if flags & api.PDH_FMT_DOUBLE:
            if self.collections == 1:
                value._obj.CStatus = api.PDH_CSTATUS_INVALID_DATA
                return api.PDH_INVALID_DATA
            value._obj.value.doubleValue = self.rate
        elif flags & api.PDH_FMT_LARGE:
            value._obj.value.largeValue = 100
        else:
            raise AssertionError("必须显式选择计数器数值类型")
        value._obj.CStatus = 0
        return 0

    def close_query(self, query):
        self.closed.append(handle_value(query))
        return 5 if self.fail_close_query else 0

    def open_token(self, current, access, token):
        self.opens.append(("token", access))
        token._obj.value = 500
        return True

    def lookup(self, system, name, luid):
        if name != api.PRIVILEGE:
            raise AssertionError("只能查询固定特权")
        luid._obj.LowPart = 5
        luid._obj.HighPart = 0
        return True

    def token_info(self, token, kind, buffer, length, size):
        size._obj.value = c.sizeof(api.TokenPrivilegesOne)
        if buffer is None:
            c.set_last_error(122)
            return False
        state = api.TokenPrivilegesOne.from_buffer(buffer)
        state.PrivilegeCount = 0 if self.attrs is None else 1
        state.Privileges[0].Luid = api.Luid(5, 0)
        state.Privileges[0].Attributes = self.attrs or 0
        return True

    def adjust(self, token, all_privileges, state, size, previous, needed):
        attrs = state._obj.Privileges[0].Attributes
        self.adjustments.append(attrs)
        if (attrs == api.SE_PRIVILEGE_ENABLED and self.fail_enable) or (attrs == 0 and self.fail_restore):
            c.set_last_error(1300)
            return True
        self.attrs = attrs
        c.set_last_error(0)
        return True

    def nt_call(self, info_class, command, size):
        self.nt_calls.append((info_class, command._obj.value, size))
        if self.nt_error:
            raise self.nt_error
        return self.nt_status

    def release_mutex(self, handle):
        if self.fail_release:
            c.set_last_error(5)
            return False
        return True


class WindowsTests(unittest.TestCase):
    def setUp(self):
        native_guard = patch.object(api.c, "WinDLL", side_effect=AssertionError("测试禁止加载真实DLL"), create=True)
        native_guard.start()
        self.addCleanup(native_guard.stop)
        self.fake = FakeSystem()
        self.lock = threading.Lock()
        self.guard = patch.object(api, "_maintenance_lock", self.lock)
        self.guard.start()
        self.addCleanup(self.guard.stop)
        self.backend = api.WindowsBackend(self.fake.load, now=lambda: "2026-10-21T00:00:00+00:00")

    def operation(self, mechanism="working-set"):
        return self.backend.open_operation(ActionRequest(mechanism, confirmed=True, confirmation_source="test"))

    def test_import_and_backend_construction_do_not_load_dlls(self):
        with patch.object(api.c, "WinDLL", side_effect=AssertionError("禁止真实DLL")):
            spec = importlib.util.spec_from_file_location("cpu_memory_cleanup.system.platforms._windows_probe", api.__file__)
            fresh = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(fresh)
            fresh.WindowsBackend()
        self.assertEqual(self.fake.loads, [])

    def test_windows_native_layouts_match_documented_field_widths(self):
        if api.sys.platform != "win32":
            self.skipTest("仅检查 Windows 原生 ctypes 布局，不调用系统")
        x64 = c.sizeof(c.c_void_p) == 8
        self.assertEqual(c.sizeof(api.PerformanceInfo), 104 if x64 else 56)
        self.assertEqual(c.sizeof(api.ProcessMemoryEx), 80 if x64 else 44)
        self.assertEqual(c.sizeof(api.GroupAffinity), 16 if x64 else 12)
        self.assertEqual(api.TokenPrivilegesOne.Privileges.offset, 4)
        self.assertEqual(c.sizeof(api.TokenPrivilegesOne), 16)

    def test_observation_has_memory_cpu_and_first_tick_gaps(self):
        session = self.backend.open_observation()
        try:
            sample = session.read("run", 0)
            later = session.read("run", 1)
        finally:
            closed = session.close()
        self.assertTrue(closed["closed"])
        self.assertEqual(sample.metrics["physical"]["used_bytes"], 700 * 4096)
        self.assertEqual(sample.metrics["cpu"]["total_seconds"], 250)
        self.assertEqual(sample.metrics["cpu"]["idle_seconds"], 100)
        self.assertEqual(sample.metrics["cpu"]["coverage"], "complete")
        self.assertTrue(all(value is None for value in sample.metrics["rates"].values()))
        self.assertTrue(all(value == 1.5 for value in later.metrics["rates"].values()))
        self.assertEqual(self.fake.opens, [])
        self.assertEqual(self.fake.nt_calls, [])

    def test_multigroup_cpu_is_not_complete_system_coverage(self):
        self.fake.groups, self.fake.total, self.fake.group, self.fake.group_count = 2, 96, 1, 32
        session = self.backend.open_observation()
        try:
            cpu = session.read("r", 0).metrics["cpu"]
        finally:
            session.close()
        self.assertEqual((cpu["scope"], cpu["coverage"], cpu["processor_group"]), ("group", "partial", 1))
        self.assertEqual(cpu["logical_processors"], 96)
        self.assertEqual(cpu["group_logical_processors"], 32)

    def test_unknown_topology_never_claims_system_coverage(self):
        self.fake.fail_topology = True
        session = self.backend.open_observation()
        try:
            sample = session.read("r", 0)
        finally:
            session.close()
        self.assertEqual(sample.metrics["cpu"]["coverage"], "unknown")
        self.assertIsNone(sample.metrics["cpu"]["logical_processors"])
        self.assertTrue(sample.errors)

    def test_process_identity_cpu_and_commit_survive_top_one(self):
        session = self.backend.open_observation(with_processes=True, top=1)
        try:
            sample = session.read("r", 0)
        finally:
            session.close()
        self.assertEqual(len(sample.processes), 2)
        first = sample.processes[0]
        self.assertEqual(first["pid"], 11)
        self.assertEqual(first["created_at"], str((1 << 40) + 11))
        self.assertEqual(first["cpu_seconds"], 13)
        self.assertEqual(first["private_bytes"], 2200)
        self.assertEqual(first["name"], "example.exe")
        self.assertEqual(sample.metrics["process_coverage"]["coverage"], "partial")
        self.assertTrue(all(item[0] == 0x1000 for item in self.fake.opens))
        self.assertIn(11, self.fake.closed)
        self.assertIn(12, self.fake.closed)

    def test_nonfinite_pdh_value_is_explicitly_missing(self):
        session = self.backend.open_observation()
        try:
            session.read("r", 0)
            self.fake.rate = float("nan")
            sample = session.read("r", 1)
        finally:
            session.close()
        self.assertTrue(all(value is None for value in sample.metrics["rates"].values()))
        self.assertTrue(sample.errors)

    def test_pdh_open_failure_remains_visible_in_every_sample(self):
        self.fake.pdh.PdhOpenQueryW = Function(lambda *args: 5)
        session = self.backend.open_observation()
        try:
            first = session.read("r", 0)
            first.errors.append("sample-local")
            later = session.read("r", 1)
        finally:
            closed = session.close()
        self.assertIn("PDH open unavailable: 0x00000005", later.errors)
        self.assertNotIn("sample-local", later.errors)
        self.assertTrue(all(value is None for value in later.metrics["rates"].values()))
        self.assertTrue(closed["closed"])

    def test_pdh_invalid_rates_keep_each_failure_after_first_sample_warmup(self):
        original = self.fake.pdh.PdhGetFormattedCounterValue

        def invalid_rate(handle, flags, kind, value):
            if flags & api.PDH_FMT_DOUBLE and self.fake.collections < 4:
                value._obj.CStatus = api.PDH_CSTATUS_INVALID_DATA
                return c.c_long(api.PDH_INVALID_DATA).value
            return original(handle, flags, kind, value)

        self.fake.pdh.PdhGetFormattedCounterValue = Function(invalid_rate)
        session = self.backend.open_observation()
        try:
            first = session.read("r", 0)
            second = session.read("r", 1)
            third = session.read("r", 2)
            recovered = session.read("r", 3)
        finally:
            closed = session.close()
        self.assertTrue(any("no previous PDH interval" in error for error in first.errors))
        self.assertEqual(len(second.errors), len(api.RATE_COUNTERS))
        self.assertEqual(third.errors, second.errors)
        self.assertTrue(all("0xc0000bc6" in error and "no previous" not in error for error in second.errors))
        self.assertTrue(all(value is None for value in third.metrics["rates"].values()))
        self.assertEqual(recovered.errors, [])
        self.assertTrue(all(value == self.fake.rate for value in recovered.metrics["rates"].values()))
        self.assertTrue(closed["closed"])

    def test_pdh_formatting_preserves_capacity_and_rate_units(self):
        def scaled_counter(handle, flags, kind, value):
            # 按 PDH 官方 NOSCALE 合同模拟非零默认缩放，不读取真实计数器。
            reading = 100_000_000 if flags & 0x1000 else 10_000
            if flags & api.PDH_FMT_DOUBLE:
                value._obj.value.doubleValue = reading
            elif flags & api.PDH_FMT_LARGE:
                value._obj.value.largeValue = reading
            else:
                raise AssertionError("必须显式选择计数器数值类型")
            value._obj.CStatus = 0
            return 0

        self.fake.pdh.PdhGetFormattedCounterValue = Function(scaled_counter)
        session = self.backend.open_observation()
        try:
            first = session.read("r", 0)
            later = session.read("r", 1)
        finally:
            closed = session.close()
        self.assertEqual(later.metrics["cache"]["cache_bytes"], 100_000_000)
        self.assertEqual(later.metrics["rates"]["disk_read_bytes_per_sec"], 100_000_000)
        self.assertTrue(all(value is None for value in first.metrics["rates"].values()))
        self.assertTrue(closed["closed"])

    def test_required_memory_read_failure_propagates_and_closes(self):
        session = self.backend.open_observation()
        self.fake.fail_memory = True
        with self.assertRaises(OSError):
            session.read("r", 0)
        self.assertTrue(session.close()["closed"])
        self.assertEqual(self.fake.nt_calls, [])

    def test_query_close_failure_is_not_success(self):
        session = self.backend.open_observation()
        session.read("r", 0)
        self.fake.fail_close_query = True
        self.assertFalse(session.close()["closed"])

    def test_unread_observation_closes_without_opening_pdh(self):
        session = self.backend.open_observation()
        self.assertTrue(session.close()["closed"])
        self.assertNotIn("pdh.dll", self.fake.loads)
        self.assertEqual(self.fake.closed, [])

    def test_inspect_pdh_initialization_interrupt_closes_owned_query(self):
        interruption = KeyboardInterrupt("fake PDH initialization interruption")

        def interrupt_add(*args):
            raise interruption

        self.fake.pdh.PdhAddEnglishCounterW = Function(interrupt_add)
        app = make_app(self.backend, self.backend)
        with self.assertRaises(KeyboardInterrupt) as caught:
            app.inspect()
        self.assertIs(caught.exception, interruption)
        self.assertFalse(app.busy)
        self.assertFalse(app._unsafe)
        self.assertEqual(self.fake.closed.count(50), 1)
        self.assertEqual(self.fake.nt_calls, [])

    def test_maintain_pdh_initialization_interrupt_closes_query_and_operation(self):
        interruption = KeyboardInterrupt("fake PDH initialization interruption")

        def interrupt_add(*args):
            raise interruption

        self.fake.pdh.PdhAddEnglishCounterW = Function(interrupt_add)
        app = make_app(self.backend, self.backend)
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(KeyboardInterrupt) as caught:
                app.maintain(ActionRequest("working-set", confirmed=True,
                                           confirmation_source="test"), directory)
        self.assertIs(caught.exception, interruption)
        self.assertFalse(app.busy)
        self.assertEqual(self.fake.closed.count(50), 1)
        self.assertIn(500, self.fake.closed)
        self.assertIn(700, self.fake.closed)
        self.assertFalse(self.lock.locked())
        self.assertEqual(self.fake.adjustments, [])
        self.assertEqual(self.fake.nt_calls, [])

    def test_pdh_initialization_interrupt_close_failure_keeps_resource_and_blocks_requests(self):
        interruption = KeyboardInterrupt("fake PDH initialization interruption")

        def interrupt_add(*args):
            raise interruption

        self.fake.pdh.PdhAddEnglishCounterW = Function(interrupt_add)
        self.fake.fail_close_query = True
        app = make_app(self.backend, self.backend)
        with self.assertRaises(KeyboardInterrupt) as caught:
            app.inspect()
        self.assertIs(caught.exception, interruption)
        self.assertFalse(app.busy)
        self.assertTrue(app._unsafe)
        self.assertEqual(len(app._pending_resources), 1)
        session = app._pending_resources[0]
        self.assertEqual(session.counters._query.value, 50)
        self.assertFalse(session.closed)
        loads = self.fake.loads.copy()
        self.assertEqual(app.inspect().completion, "failed")
        with tempfile.TemporaryDirectory() as directory:
            rejected = app.maintain(ActionRequest("working-set", confirmed=True,
                                                 confirmation_source="test"), directory)
        self.assertEqual(rejected.completion, "failed")
        self.assertEqual(self.fake.loads, loads)
        self.fake.fail_close_query = False
        self.assertTrue(session.close()["closed"])
        self.assertIsNone(session.counters._query)
        self.assertEqual(self.fake.nt_calls, [])

    def test_pdh_initialization_error_is_not_retried_and_gaps_remain(self):
        for exception_type in (OSError, AttributeError):
            with self.subTest(exception_type=exception_type.__name__):
                fake = FakeSystem()
                additions = []

                def failed_add(*args):
                    additions.append(True)
                    raise exception_type("fake counter initialization failure")

                fake.pdh.PdhAddEnglishCounterW = Function(failed_add)
                backend = api.WindowsBackend(fake.load, now=lambda: "2026-10-21T00:00:00+00:00")
                session = backend.open_observation()
                try:
                    first = session.read("r", 0)
                    later = session.read("r", 1)
                finally:
                    closed = session.close()
                self.assertTrue(closed["closed"])
                self.assertEqual(additions, [True])
                self.assertEqual(fake.loads.count("pdh.dll"), 1)
                self.assertTrue(any("fake counter initialization failure" in error for error in first.errors))
                self.assertEqual(later.errors, first.errors)
                self.assertTrue(all(value is None for value in later.metrics["rates"].values()))
                self.assertEqual(fake.nt_calls, [])

    def test_pdh_open_and_cleanup_failures_keep_session_for_later_close(self):
        def failed_add(*args):
            raise OSError("模拟计数器初始化失败")
        self.fake.pdh.PdhAddEnglishCounterW = Function(failed_add)
        self.fake.fail_close_query = True
        session = self.backend.open_observation()
        sample = session.read("r", 0)
        self.assertTrue(any("计数器初始化失败" in error for error in sample.errors))
        self.assertTrue(any("PDH初始化收尾失败" in error for error in sample.errors))
        self.assertTrue(all(value is None for value in sample.metrics["rates"].values()))
        self.assertFalse(session.close()["closed"])
        self.fake.fail_close_query = False
        self.assertTrue(session.close()["closed"])
        self.assertEqual(self.fake.closed.count(50), 3)
        self.assertEqual(self.fake.nt_calls, [])

    def test_failed_process_handle_close_remains_owned_until_confirmed(self):
        original = self.fake.kernel.CloseHandle
        self.fake.kernel.CloseHandle = Function(lambda handle: False if handle_value(handle) == 11 else original(handle))
        session = self.backend.open_observation(with_processes=True)
        sample = session.read("r", 0)
        self.assertEqual(len(sample.processes), 2)
        self.assertFalse(session.close()["closed"])
        self.fake.kernel.CloseHandle = original
        self.assertTrue(session.close()["closed"])

    def test_operation_constructor_is_lazy_and_requires_confirmation(self):
        operation = self.operation()
        self.assertEqual(self.fake.loads, [])
        self.assertTrue(operation.close()["closed"])
        with self.assertRaises(ValueError):
            self.backend.open_operation(ActionRequest("working-set"))

    def test_observation_top_contract_rejects_out_of_range_before_loading(self):
        for top in (0, 101, True):
            with self.subTest(top=top), self.assertRaises(ValueError):
                self.backend.open_observation(top=top)
        self.assertEqual(self.fake.loads, [])

    def test_preflight_queries_only_and_each_mechanism_calls_once(self):
        for mechanism, command in (("working-set", 2), ("standby", 4)):
            with self.subTest(mechanism=mechanism):
                operation = self.operation(mechanism)
                ready = operation.preflight()
                self.assertTrue(all(ready[key] for key in ("supported", "privilege_available", "lock_acquired")))
                before = len(self.fake.nt_calls)
                self.assertEqual(self.fake.adjustments, [] if before == 0 else [2, 0])
                result = operation.perform_once()
                self.assertEqual(result["call_outcome"], "success")
                self.assertTrue(result["privilege_restored"])
                self.assertEqual(self.fake.nt_calls[-1][1], command)
                self.assertEqual(len(self.fake.nt_calls), before + 1)
                with self.assertRaises(RuntimeError):
                    operation.perform_once()
                self.assertTrue(operation.close()["closed"])

    def test_enable_failure_is_zero_native_calls(self):
        operation = self.operation()
        self.fake.fail_enable = True
        result = operation.perform_once()
        self.assertFalse(result["attempted"])
        self.assertEqual(result["call_outcome"], "not_started")
        self.assertEqual(self.fake.nt_calls, [])
        self.assertTrue(result["privilege_restored"])
        self.assertTrue(operation.close()["closed"])

    def test_missing_or_removed_privilege_is_refused(self):
        for attrs in (None, api.SE_PRIVILEGE_REMOVED):
            with self.subTest(attrs=attrs):
                self.fake.attrs = attrs
                operation = self.operation()
                self.assertFalse(operation.preflight()["privilege_available"])
                self.assertEqual(operation.perform_once()["call_outcome"], "not_started")
                self.assertTrue(operation.close()["closed"])
        self.assertEqual(self.fake.adjustments, [])
        self.assertEqual(self.fake.nt_calls, [])

    def test_already_enabled_privilege_is_preserved(self):
        self.fake.attrs = 3
        operation = self.operation()
        result = operation.perform_once()
        self.assertEqual(result["call_outcome"], "success")
        self.assertEqual(self.fake.adjustments, [])
        self.assertEqual(self.fake.attrs, 3)
        self.assertTrue(operation.close()["closed"])

    def test_restore_failure_keeps_success_fact_and_blocks_more_maintenance(self):
        self.fake.fail_restore = True
        operation = self.operation()
        result = operation.perform_once()
        self.assertEqual(result["call_outcome"], "success")
        self.assertFalse(result["privilege_restored"])
        self.assertFalse(operation.close()["closed"])
        other = self.operation()
        self.assertFalse(other.preflight()["lock_acquired"])
        self.assertEqual(other.perform_once()["call_outcome"], "not_started")
        other.close()
        self.assertEqual(len(self.fake.nt_calls), 1)

    def test_restore_interrupt_blocks_new_application_and_closes_token(self):
        interruption = KeyboardInterrupt("fake restore interruption")
        adjust = self.fake.advapi.AdjustTokenPrivileges.function

        def interrupt_restore(token, all_privileges, state, size, previous, needed):
            if state._obj.Privileges[0].Attributes == 0:
                raise interruption
            return adjust(token, all_privileges, state, size, previous, needed)

        self.fake.advapi.AdjustTokenPrivileges.function = interrupt_restore
        operation = self.operation()
        with self.assertRaises(KeyboardInterrupt) as caught:
            operation.perform_once()
        self.assertIs(caught.exception, interruption)
        self.assertEqual(operation.tokens, [])
        self.assertEqual(self.fake.closed.count(500), 2)
        self.assertEqual(self.fake.attrs, api.SE_PRIVILEGE_ENABLED)
        self.assertFalse(operation.close()["closed"])
        self.assertTrue(self.lock.locked())
        with tempfile.TemporaryDirectory() as directory:
            app = make_app(self.backend, self.backend)
            report = app.maintain(ActionRequest("working-set", confirmed=True,
                                               confirmation_source="test"), directory)
        self.assertEqual(report.completion, "failed")
        self.assertFalse(report.action["attempted"])
        self.assertEqual(len(self.fake.nt_calls), 1)
        self.assertTrue(self.lock.locked())

    def test_restore_verification_interrupt_keeps_process_guard(self):
        interruption = SystemExit("fake restore verification interruption")
        query = self.fake.advapi.GetTokenInformation.function

        def interrupt_verification(*args):
            if self.fake.adjustments == [api.SE_PRIVILEGE_ENABLED, 0]:
                raise interruption
            return query(*args)

        self.fake.advapi.GetTokenInformation.function = interrupt_verification
        operation = self.operation()
        with self.assertRaises(SystemExit) as caught:
            operation.perform_once()
        self.assertIs(caught.exception, interruption)
        self.assertEqual(operation.tokens, [])
        self.assertEqual(self.fake.attrs, 0)
        self.assertFalse(operation.close()["closed"])
        self.assertTrue(self.lock.locked())
        other = self.operation()
        self.assertFalse(other.preflight()["lock_acquired"])
        self.assertTrue(other.close()["closed"])
        self.assertEqual(len(self.fake.nt_calls), 1)

    def test_existing_privilege_verification_interrupt_keeps_process_guard(self):
        interruption = KeyboardInterrupt("fake existing privilege verification interruption")
        self.fake.attrs = 3
        query = self.fake.advapi.GetTokenInformation.function

        def interrupt_verification(*args):
            if self.fake.nt_calls:
                raise interruption
            return query(*args)

        self.fake.advapi.GetTokenInformation.function = interrupt_verification
        operation = self.operation()
        with self.assertRaises(KeyboardInterrupt) as caught:
            operation.perform_once()
        self.assertIs(caught.exception, interruption)
        self.assertEqual(operation.tokens, [])
        self.assertEqual(self.fake.adjustments, [])
        self.assertFalse(operation.close()["closed"])
        self.assertTrue(self.lock.locked())
        other = self.operation()
        self.assertFalse(other.preflight()["lock_acquired"])
        self.assertTrue(other.close()["closed"])
        self.assertEqual(len(self.fake.nt_calls), 1)

    def test_call_interrupt_survives_restore_interrupt(self):
        interruption = KeyboardInterrupt("fake call interruption")
        self.fake.nt_error = interruption
        adjust = self.fake.advapi.AdjustTokenPrivileges.function

        def interrupt_restore(token, all_privileges, state, size, previous, needed):
            if state._obj.Privileges[0].Attributes == 0:
                raise SystemExit("fake restore interruption")
            return adjust(token, all_privileges, state, size, previous, needed)

        self.fake.advapi.AdjustTokenPrivileges.function = interrupt_restore
        operation = self.operation()
        with self.assertRaises(BaseException) as caught:
            operation.perform_once()
        self.assertIs(caught.exception, interruption)
        self.assertEqual(operation.tokens, [])
        self.assertFalse(operation.close()["closed"])
        self.assertTrue(self.lock.locked())
        self.assertEqual(len(self.fake.nt_calls), 1)

    def test_busy_or_abandoned_mutex_is_zero_calls(self):
        for status in (0x102, 0x80):
            with self.subTest(status=status):
                self.fake.mutex_status = status
                operation = self.operation()
                self.assertFalse(operation.preflight()["lock_acquired"])
                self.assertEqual(operation.perform_once()["call_outcome"], "not_started")
                self.assertTrue(operation.close()["closed"])
        self.assertEqual(self.fake.nt_calls, [])

    def test_second_same_thread_operation_cannot_reenter_process_guard(self):
        first, second = self.operation(), self.operation()
        self.assertTrue(first.preflight()["lock_acquired"])
        self.assertFalse(second.preflight()["lock_acquired"])
        self.assertEqual(second.perform_once()["call_outcome"], "not_started")
        self.assertTrue(second.close()["closed"])
        self.assertTrue(first.close()["closed"])
        self.assertFalse(self.lock.locked())

    def test_native_failure_and_unknown_are_never_retried(self):
        self.fake.nt_status = -1073741727
        operation = self.operation()
        failed = operation.perform_once()
        self.assertTrue(failed["attempted"])
        self.assertEqual(failed["call_outcome"], "failure")
        self.assertEqual(failed["status"], 0xc0000061)
        self.assertTrue(operation.close()["closed"])
        self.fake.nt_error = OSError("模拟边界异常")
        operation = self.operation()
        unknown = operation.perform_once()
        self.assertIsNone(unknown["attempted"])
        self.assertEqual(unknown["call_outcome"], "unknown")
        self.assertTrue(unknown["privilege_restored"])
        self.assertTrue(operation.close()["closed"])
        self.assertEqual(len(self.fake.nt_calls), 2)

    def test_mutex_close_failure_is_not_hidden(self):
        operation = self.operation()
        operation.preflight()
        self.fake.fail_release = True
        self.assertFalse(operation.close()["closed"])
        self.assertTrue(self.lock.locked())

    def test_operation_preflight_never_enables_or_maintains(self):
        operation = self.operation()
        record = operation.preflight()
        self.assertTrue(record["privilege_available"])
        self.assertEqual(self.fake.adjustments, [])
        self.assertEqual(self.fake.nt_calls, [])
        self.assertEqual(self.fake.opens, [("token", api.TOKEN_QUERY)])
        self.assertTrue(operation.close()["closed"])

    def test_preflight_token_close_failure_keeps_handle_and_blocks_process(self):
        self.fake.fail_close_token = True
        operation = self.operation()
        self.assertFalse(operation.preflight()["privilege_available"])
        self.assertEqual(operation.perform_once()["call_outcome"], "not_started")
        self.assertFalse(operation.close()["closed"])
        other_backend = api.WindowsBackend(self.fake.load)
        other = other_backend.open_operation(ActionRequest("standby", confirmed=True, confirmation_source="test"))
        self.assertFalse(other.preflight()["lock_acquired"])
        self.assertTrue(other.close()["closed"])
        self.assertTrue(self.lock.locked())
        self.assertEqual(self.fake.nt_calls, [])
        self.fake.fail_close_token = False
        self.assertTrue(operation.close()["closed"])
        self.assertFalse(self.lock.locked())
        self.assertEqual(self.fake.closed.count(500), 3)

    def test_preflight_query_and_close_errors_are_both_kept(self):
        def query_failure(*args):
            raise OSError("模拟查询失败")
        self.fake.fail_close_token = True
        self.fake.advapi.GetTokenInformation = Function(query_failure)
        operation = self.operation()
        result = operation.preflight()
        self.assertTrue(any("模拟查询失败" in error for error in result["errors"]))
        self.assertTrue(any("令牌关闭失败" in error for error in result["errors"]))
        self.assertFalse(operation.close()["closed"])
        self.assertEqual(self.fake.nt_calls, [])

    def test_action_token_close_failure_preserves_call_and_blocks_until_closed(self):
        operation = self.operation()
        operation.preflight()
        self.fake.fail_close_token = True
        result = operation.perform_once()
        self.assertEqual(result["call_outcome"], "success")
        self.assertTrue(result["privilege_restored"])
        self.assertFalse(operation.close()["closed"])
        self.assertTrue(self.lock.locked())
        self.fake.fail_close_token = False
        self.assertTrue(operation.close()["closed"])
        self.assertFalse(self.lock.locked())
        self.assertEqual(len(self.fake.nt_calls), 1)


if __name__ == "__main__":
    unittest.main()
