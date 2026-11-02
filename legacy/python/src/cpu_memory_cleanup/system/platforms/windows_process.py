"""显式请求的单目标工作集能力；导入不加载DLL或枚举进程。"""

import ctypes as c
from ctypes import wintypes as w
import ntpath
from contextlib import nullcontext

from ...contracts import ProcessTarget, ResourceCloseError

QUERY = 0x1000
SET_QUOTA = 0x0100


class MemoryInfo(c.Structure):
    _fields_ = [("cb", w.DWORD), ("PageFaultCount", w.DWORD)] + [
        (name, c.c_size_t) for name in ("PeakWorkingSetSize", "WorkingSetSize",
        "QuotaPeakPagedPoolUsage", "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage",
        "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage", "PrivateUsage")]


def bind(dll, name, arguments, result):
    function = getattr(dll, name)
    function.argtypes, function.restype = arguments, result
    return function


def checked(value):
    if not value:
        raise OSError(c.get_last_error(), "目标进程操作失败")
    return value


class TargetSession:
    """唯一拥有目标句柄；关闭结果未知时不重复关闭。"""
    def __init__(self, loader, target, *, writable=True, method="EmptyWorkingSet"):
        if method not in ("EmptyWorkingSet", "SetProcessWorkingSetSize"):
            raise ValueError("未知单目标工作集接口")
        self.method = method
        self.kernel, self.psapi = loader("kernel32.dll"), loader("psapi.dll")
        self.target, self.handle, self.close_attempted = target, None, False
        self.consumed = False
        rights = QUERY | SET_QUOTA if writable else QUERY
        self.handle = checked(bind(self.kernel, "OpenProcess", [w.DWORD, w.BOOL, w.DWORD], w.HANDLE)(
            rights, False, target.pid))

    def identity(self):
        return identity(self.kernel, self.handle)

    def verify(self):
        if self.identity() != self.target.created_at:
            raise ValueError("目标创建身份已改变，未执行整理")
        if self.target.image_path:
            expected = ntpath.normcase(ntpath.normpath(self.target.image_path))
            if ntpath.normcase(ntpath.normpath(image_path(self.kernel, self.handle))) != expected:
                raise ValueError("目标程序路径已改变，未执行整理")

    def read(self):
        self.verify()
        return {"pid": self.target.pid, "created_at": self.target.created_at,
                "working_set_bytes": working_set(self.psapi, self.handle)}

    def perform_once(self):
        if self.consumed or self.close_attempted:
            raise RuntimeError("目标会话仅允许一次派发")
        self.consumed = True
        timings = getattr(self, "timings", None)
        with timings.measure("target_verify") if timings is not None else nullcontext():
            self.verify()
        with timings.measure("native_call") if timings is not None else nullcontext():
            if self.method == "SetProcessWorkingSetSize":
                flush = c.c_size_t(-1).value
                ok = bind(self.kernel, self.method, [w.HANDLE, c.c_size_t, c.c_size_t], w.BOOL)(
                    self.handle, flush, flush)
            else:
                ok = bind(self.psapi, self.method, [w.HANDLE], w.BOOL)(self.handle)
        if timings is not None:
            timings.mark("native_returned")
        return {"attempted": True, "call_outcome": "success" if ok else "failure",
                "status": 0 if ok else c.get_last_error(), "privilege_restored": True,
                "errors": [] if ok else ["目标工作集整理失败"], "native_method": self.method}

    def close(self):
        if self.handle is None:
            return {"closed": True, "errors": []}
        if self.close_attempted:
            return {"closed": False, "errors": ["目标句柄关闭未确认，不重复关闭"]}
        self.close_attempted = True
        ok = bind(self.kernel, "CloseHandle", [w.HANDLE], w.BOOL)(self.handle)
        if ok:
            self.handle = None
        return {"closed": bool(ok), "errors": [] if ok else ["目标句柄关闭失败"]}


def identity(kernel, handle):
    created, exited, kern, user = (w.FILETIME() for _ in range(4))
    checked(bind(kernel, "GetProcessTimes", [w.HANDLE] + [c.POINTER(w.FILETIME)] * 4,
                 w.BOOL)(handle, c.byref(created), c.byref(exited), c.byref(kern), c.byref(user)))
    return str((created.dwHighDateTime << 32) | created.dwLowDateTime)


def working_set(psapi, handle):
    info = MemoryInfo(cb=c.sizeof(MemoryInfo))
    checked(bind(psapi, "GetProcessMemoryInfo", [w.HANDLE, c.POINTER(MemoryInfo), w.DWORD],
                 w.BOOL)(handle, c.byref(info), c.sizeof(info)))
    return int(info.WorkingSetSize)


def image_path(kernel, handle):
    name, length = c.create_unicode_buffer(32768), w.DWORD(32768)
    checked(bind(kernel, "QueryFullProcessImageNameW", [w.HANDLE, w.DWORD, w.LPWSTR,
            c.POINTER(w.DWORD)], w.BOOL)(handle, 0, name, c.byref(length)))
    return name.value


def process_ids(loader):
    """一次有界枚举，不打开目标或执行维护。"""
    psapi = loader("psapi.dll")
    capacity = 1024
    enum = bind(psapi, "EnumProcesses", [c.POINTER(w.DWORD), w.DWORD, c.POINTER(w.DWORD)], w.BOOL)
    while capacity <= 131072:
        ids, used = (w.DWORD * capacity)(), w.DWORD()
        checked(enum(ids, c.sizeof(ids), c.byref(used)))
        if used.value > c.sizeof(ids) or used.value % c.sizeof(w.DWORD):
            raise OSError("进程枚举长度无效")
        if used.value < c.sizeof(ids):
            break
        capacity *= 2
    else:
        raise OSError("进程枚举超过上限")
    return list(dict.fromkeys(int(pid) for pid in ids[:used.value // c.sizeof(w.DWORD)] if pid))


def sweep_working_sets(loader, timings=None):
    """一轮逐进程裁剪；不提权、不结束进程、不回退全局NT。"""
    kernel = loader("kernel32.dll")
    measure = timings.measure if timings is not None else lambda _: nullcontext()
    with measure("process_enumeration"):
        ids = process_ids(loader)
    sweep = {"enumerated_count": len(ids), "visited_count": 0, "attempted_count": 0,
             "successful_count": 0, "failed_count": 0, "unknown_count": 0, "skipped_count": 0,
             "resources_closed": True, "items": [], "omitted_items_count": 0}
    errors, first_failure = [], None
    trim = bind(kernel, "SetProcessWorkingSetSize", [w.HANDLE, c.c_size_t, c.c_size_t], w.BOOL)
    flush = c.c_size_t(-1).value
    for pid in ids:
        row = {"pid": pid, "outcome": "skipped"}
        with measure("target_open"):
            handle = bind(kernel, "OpenProcess", [w.DWORD, w.BOOL, w.DWORD], w.HANDLE)(
                QUERY | SET_QUOTA, False, pid)
        if not handle:
            row["status"] = c.get_last_error()
            sweep["skipped_count"] += 1
        else:
            try:
                try:
                    with measure("target_verify"):
                        row["created_at"] = identity(kernel, handle)
                        if identity(kernel, handle) != row["created_at"]:
                            raise ValueError("当前句柄的创建身份核验不一致")
                except (OSError, ValueError) as error:
                    row["error"] = str(error)
                    sweep["skipped_count"] += 1
                else:
                    sweep["attempted_count"] += 1
                    try:
                        with measure("native_call"):
                            ok = trim(handle, flush, flush)
                            status = 0 if ok else c.get_last_error()
                        if timings is not None:
                            timings.mark("native_returned")
                    except Exception as error:
                        row.update(outcome="unknown", error=str(error))
                        sweep["unknown_count"] += 1
                    else:
                        row.update(outcome="success" if ok else "failure", status=status)
                        sweep["successful_count" if ok else "failed_count"] += 1
                        if not ok and first_failure is None:
                            first_failure = status
            finally:
                try:
                    with measure("target_close"):
                        checked(bind(kernel, "CloseHandle", [w.HANDLE], w.BOOL)(handle))
                except Exception as error:
                    row["close_error"] = str(error)
                    sweep["resources_closed"] = False
        sweep["visited_count"] += 1
        if len(sweep["items"]) < 1024:
            sweep["items"].append(row)
        else:
            sweep["omitted_items_count"] += 1
        if sweep["unknown_count"] or not sweep["resources_closed"]:
            break
    if sweep["failed_count"]:
        errors.append(f"{sweep['failed_count']}个进程工作集调用失败，逐项状态见process_sweep")
    if sweep["unknown_count"]:
        errors.append("目标原生调用结果未知，已停止遍历，不重试")
    if not sweep["resources_closed"]:
        errors.append("目标句柄关闭未确认，已停止遍历，不重复关闭")
    if not sweep["attempted_count"]:
        errors.append("没有可派发工作集整理的目标")
    outcome = ("unknown" if sweep["unknown_count"] else "not_started" if not sweep["attempted_count"]
               else "failure" if sweep["failed_count"] else "success")
    sweep["coverage"] = "partial" if (not sweep["resources_closed"] or sweep["skipped_count"] or sweep["failed_count"] or
        sweep["unknown_count"] or sweep["visited_count"] < len(ids)) else "observed_only"
    return {"attempted": None if sweep["unknown_count"] else bool(sweep["attempted_count"]),
            "call_outcome": outcome, "status": first_failure if first_failure is not None else 0,
            "privilege_restored": True, "errors": errors,
            "native_method": "SetProcessWorkingSetSize", "process_sweep": sweep}


def targets(loader):
    kernel, psapi = loader("kernel32.dll"), loader("psapi.dll")
    rows, errors = [], []
    for pid in process_ids(loader):
        # 发现阶段尚无创建身份，先只读打开，再由同一句柄取得身份与名称。
        handle = bind(kernel, "OpenProcess", [w.DWORD, w.BOOL, w.DWORD], w.HANDLE)(QUERY, False, pid)
        if not handle:
            errors.append(f"进程 {pid} 不可读或已退出")
            continue
        try:
            path = image_path(kernel, handle)
            target = ProcessTarget(int(pid), identity(kernel, handle), ntpath.basename(path), path)
            rows.append({"pid": target.pid, "created_at": target.created_at, "name": target.name,
                         "image_path": path, "working_set_bytes": working_set(psapi, handle)})
        except (OSError, ValueError) as error:
            errors.append(f"进程 {pid}: {error}")
        finally:
            try:
                checked(bind(kernel, "CloseHandle", [w.HANDLE], w.BOOL)(handle))
            except BaseException as error:
                raise ResourceCloseError("目标发现句柄关闭未确认，停止读取") from error
    return rows, errors
