"""显式请求的单目标工作集能力；导入不加载DLL或枚举进程。"""

import ctypes as c
from ctypes import wintypes as w
import ntpath

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
    def __init__(self, loader, target, *, writable=True):
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

    def read(self):
        self.verify()
        return {"pid": self.target.pid, "created_at": self.target.created_at,
                "working_set_bytes": working_set(self.psapi, self.handle)}

    def perform_once(self):
        if self.consumed or self.close_attempted:
            raise RuntimeError("目标会话仅允许一次派发")
        self.consumed = True
        self.verify()
        ok = bind(self.psapi, "EmptyWorkingSet", [w.HANDLE], w.BOOL)(self.handle)
        return {"attempted": True, "call_outcome": "success" if ok else "failure",
                "status": 0 if ok else c.get_last_error(), "privilege_restored": True,
                "errors": [] if ok else ["目标工作集整理失败"]}

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


def targets(loader):
    kernel, psapi = loader("kernel32.dll"), loader("psapi.dll")
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
    rows, errors = [], []
    for pid in ids[:used.value // c.sizeof(w.DWORD)]:
        if not pid:
            continue
        # 发现阶段尚无创建身份，先只读打开，再由同一句柄取得身份与名称。
        handle = bind(kernel, "OpenProcess", [w.DWORD, w.BOOL, w.DWORD], w.HANDLE)(QUERY, False, pid)
        if not handle:
            errors.append(f"进程 {pid} 不可读或已退出")
            continue
        try:
            name, length = c.create_unicode_buffer(32768), w.DWORD(32768)
            checked(bind(kernel, "QueryFullProcessImageNameW", [w.HANDLE, w.DWORD, w.LPWSTR,
                    c.POINTER(w.DWORD)], w.BOOL)(handle, 0, name, c.byref(length)))
            target = ProcessTarget(int(pid), identity(kernel, handle), ntpath.basename(name.value))
            rows.append({"pid": target.pid, "created_at": target.created_at, "name": target.name,
                         "working_set_bytes": working_set(psapi, handle)})
        except (OSError, ValueError) as error:
            errors.append(f"进程 {pid}: {error}")
        finally:
            try:
                checked(bind(kernel, "CloseHandle", [w.HANDLE], w.BOOL)(handle))
            except BaseException as error:
                raise ResourceCloseError("目标发现句柄关闭未确认，停止读取") from error
    return rows, errors
