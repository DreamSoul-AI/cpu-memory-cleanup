"""Read-only Windows bindings. No process writes or privilege changes."""

import ctypes as c
from ctypes import wintypes as w
from datetime import datetime, timezone
import ntpath
import sys


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
    "standby_core_bytes": "Standby Cache Core Bytes",
    "standby_normal_bytes": "Standby Cache Normal Priority Bytes",
    "standby_reserve_bytes": "Standby Cache Reserve Bytes",
    "modified_bytes": "Modified Page List Bytes",
    "free_zero_bytes": "Free & Zero Page List Bytes",
    "cache_bytes": "Cache Bytes",
    "system_cache_resident_bytes": "System Cache Resident Bytes",
}
STANDBY_KEYS = ("standby_core_bytes", "standby_normal_bytes", "standby_reserve_bytes")
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def bind(dll, name, args, result):
    function = getattr(dll, name)
    function.argtypes = args
    function.restype = result
    return function


def check(ok):
    if not ok:
        raise c.WinError(c.get_last_error())


def read_counters(warnings):
    values = dict.fromkeys(COUNTERS)
    pdh = c.WinDLL("pdh.dll", use_last_error=True)
    open_query = bind(pdh, "PdhOpenQueryW", [w.LPCWSTR, c.c_size_t, c.POINTER(w.HANDLE)], w.LONG)
    add = bind(pdh, "PdhAddEnglishCounterW", [w.HANDLE, w.LPCWSTR, c.c_size_t, c.POINTER(w.HANDLE)], w.LONG)
    collect = bind(pdh, "PdhCollectQueryData", [w.HANDLE], w.LONG)
    get = bind(pdh, "PdhGetFormattedCounterValue", [w.HANDLE, w.DWORD, c.POINTER(w.DWORD), c.POINTER(CounterValue)], w.LONG)
    close = bind(pdh, "PdhCloseQuery", [w.HANDLE], w.LONG)
    query = w.HANDLE()
    status = open_query(None, 0, c.byref(query))
    if status:
        warnings.append(f"PDH open unavailable: 0x{status & 0xffffffff:08x}")
        return values
    try:
        handles = {}
        for key, counter in COUNTERS.items():
            handle = w.HANDLE()
            status = add(query, "\\Memory\\" + counter, 0, c.byref(handle))
            if status:
                warnings.append(f"{key} unavailable: PDH 0x{status & 0xffffffff:08x}")
            else:
                handles[key] = handle
        status = collect(query)
        if status:
            warnings.append(f"PDH collection unavailable: 0x{status & 0xffffffff:08x}")
            return values
        for key, handle in handles.items():
            value = CounterValue()
            status = get(handle, 0x400, None, c.byref(value))  # PDH_FMT_LARGE
            if status == 0 and value.CStatus in (0, 1) and value.value.largeValue >= 0:
                values[key] = value.value.largeValue
            else:
                warnings.append(f"{key} unavailable: PDH 0x{status & 0xffffffff:08x}, CStatus 0x{value.CStatus:08x}")
        return values
    finally:
        close(query)


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


def snapshot(top=10):
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
    try:
        cache.update(read_counters(warnings))
    except OSError as exc:
        cache.update(dict.fromkeys(COUNTERS))
        warnings.append(f"PDH unavailable: {exc}")
    try:
        pagefile = read_pagefiles(psapi, page)
    except OSError as exc:
        pagefile = None
        warnings.append(f"Pagefile usage unavailable: {exc}")
    try:
        processes = read_processes(kernel, psapi, top)
        if processes["skipped"]:
            warnings.append("Process ranking is incomplete: access denied, protected or exited processes were skipped.")
    except OSError as exc:
        processes = None
        warnings.append(f"Process ranking unavailable: {exc}")
    available = info.PhysicalAvailable * page
    return {
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
