"""Windows 接入：显式读取与单项维护，导入和构造不查询系统。"""

import ctypes as c
from ctypes import wintypes as w
from datetime import datetime, timezone
import ntpath
import sys
import math
import threading

from ...contracts import ActionRequest, Sample


class PerformanceInfo(c.Structure):
    _fields_ = [("cb", w.DWORD)] + [
        (name, c.c_size_t) for name in (
            "CommitTotal", "CommitLimit", "CommitPeak", "PhysicalTotal",
            "PhysicalAvailable", "SystemCache", "KernelTotal", "KernelPaged",
            "KernelNonpaged", "PageSize"
        )
    ] + [(name, w.DWORD) for name in ("HandleCount", "ProcessCount", "ThreadCount")]


class ProcessMemory(c.Structure):
    _fields_ = [("cb", w.DWORD), ("PageFaultCount", w.DWORD)] + [
        (name, c.c_size_t) for name in (
            "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
            "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage",
            "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage"
        )
    ]


class ProcessMemoryEx(ProcessMemory):
    _fields_ = [("PrivateUsage", c.c_size_t)]


class PageFileInfo(c.Structure):
    _fields_ = [("cb", w.DWORD), ("Reserved", w.DWORD)] + [
        (name, c.c_size_t) for name in ("TotalSize", "TotalInUse", "PeakUsage")
    ]


class CounterUnion(c.Union):
    _fields_ = [("largeValue", c.c_longlong), ("doubleValue", c.c_double),
                ("pointerValue", c.c_void_p)]


class CounterValue(c.Structure):
    _fields_ = [("CStatus", w.DWORD), ("value", CounterUnion)]


COUNTERS = {
    "standby_core_bytes": r"\Memory\Standby Cache Core Bytes",
    "standby_normal_bytes": r"\Memory\Standby Cache Normal Priority Bytes",
    "standby_reserve_bytes": r"\Memory\Standby Cache Reserve Bytes",
    "modified_bytes": r"\Memory\Modified Page List Bytes",
    "free_zero_bytes": r"\Memory\Free & Zero Page List Bytes",
    "cache_bytes": r"\Memory\Cache Bytes",
    "system_cache_resident_bytes": r"\Memory\System Cache Resident Bytes",
}
# Rates need two PDH collections. Page reads are disk operations that satisfy
# hard faults; one read can bring in multiple pages.
RATE_COUNTERS = {
    "page_reads_per_sec": r"\Memory\Page Reads/sec",
    "pages_input_per_sec": r"\Memory\Pages Input/sec",
    "disk_read_bytes_per_sec": r"\PhysicalDisk(_Total)\Disk Read Bytes/sec",
    "disk_write_bytes_per_sec": r"\PhysicalDisk(_Total)\Disk Write Bytes/sec",
}
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PDH_FMT_DOUBLE = 0x200
PDH_FMT_LARGE = 0x400
PDH_FMT_NOSCALE = 0x1000
PDH_CSTATUS_INVALID_DATA = 0xC0000BBA
PDH_INVALID_DATA = 0xC0000BC6


def bind(dll, name, args, result):
    function = getattr(dll, name)
    function.argtypes = args
    function.restype = result
    return function


def check(ok):
    if not ok:
        raise c.WinError(c.get_last_error())


class CounterSession:
    """复用一个PDH查询，关闭失败时保留查询供收尾重试。"""

    def __init__(self, include_rates=False, dll_loader=None):
        self._dll_loader = _load_dll if dll_loader is None else dll_loader
        self.include_rates = include_rates
        self.ready = False
        self._handles = {}
        self._formats = {}
        self._query = None
        self._collect = None
        self._get = None
        self._close = None
        self._close_unknown = False
        self._first_collection = True

    def open(self, warnings):
        specs = dict(COUNTERS)
        if self.include_rates:
            specs.update(RATE_COUNTERS)
        pdh = self._dll_loader("pdh.dll")
        open_query = bind(pdh, "PdhOpenQueryW", [w.LPCWSTR, c.c_size_t, c.POINTER(w.HANDLE)], w.LONG)
        add = bind(pdh, "PdhAddEnglishCounterW", [w.HANDLE, w.LPCWSTR, c.c_size_t, c.POINTER(w.HANDLE)], w.LONG)
        self._collect = bind(pdh, "PdhCollectQueryData", [w.HANDLE], w.LONG)
        self._get = bind(pdh, "PdhGetFormattedCounterValue",
                         [w.HANDLE, w.DWORD, c.POINTER(w.DWORD), c.POINTER(CounterValue)], w.LONG)
        self._close = bind(pdh, "PdhCloseQuery", [w.HANDLE], w.LONG)
        query = self._query = w.HANDLE()
        status = open_query(None, 0, c.byref(query))
        if status:
            warnings.append(f"PDH open unavailable: 0x{status & 0xffffffff:08x}")
            return False
        self.ready = True
        for key, path in specs.items():
            handle = w.HANDLE()
            status = add(query, path, 0, c.byref(handle))
            if status:
                warnings.append(f"{key} unavailable: PDH 0x{status & 0xffffffff:08x}")
            else:
                self._handles[key] = handle
                self._formats[key] = (PDH_FMT_DOUBLE if key in RATE_COUNTERS else PDH_FMT_LARGE) | PDH_FMT_NOSCALE
        return True

    def collect(self, warnings):
        capacity = dict.fromkeys(COUNTERS)
        rates = dict.fromkeys(RATE_COUNTERS) if self.include_rates else None
        if not self.ready:
            return capacity, rates
        status = self._collect(self._query)
        if status:
            warnings.append(f"PDH collection unavailable: 0x{status & 0xffffffff:08x}")
            return capacity, rates
        first_collection, self._first_collection = self._first_collection, False
        for key, handle in self._handles.items():
            if key in RATE_COUNTERS and first_collection:
                warnings.append(f"{key} unavailable until the next sample: no previous PDH interval")
                continue
            value = CounterValue()
            status = self._get(handle, self._formats[key], None, c.byref(value))
            if key in RATE_COUNTERS:
                reading = value.value.doubleValue
            else:
                reading = value.value.largeValue
            if status == 0 and value.CStatus in (0, 1) and math.isfinite(reading) and reading >= 0:
                if key in RATE_COUNTERS:
                    rates[key] = reading
                else:
                    capacity[key] = reading
            else:
                warnings.append(
                    f"{key} unavailable: PDH 0x{status & 0xffffffff:08x}, CStatus 0x{value.CStatus:08x}")
        return capacity, rates

    def close(self):
        if self._query and self._close is not None:
            if self._close_unknown:
                raise OSError("PDH关闭结果未知，不能重试关闭")
            self._close_unknown = True
            status = self._close(self._query)
            if not status:
                self._query = None
            self._close_unknown = False
            if status:
                raise OSError(f"PdhCloseQuery failed: 0x{status & 0xffffffff:08x}")
        self._query = None
        self.ready = False


def read_pagefiles(psapi, page_size):
    totals = {"count": 0, "total_bytes": 0, "used_bytes": 0}
    callback_type = c.WINFUNCTYPE(w.BOOL, c.c_void_p, c.POINTER(PageFileInfo), w.LPCWSTR)

    @callback_type
    def callback(context, info, filename):
        totals["count"] += 1
        totals["total_bytes"] += info.contents.TotalSize * page_size
        totals["used_bytes"] += info.contents.TotalInUse * page_size
        return True

    enum = bind(psapi, "EnumPageFilesW", [callback_type, c.c_void_p], w.BOOL)
    check(enum(callback, None))
    return totals


class GroupAffinity(c.Structure):
    _fields_ = [("Mask", c.c_size_t), ("Group", w.WORD), ("Reserved", w.WORD * 3)]


class Luid(c.Structure):
    _fields_ = [("LowPart", w.DWORD), ("HighPart", w.LONG)]


class LuidAttributes(c.Structure):
    _fields_ = [("Luid", Luid), ("Attributes", w.DWORD)]


class TokenPrivilegesOne(c.Structure):
    _fields_ = [("PrivilegeCount", w.DWORD), ("Privileges", LuidAttributes * 1)]


PRIVILEGE = "SeProfileSingleProcessPrivilege"
TOKEN_QUERY = 0x0008
TOKEN_ADJUST_PRIVILEGES = 0x0020
SE_PRIVILEGE_ENABLED = 0x0002
SE_PRIVILEGE_REMOVED = 0x0004
COMMANDS = {"working-set": 2, "standby": 4}
MUTEX_NAME = r"Local\DreamSoul.CpuMemoryCleanup.Maintenance"
_maintenance_lock = threading.Lock()


def _load_dll(name):
    if sys.platform != "win32":
        raise OSError("需要 Windows，未执行系统操作")
    return c.WinDLL(name, use_last_error=True)


def _error_text(error):
    return f"{type(error).__name__}: {error}"


def _filetime(value):
    return (value.dwHighDateTime << 32) | value.dwLowDateTime


def _cpu_topology(kernel):
    groups = bind(kernel, "GetActiveProcessorGroupCount", [], w.WORD)()
    count = bind(kernel, "GetActiveProcessorCount", [w.WORD], w.DWORD)
    total = count(0xffff)
    if not groups or not total:
        raise OSError("无法确认活动处理器范围")
    thread = bind(kernel, "GetCurrentThread", [], w.HANDLE)()
    affinity = GroupAffinity()
    get_affinity = bind(kernel, "GetThreadGroupAffinity", [w.HANDLE, c.POINTER(GroupAffinity)], w.BOOL)
    check(get_affinity(thread, c.byref(affinity)))
    group_count = count(affinity.Group)
    if (not group_count or affinity.Group >= groups or group_count > 64 or
            total < group_count or (groups == 1 and total != group_count)):
        raise OSError("无法确认当前处理器组范围")
    return (int(groups), int(total), int(affinity.Group), int(group_count))


def _read_cpu(kernel, errors):
    result = {"total_seconds": None, "idle_seconds": None, "logical_processors": None,
              "processor_group": None, "scope": "unknown", "coverage": "unknown"}
    try:
        before = _cpu_topology(kernel)
    except (OSError, AttributeError) as error:
        before = None
        errors.append("CPU覆盖未知: " + _error_text(error))
    try:
        idle, kern, user = w.FILETIME(), w.FILETIME(), w.FILETIME()
        get_times = bind(kernel, "GetSystemTimes", [c.POINTER(w.FILETIME)] * 3, w.BOOL)
        check(get_times(c.byref(idle), c.byref(kern), c.byref(user)))
        result["total_seconds"] = (_filetime(kern) + _filetime(user)) / 10_000_000
        result["idle_seconds"] = _filetime(idle) / 10_000_000
        after = _cpu_topology(kernel) if before is not None else None
        if before is not None and before == after:
            groups, total, group, group_count = before
            result.update(logical_processors=total, processor_group=group,
                          scope="system" if groups == 1 else "group",
                          coverage="complete" if groups == 1 else "partial",
                          group_logical_processors=group_count)
        elif before is not None:
            errors.append("采集期间处理器覆盖改变，CPU范围未知")
    except (OSError, AttributeError) as error:
        errors.append("CPU读取不完整: " + _error_text(error))
    return result


def _read_processes(kernel, psapi, errors, pending_handles, unknown_handles):
    enum = bind(psapi, "EnumProcesses", [c.POINTER(w.DWORD), w.DWORD, c.POINTER(w.DWORD)], w.BOOL)
    open_process = bind(kernel, "OpenProcess", [w.DWORD, w.BOOL, w.DWORD], w.HANDLE)
    close = bind(kernel, "CloseHandle", [w.HANDLE], w.BOOL)
    memory = bind(psapi, "GetProcessMemoryInfo", [w.HANDLE, c.POINTER(ProcessMemoryEx), w.DWORD], w.BOOL)
    image = bind(kernel, "QueryFullProcessImageNameW", [w.HANDLE, w.DWORD, w.LPWSTR, c.POINTER(w.DWORD)], w.BOOL)
    times = bind(kernel, "GetProcessTimes", [w.HANDLE] + [c.POINTER(w.FILETIME)] * 4, w.BOOL)
    capacity = 1024
    while capacity <= 131072:
        ids, needed = (w.DWORD * capacity)(), w.DWORD()
        check(enum(ids, c.sizeof(ids), c.byref(needed)))
        if needed.value > c.sizeof(ids) or needed.value % c.sizeof(w.DWORD):
            raise OSError("EnumProcesses返回长度无效")
        if needed.value < c.sizeof(ids):
            break
        capacity *= 2
    else:
        raise OSError("进程枚举超过有界上限")
    processes, skipped = [], 0
    for pid in ids[:needed.value // c.sizeof(w.DWORD)]:
        handle = open_process(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            skipped += 1
            continue
        pending_handles.append(handle)
        try:
            stats = ProcessMemoryEx(cb=c.sizeof(ProcessMemoryEx))
            name, length = c.create_unicode_buffer(32768), w.DWORD(32768)
            created, exited, kern, user = (w.FILETIME() for _ in range(4))
            if not (memory(handle, c.byref(stats), c.sizeof(stats)) and
                    image(handle, 0, name, c.byref(length)) and
                    times(handle, c.byref(created), c.byref(exited), c.byref(kern), c.byref(user))):
                skipped += 1
                continue
            processes.append({"pid": int(pid), "created_at": str(_filetime(created)),
                              "cpu_seconds": (_filetime(kern) + _filetime(user)) / 10_000_000,
                              "name": ntpath.basename(name.value), "working_set_bytes": stats.WorkingSetSize,
                              "private_bytes": stats.PrivateUsage, "parent_pid": None})
        finally:
            unknown_handles.add(handle)
            closed = close(handle)
            if closed:
                pending_handles.remove(handle)
            unknown_handles.remove(handle)
            if not closed:
                errors.append("进程读取句柄关闭失败")
    if skipped:
        errors.append(f"进程读取不完整，{skipped}项不可读或已退出")
    return processes, {"enumerated": needed.value // c.sizeof(w.DWORD), "readable": len(processes),
                       "scope": "enumerated_processes", "coverage": "partial" if skipped else "complete"}


class WindowsBackend:
    """只保存接入参数；原生能力仅在显式打开会话后取得。"""

    def __init__(self, dll_loader=None, now=None):
        self._dll = _load_dll if dll_loader is None else dll_loader
        self._now = now or (lambda: datetime.now(timezone.utc).isoformat())

    def open_observation(self, *, with_processes=False, top=10):
        if type(with_processes) is not bool or type(top) is not int or not 1 <= top <= 100:
            raise ValueError("读取范围或top无效")
        return _Observation(self, with_processes)

    def open_operation(self, request):
        if (not isinstance(request, ActionRequest) or request.mechanism not in COMMANDS
                or not request.confirmed or not request.confirmation_source.strip()):
            raise ValueError("维护需要本次明确确认")
        return _Operation(self, request)


class _Observation:
    def __init__(self, backend, with_processes):
        self.backend, self.with_processes = backend, with_processes
        self.kernel, self.psapi = backend._dll("kernel32.dll"), backend._dll("psapi.dll")
        self.counters = CounterSession(include_rates=True, dll_loader=backend._dll)
        self._counters_initialized = False
        self.open_errors = []
        self.pending_handles = []
        self.unknown_handles = set()
        self.closed = False

    def read(self, run_id, sequence):
        if self.closed:
            raise RuntimeError("读取会话已关闭")
        if not self._counters_initialized:
            self._counters_initialized = True
            try:
                self.counters.open(self.open_errors)
            except (OSError, AttributeError) as error:
                self.open_errors.append("PDH不可用: " + _error_text(error))
                self.counters.ready = False
                try:
                    self.counters.close()
                except OSError as close_error:
                    self.open_errors.append("PDH初始化收尾失败: " + _error_text(close_error))
        errors = self.open_errors.copy()
        info = PerformanceInfo(cb=c.sizeof(PerformanceInfo))
        get_info = bind(self.psapi, "GetPerformanceInfo", [c.POINTER(PerformanceInfo), w.DWORD], w.BOOL)
        check(get_info(c.byref(info), c.sizeof(info)))
        if not info.PageSize or info.PhysicalAvailable > info.PhysicalTotal or info.CommitTotal > info.CommitLimit:
            raise OSError("主机内存返回值无效")
        page, available = info.PageSize, info.PhysicalAvailable * info.PageSize
        cache = {"system_cache_composite_bytes": info.SystemCache * page}
        try:
            capacity, rates = self.counters.collect(errors)
        except OSError as error:
            capacity, rates = dict.fromkeys(COUNTERS), dict.fromkeys(RATE_COUNTERS)
            errors.append("PDH读取失败: " + _error_text(error))
        cache.update(capacity)
        try:
            pagefile = read_pagefiles(self.psapi, page)
        except (OSError, AttributeError) as error:
            pagefile = None
            errors.append("页面文件读取失败: " + _error_text(error))
        metrics = {"physical": {"total_bytes": info.PhysicalTotal * page, "available_bytes": available,
                                "used_bytes": info.PhysicalTotal * page - available},
                   "commit": {"used_bytes": info.CommitTotal * page, "limit_bytes": info.CommitLimit * page,
                              "peak_bytes": info.CommitPeak * page}, "cache": cache, "rates": rates,
                   "pagefile": pagefile, "cpu": _read_cpu(self.kernel, errors)}
        processes = []
        if self.with_processes:
            try:
                processes, metrics["process_coverage"] = _read_processes(
                    self.kernel, self.psapi, errors, self.pending_handles, self.unknown_handles)
            except (OSError, AttributeError) as error:
                metrics["process_coverage"] = {"enumerated": None, "readable": None,
                                               "scope": "enumerated_processes", "coverage": "unknown"}
                errors.append("进程读取失败: " + _error_text(error))
        return Sample(run_id, sequence, self.backend._now(), metrics, processes, errors)

    def close(self):
        if self.closed:
            return {"closed": True, "errors": []}
        errors = []
        try:
            self.counters.close()
        except OSError as error:
            errors.append(_error_text(error))
        for handle in list(self.pending_handles):
            if handle in self.unknown_handles:
                errors.append("进程句柄关闭结果未知，不能重试关闭")
                continue
            self.unknown_handles.add(handle)
            closed = bind(self.kernel, "CloseHandle", [w.HANDLE], w.BOOL)(handle)
            if closed:
                self.pending_handles.remove(handle)
            self.unknown_handles.remove(handle)
            try:
                check(closed)
            except OSError as error:
                errors.append("进程句柄关闭失败: " + _error_text(error))
        self.closed = not errors
        return {"closed": self.closed, "errors": errors}


def _privilege_luid(advapi):
    luid = Luid()
    lookup = bind(advapi, "LookupPrivilegeValueW", [w.LPCWSTR, w.LPCWSTR, c.POINTER(Luid)], w.BOOL)
    check(lookup(None, PRIVILEGE, c.byref(luid)))
    return luid


def _open_token(kernel, advapi, access):
    current = bind(kernel, "GetCurrentProcess", [], w.HANDLE)()
    token = w.HANDLE()
    opener = bind(advapi, "OpenProcessToken", [w.HANDLE, w.DWORD, c.POINTER(w.HANDLE)], w.BOOL)
    check(opener(current, access, c.byref(token)))
    return token


def _privilege_attributes(advapi, token, luid):
    get_info = bind(advapi, "GetTokenInformation",
                    [w.HANDLE, c.c_int, c.c_void_p, w.DWORD, c.POINTER(w.DWORD)], w.BOOL)
    size = w.DWORD()
    get_info(token, 3, None, 0, c.byref(size))
    if not c.sizeof(w.DWORD) <= size.value <= 1024 * 1024:
        raise OSError("令牌特权缓冲区长度无效")
    buffer = (c.c_byte * size.value)()
    check(get_info(token, 3, buffer, size.value, c.byref(size)))
    count = w.DWORD.from_buffer(buffer).value
    offset = TokenPrivilegesOne.Privileges.offset
    if count > (len(buffer) - offset) // c.sizeof(LuidAttributes):
        raise OSError("令牌特权条目超出缓冲区")
    entries = (LuidAttributes * count).from_buffer(buffer, offset)
    for entry in entries:
        if (entry.Luid.LowPart, entry.Luid.HighPart) == (luid.LowPart, luid.HighPart):
            return int(entry.Attributes)
    return None


def _adjust_privilege(advapi, token, state):
    adjust = bind(advapi, "AdjustTokenPrivileges",
                  [w.HANDLE, w.BOOL, c.c_void_p, w.DWORD, c.c_void_p, c.POINTER(w.DWORD)], w.BOOL)
    previous, size = TokenPrivilegesOne(), w.DWORD(c.sizeof(TokenPrivilegesOne))
    c.set_last_error(0)
    ok = adjust(token, False, c.byref(state), c.sizeof(previous), c.byref(previous), c.byref(size))
    error = c.get_last_error()
    if not ok or error:
        raise OSError(f"AdjustTokenPrivileges失败，winerror {error}")


class _Operation:
    def __init__(self, backend, request):
        self.backend, self.request = backend, request
        self.kernel = self.advapi = self.nt_call = None
        self.handle = None
        self.tokens = []
        self._unknown_handles = []
        self._release_unknown = False
        self.owned = self.local_owned = self.consumed = self.closed = False
        self.owner_thread = None
        self._preflight = None
        self._restore_failed = False

    def preflight(self):
        if self.closed:
            raise RuntimeError("维护会话已关闭")
        if self._preflight is not None:
            return dict(self._preflight)
        result = {"supported": False, "privilege_available": False, "lock_acquired": False,
                  "scope": "system", "lock_scope": "windows_session", "errors": []}
        self._preflight = result
        if not _maintenance_lock.acquire(blocking=False):
            result["errors"].append("当前进程另有维护或安全上下文待核对")
            return dict(result)
        self.local_owned = True
        self.owner_thread = threading.get_ident()
        try:
            self.kernel, self.advapi = self.backend._dll("kernel32.dll"), self.backend._dll("advapi32.dll")
            ntdll = self.backend._dll("ntdll.dll")
            self.nt_call = bind(ntdll, "NtSetSystemInformation", [c.c_int, c.c_void_p, w.ULONG], c.c_long)
            result["supported"] = True
            create = bind(self.kernel, "CreateMutexW", [c.c_void_p, w.BOOL, w.LPCWSTR], w.HANDLE)
            self.handle = create(None, False, MUTEX_NAME)
            check(self.handle)
            status = bind(self.kernel, "WaitForSingleObject", [w.HANDLE, w.DWORD], w.DWORD)(self.handle, 0)
            self.owned = status in (0, 0x80)
            if status != 0:
                raise OSError("维护互斥被遗弃，需要核对" if status == 0x80 else "维护互斥忙或不可用")
            result["lock_acquired"] = True
            token = _open_token(self.kernel, self.advapi, TOKEN_QUERY)
            self.tokens.append(token)
            try:
                attrs = _privilege_attributes(self.advapi, token, _privilege_luid(self.advapi))
            finally:
                self._close_token(token, result["errors"])
            if not result["errors"]:
                result["privilege_available"] = attrs is not None and not bool(attrs & SE_PRIVILEGE_REMOVED)
        except (OSError, AttributeError) as error:
            result["errors"].append(_error_text(error))
        return dict(result)

    def _close_token(self, token, errors):
        if token in self._unknown_handles:
            errors.append("令牌关闭结果未知，不能重试关闭")
            return
        try:
            self._unknown_handles.append(token)
            closed = bind(self.kernel, "CloseHandle", [w.HANDLE], w.BOOL)(token)
            if closed:
                self.tokens.remove(token)
            self._unknown_handles.remove(token)
            check(closed)
        except (OSError, AttributeError) as error:
            errors.append("令牌关闭失败: " + _error_text(error))

    def perform_once(self):
        if self.consumed or self.closed:
            raise RuntimeError("维护会话只允许一次派发")
        self.consumed = True
        result = {"attempted": False, "call_outcome": "not_started", "status": None,
                  "privilege_restored": True, "errors": []}
        admission = self.preflight()
        if not all(admission.get(key) is True for key in ("supported", "privilege_available", "lock_acquired")):
            result["errors"] = list(admission["errors"]) or ["维护准入失败"]
            return result
        if threading.get_ident() != self.owner_thread:
            result["errors"].append("维护必须在取得互斥的线程执行")
            return result
        token, original, luid, adjusted = None, None, None, False
        interruption = None
        try:
            token = _open_token(self.kernel, self.advapi, TOKEN_QUERY | TOKEN_ADJUST_PRIVILEGES)
            self.tokens.append(token)
            luid = _privilege_luid(self.advapi)
            original = _privilege_attributes(self.advapi, token, luid)
            if original is None or original & SE_PRIVILEGE_REMOVED:
                raise OSError("当前令牌不持有所需特权")
            if not original & SE_PRIVILEGE_ENABLED:
                state = TokenPrivilegesOne(1, (LuidAttributes * 1)(LuidAttributes(luid, SE_PRIVILEGE_ENABLED)))
                adjusted = True
                _adjust_privilege(self.advapi, token, state)
                enabled = _privilege_attributes(self.advapi, token, luid)
                if enabled is None or not enabled & SE_PRIVILEGE_ENABLED:
                    raise OSError("无法确认特权启用")
            command = c.c_int(COMMANDS[self.request.mechanism])
            try:
                status = self.nt_call(80, c.byref(command), c.sizeof(command))
            except Exception as error:
                result.update(attempted=None, call_outcome="unknown")
                result["errors"].append("原生调用结果未知: " + _error_text(error))
            else:
                if type(status) is not int:
                    result.update(attempted=True, call_outcome="unknown")
                    result["errors"].append("原生调用未返回有效状态")
                else:
                    status &= 0xffffffff
                    result.update(attempted=True, status=status,
                                  call_outcome="success" if status == 0 else "failure")
        except (OSError, AttributeError) as error:
            result["errors"].append(_error_text(error))
        except BaseException as error:
            interruption = error
        finally:
            if token is not None:
                if adjusted:
                    try:
                        state = TokenPrivilegesOne(1, (LuidAttributes * 1)(LuidAttributes(luid, original)))
                        _adjust_privilege(self.advapi, token, state)
                        if _privilege_attributes(self.advapi, token, luid) != original:
                            raise OSError("原特权状态未恢复")
                    except BaseException as error:
                        self._restore_failed = True
                        result["privilege_restored"] = False
                        result["errors"].append("特权恢复失败: " + _error_text(error))
                        if not isinstance(error, (OSError, AttributeError)) and interruption is None:
                            interruption = error
                elif original is not None:
                    try:
                        if _privilege_attributes(self.advapi, token, luid) != original:
                            raise OSError("调用后原有特权状态无法确认")
                    except BaseException as error:
                        self._restore_failed = True
                        result["privilege_restored"] = False
                        result["errors"].append("特权状态核对失败: " + _error_text(error))
                        if not isinstance(error, (OSError, AttributeError)) and interruption is None:
                            interruption = error
                try:
                    self._close_token(token, result["errors"])
                except BaseException as error:
                    result["errors"].append("令牌关闭失败: " + _error_text(error))
                    if interruption is None:
                        interruption = error
        if interruption is not None:
            raise interruption
        return result

    def close(self):
        self.consumed = True
        if self.closed:
            return {"closed": not self._restore_failed, "errors": []}
        errors = []
        if self.owned and threading.get_ident() != self.owner_thread:
            return {"closed": False, "errors": ["必须由取得互斥的线程关闭维护会话"]}
        for token in list(self.tokens):
            self._close_token(token, errors)
        if self.owned:
            if self._release_unknown:
                errors.append("互斥释放结果未知，不能重试释放")
            else:
                try:
                    self._release_unknown = True
                    released = bind(self.kernel, "ReleaseMutex", [w.HANDLE], w.BOOL)(self.handle)
                    if released:
                        self.owned = False
                    self._release_unknown = False
                    check(released)
                except OSError as error:
                    errors.append("互斥释放失败: " + _error_text(error))
        if self.handle is not None and not self.owned:
            if self.handle in self._unknown_handles:
                errors.append("互斥句柄关闭结果未知，不能重试关闭")
            else:
                try:
                    handle = self.handle
                    self._unknown_handles.append(handle)
                    closed = bind(self.kernel, "CloseHandle", [w.HANDLE], w.BOOL)(handle)
                    if closed:
                        self.handle = None
                    self._unknown_handles.remove(handle)
                    check(closed)
                except OSError as error:
                    errors.append("互斥句柄关闭失败: " + _error_text(error))
        if self._restore_failed:
            errors.append("维护后的资源或安全上下文未确认，阻止后续维护")
        if not errors and self.local_owned:
            _maintenance_lock.release()
            self.local_owned = False
        self.closed = not errors
        return {"closed": self.closed, "errors": errors}
