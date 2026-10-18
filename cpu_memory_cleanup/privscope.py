"""Scoped enable of one already-held privilege, only around an explicit --release.

Authorised by Enmao on 2026-10-19 (Asia/Shanghai): during an explicit
``--release`` the tool may turn on SeProfileSingleProcessPrivilege in its OWN
process token if, and only if, that token already holds it (held but disabled),
and must turn it off again immediately after the release calls. This module
never adds a privilege the token does not hold, never touches any other
privilege, process or token, and never elevates (no UAC prompt, no new
process). If the token does not hold the privilege, AdjustTokenPrivileges
reports ERROR_NOT_ALL_ASSIGNED, nothing is enabled, and the release calls
return their NTSTATUS as before. Tests inject a fake adjuster and never touch
the real token.
"""

import contextlib
import ctypes as c
from ctypes import wintypes as w
import sys

PRIVILEGE = "SeProfileSingleProcessPrivilege"
TOKEN_QUERY = 0x0008
TOKEN_ADJUST_PRIVILEGES = 0x0020
SE_PRIVILEGE_ENABLED = 0x00000002
ERROR_NOT_ALL_ASSIGNED = 1300


class LUID(c.Structure):
    _fields_ = [("LowPart", w.DWORD), ("HighPart", w.LONG)]


class TOKEN_PRIVILEGES_ONE(c.Structure):
    _fields_ = [("PrivilegeCount", w.DWORD), ("Luid", LUID), ("Attributes", w.DWORD)]


def _default_adjust(enable):
    """Set PRIVILEGE on/off in this process token; return whether it was enabled before."""
    if sys.platform != "win32":
        raise OSError("Windows is required; no privilege was changed.")
    advapi = c.WinDLL("advapi32", use_last_error=True)
    kernel = c.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = w.HANDLE
    kernel.CloseHandle.argtypes = [w.HANDLE]
    advapi.OpenProcessToken.argtypes = [w.HANDLE, w.DWORD, c.POINTER(w.HANDLE)]
    advapi.OpenProcessToken.restype = w.BOOL
    advapi.LookupPrivilegeValueW.argtypes = [w.LPCWSTR, w.LPCWSTR, c.POINTER(LUID)]
    advapi.LookupPrivilegeValueW.restype = w.BOOL
    advapi.AdjustTokenPrivileges.argtypes = [w.HANDLE, w.BOOL, c.c_void_p, w.DWORD, c.c_void_p, c.POINTER(w.DWORD)]
    advapi.AdjustTokenPrivileges.restype = w.BOOL
    luid = LUID()
    if not advapi.LookupPrivilegeValueW(None, PRIVILEGE, c.byref(luid)):
        raise OSError(f"LookupPrivilegeValueW({PRIVILEGE}) failed, winerror {c.get_last_error()}")
    token = w.HANDLE()
    if not advapi.OpenProcessToken(kernel.GetCurrentProcess(), TOKEN_ADJUST_PRIVILEGES | TOKEN_QUERY, c.byref(token)):
        raise OSError(f"OpenProcessToken(own token) failed, winerror {c.get_last_error()}")
    try:
        new = TOKEN_PRIVILEGES_ONE(1, luid, SE_PRIVILEGE_ENABLED if enable else 0)
        prev = TOKEN_PRIVILEGES_ONE()
        size = w.DWORD(c.sizeof(prev))
        c.set_last_error(0)
        ok = advapi.AdjustTokenPrivileges(token, False, c.byref(new), c.sizeof(new), c.byref(prev), c.byref(size))
        err = c.get_last_error()
        if not ok:
            raise OSError(f"AdjustTokenPrivileges failed, winerror {err}; no elevation attempted")
        if err == ERROR_NOT_ALL_ASSIGNED:
            raise OSError(f"{PRIVILEGE} is not held by this token (ERROR_NOT_ALL_ASSIGNED); "
                          "nothing was enabled and no elevation was attempted")
        if prev.PrivilegeCount == 0:  # already in the requested state
            return bool(enable)
        return bool(prev.Attributes & SE_PRIVILEGE_ENABLED)
    finally:
        kernel.CloseHandle(token)


@contextlib.contextmanager
def scoped_enable(adjust=None):
    """Enable PRIVILEGE for the body only; disable it again afterwards if we enabled it.

    Yields a record dict. Failures are recorded, never raised, and never retried.
    """
    adjust = _default_adjust if adjust is None else adjust
    record = {"privilege": PRIVILEGE, "was_enabled": None, "enabled_by_tool": False,
              "restored": None, "error": None, "restore_error": None}
    try:
        was = bool(adjust(True))
        record["was_enabled"] = was
        record["enabled_by_tool"] = not was
    except Exception as exc:
        record["error"] = f"{type(exc).__name__}: {exc}"
    try:
        yield record
    finally:
        if record["enabled_by_tool"]:
            try:
                adjust(False)
                record["restored"] = True
            except Exception as exc:
                record["restored"] = False
                record["restore_error"] = f"{type(exc).__name__}: {exc}"


def describe(record):
    if record is None:
        return "not used (injected NT call)"
    if record["error"]:
        return f"enable failed: {record['error']}"
    if record["was_enabled"]:
        return "already enabled before the release; left as found"
    if record["restored"]:
        return "enabled only for the release calls, then disabled again"
    return f"enabled for the release calls; DISABLE FAILED: {record['restore_error']}"
