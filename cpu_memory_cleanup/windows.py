"""Read-only Windows bindings. No process writes or privilege changes."""

import ctypes as c
from ctypes import wintypes as w
from datetime import datetime, timezone
import ntpath
import sys
import time
import uuid


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
STANDBY_KEYS = ("standby_core_bytes", "standby_normal_bytes", "standby_reserve_bytes")
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PDH_FMT_DOUBLE = 0x200
PDH_FMT_LARGE = 0x400
PDH_CSTATUS_INVALID_DATA = 0xC0000BBA
PDH_INVALID_DATA = 0xC0000BC6
MIN_INTERVAL_SECONDS = 1
MAX_INTERVAL_SECONDS = 3600
MAX_SAMPLE_COUNT = 10000


def bind(dll, name, args, result):
    function = getattr(dll, name)
    function.argtypes = args
    function.restype = result
    return function


def check(ok):
    if not ok:
        raise c.WinError(c.get_last_error())


class CounterSession:
    """One PDH query reused across samples. Close always releases the query."""

    def __init__(self, include_rates=False):
        self.include_rates = include_rates
        self.ready = False
        self._handles = {}
        self._formats = {}
        self._query = None
        self._collect = None
        self._get = None
        self._close = None
        self._warmup_noted = set()

    def open(self, warnings):
        specs = dict(COUNTERS)
        if self.include_rates:
            specs.update(RATE_COUNTERS)
        pdh = c.WinDLL("pdh.dll", use_last_error=True)
        open_query = bind(pdh, "PdhOpenQueryW", [w.LPCWSTR, c.c_size_t, c.POINTER(w.HANDLE)], w.LONG)
        add = bind(pdh, "PdhAddEnglishCounterW", [w.HANDLE, w.LPCWSTR, c.c_size_t, c.POINTER(w.HANDLE)], w.LONG)
        self._collect = bind(pdh, "PdhCollectQueryData", [w.HANDLE], w.LONG)
        self._get = bind(pdh, "PdhGetFormattedCounterValue",
                         [w.HANDLE, w.DWORD, c.POINTER(w.DWORD), c.POINTER(CounterValue)], w.LONG)
        self._close = bind(pdh, "PdhCloseQuery", [w.HANDLE], w.LONG)
        query = w.HANDLE()
        status = open_query(None, 0, c.byref(query))
        if status:
            warnings.append(f"PDH open unavailable: 0x{status & 0xffffffff:08x}")
            return False
        self._query = query
        self.ready = True
        for key, path in specs.items():
            handle = w.HANDLE()
            status = add(query, path, 0, c.byref(handle))
            if status:
                warnings.append(f"{key} unavailable: PDH 0x{status & 0xffffffff:08x}")
            else:
                self._handles[key] = handle
                self._formats[key] = PDH_FMT_DOUBLE if key in RATE_COUNTERS else PDH_FMT_LARGE
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
        for key, handle in self._handles.items():
            value = CounterValue()
            status = self._get(handle, self._formats[key], None, c.byref(value))
            if key in RATE_COUNTERS and (
                    status == PDH_INVALID_DATA or value.CStatus == PDH_CSTATUS_INVALID_DATA):
                if key not in self._warmup_noted:
                    self._warmup_noted.add(key)
                    warnings.append(f"{key} unavailable until the next sample: no previous PDH interval")
                continue
            if key in RATE_COUNTERS:
                reading = value.value.doubleValue
            else:
                reading = value.value.largeValue
            if status == 0 and value.CStatus in (0, 1) and reading >= 0:
                if key in RATE_COUNTERS:
                    rates[key] = reading
                else:
                    capacity[key] = reading
            else:
                warnings.append(
                    f"{key} unavailable: PDH 0x{status & 0xffffffff:08x}, CStatus 0x{value.CStatus:08x}")
        return capacity, rates

    def close(self):
        if self._query is not None and self._close is not None:
            self._close(self._query)
        self._query = None
        self.ready = False


def read_counters(warnings):
    session = CounterSession(include_rates=False)
    if not session.open(warnings):
        return dict.fromkeys(COUNTERS)
    try:
        capacity, _ = session.collect(warnings)
        return capacity
    finally:
        session.close()


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


def read_processes(kernel, psapi, top):
    enum = bind(psapi, "EnumProcesses", [c.POINTER(w.DWORD), w.DWORD, c.POINTER(w.DWORD)], w.BOOL)
    open_process = bind(kernel, "OpenProcess", [w.DWORD, w.BOOL, w.DWORD], w.HANDLE)
    close = bind(kernel, "CloseHandle", [w.HANDLE], w.BOOL)
    memory = bind(psapi, "GetProcessMemoryInfo", [w.HANDLE, c.POINTER(ProcessMemory), w.DWORD], w.BOOL)
    image = bind(kernel, "QueryFullProcessImageNameW", [w.HANDLE, w.DWORD, w.LPWSTR, c.POINTER(w.DWORD)], w.BOOL)
    capacity = 1024
    while True:
        ids = (w.DWORD * capacity)()
        needed = w.DWORD()
        check(enum(ids, c.sizeof(ids), c.byref(needed)))
        if needed.value < c.sizeof(ids):
            break
        capacity *= 2
    processes = []
    skipped = 0
    for pid in ids[:needed.value // c.sizeof(w.DWORD)]:
        handle = open_process(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            skipped += 1
            continue
        try:
            stats = ProcessMemory(cb=c.sizeof(ProcessMemory))
            name = c.create_unicode_buffer(32768)
            length = w.DWORD(len(name))
            if not memory(handle, c.byref(stats), c.sizeof(stats)) or not image(handle, 0, name, c.byref(length)):
                skipped += 1
                continue
            processes.append({"name": ntpath.basename(name.value), "working_set_bytes": stats.WorkingSetSize})
        finally:
            close(handle)
    processes.sort(key=lambda item: item["working_set_bytes"], reverse=True)
    return {"enumerated": needed.value // c.sizeof(w.DWORD), "readable": len(processes),
            "skipped": skipped, "top": processes[:top]}


def estimate(cache, available):
    standby = [cache[key] for key in STANDBY_KEYS]
    upper = min(sum(standby), available) if all(v is not None for v in standby) else None
    return {
        "standby_repurposable_upper_bytes": upper,
        "guaranteed_additional_available_bytes": 0,
        "basis": "0..min(sum(standby core, normal, reserve), available); already included in available. "
                 "Not additional RAM. Missing standby counters => unknown. Excludes process working sets, "
                 "modified pages, compression and pagefile. Non-atomic snapshot; no performance gain guaranteed.",
    }


def snapshot(top=10, *, session=None, include_processes=True):
    if sys.platform != "win32":
        raise OSError("Windows is required; no operations were performed.")
    started = datetime.now(timezone.utc).isoformat()
    kernel = c.WinDLL("kernel32.dll", use_last_error=True)
    psapi = c.WinDLL("psapi.dll", use_last_error=True)
    get_info = bind(psapi, "GetPerformanceInfo", [c.POINTER(PerformanceInfo), w.DWORD], w.BOOL)
    info = PerformanceInfo(cb=c.sizeof(PerformanceInfo))
    check(get_info(c.byref(info), c.sizeof(info)))
    page = info.PageSize
    warnings = []
    cache = {"system_cache_composite_bytes": info.SystemCache * page}
    rates = dict.fromkeys(RATE_COUNTERS) if session is not None else None
    try:
        if session is None:
            cache.update(read_counters(warnings))
        elif session.ready:
            capacity, rate_values = session.collect(warnings)
            cache.update(capacity)
            if rate_values:
                rates.update(rate_values)
        else:
            cache.update(dict.fromkeys(COUNTERS))
    except OSError as exc:
        cache.update(dict.fromkeys(COUNTERS))
        warnings.append(f"PDH unavailable: {exc}")
    try:
        pagefile = read_pagefiles(psapi, page)
    except OSError as exc:
        pagefile = None
        warnings.append(f"Pagefile usage unavailable: {exc}")
    processes = None
    if include_processes:
        try:
            processes = read_processes(kernel, psapi, top)
            if processes["skipped"]:
                warnings.append("Process ranking is incomplete: access denied, protected or exited processes were skipped.")
        except OSError as exc:
            warnings.append(f"Process ranking unavailable: {exc}")
    available = info.PhysicalAvailable * page
    report = {
        "schema_version": 1, "mode": "read-only", "cleanup_performed": False,
        "started_at_utc": started, "finished_at_utc": datetime.now(timezone.utc).isoformat(),
        "physical": {"total_bytes": info.PhysicalTotal * page,
                     "used_bytes": (info.PhysicalTotal - info.PhysicalAvailable) * page,
                     "available_bytes": available},
        "commit": {"used_bytes": info.CommitTotal * page, "limit_bytes": info.CommitLimit * page,
                   "peak_bytes": info.CommitPeak * page},
        "cache": cache, "pagefile": pagefile, "processes": processes,
        "estimate": estimate(cache, available), "warnings": warnings,
    }
    if rates is not None:
        report["rates"] = rates
    return report


def iter_snapshots(count, interval, *, top=10, include_processes=True,
                   sleeper=time.sleep, clock=time.monotonic):
    """Yield a bounded read-only series. The PDH query stays open so rates exist after the first sample."""
    if not 1 <= count <= MAX_SAMPLE_COUNT:
        raise ValueError(f"count must be 1..{MAX_SAMPLE_COUNT}")
    if not MIN_INTERVAL_SECONDS <= interval <= MAX_INTERVAL_SECONDS:
        raise ValueError(f"interval must be {MIN_INTERVAL_SECONDS}..{MAX_INTERVAL_SECONDS} seconds")
    session = CounterSession(include_rates=True)
    open_warnings = []
    session.open(open_warnings)
    overrun = False
    run_id = uuid.uuid4().hex
    try:
        next_tick = clock()
        for index in range(count):
            report = snapshot(top, session=session, include_processes=include_processes)
            report["schema_version"] = 2
            report["series"] = {
                "run_id": run_id,
                "index": index,
                "count": count,
                "interval_seconds": interval,
                "process_ranking": include_processes,
            }
            if index == 0 and open_warnings:
                report["warnings"] = open_warnings + report["warnings"]
            if overrun:
                report["warnings"].insert(
                    0, "Previous interval overrun: sample ran longer than the interval; no catch-up burst was taken.")
                overrun = False
            yield report
            if index + 1 >= count:
                break
            next_tick += interval
            delay = next_tick - clock()
            if delay > 0:
                sleeper(delay)
            else:
                overrun = True
                next_tick = clock()
    finally:
        session.close()
