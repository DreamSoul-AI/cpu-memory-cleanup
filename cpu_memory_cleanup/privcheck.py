"""Read-only privilege pre-flight for --release (2026-10-19).

Opens the current process token with TOKEN_QUERY only and lists which of the
privileges the release mechanisms need are held and enabled. It never enables,
requests or adjusts any privilege and never attempts elevation; it only explains
in advance why a live --release would return STATUS_PRIVILEGE_NOT_HELD.
Tests inject a fake query and never touch the real token.
"""

import ctypes as c
from ctypes import wintypes as w
import sys

TOKEN_QUERY = 0x0008
TOKEN_INFORMATION_CLASS_PRIVILEGES = 3
SE_PRIVILEGE_ENABLED_BY_DEFAULT = 0x00000001
SE_PRIVILEGE_ENABLED = 0x00000002
SE_PRIVILEGE_REMOVED = 0x00000004

# Needed by the system memory-list call behind both v1 release commands.
REQUIRED = ("SeProfileSingleProcessPrivilege",)


class LUID(c.Structure):
    _fields_ = [("LowPart", w.DWORD), ("HighPart", w.LONG)]


class LUID_AND_ATTRIBUTES(c.Structure):
    _fields_ = [("Luid", LUID), ("Attributes", w.DWORD)]


def _default_query():
    """Return [(privilege_name, attributes)] for this process token (read-only)."""
    if sys.platform != "win32":
        raise OSError("Windows is required for the privilege pre-flight.")
    advapi = c.WinDLL("advapi32", use_last_error=True)
    kernel = c.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = w.HANDLE
    kernel.CloseHandle.argtypes = [w.HANDLE]
    advapi.OpenProcessToken.argtypes = [w.HANDLE, w.DWORD, c.POINTER(w.HANDLE)]
    advapi.OpenProcessToken.restype = w.BOOL
    advapi.GetTokenInformation.argtypes = [w.HANDLE, c.c_int, c.c_void_p, w.DWORD, c.POINTER(w.DWORD)]
    advapi.GetTokenInformation.restype = w.BOOL
    advapi.LookupPrivilegeNameW.argtypes = [w.LPCWSTR, c.POINTER(LUID), w.LPWSTR, c.POINTER(w.DWORD)]
    advapi.LookupPrivilegeNameW.restype = w.BOOL
    token = w.HANDLE()
    if not advapi.OpenProcessToken(kernel.GetCurrentProcess(), TOKEN_QUERY, c.byref(token)):
        raise OSError(f"OpenProcessToken(TOKEN_QUERY) failed, winerror {c.get_last_error()}")
    try:
        size = w.DWORD(0)
        advapi.GetTokenInformation(token, TOKEN_INFORMATION_CLASS_PRIVILEGES, None, 0, c.byref(size))
        if size.value < c.sizeof(w.DWORD):
            raise OSError(f"GetTokenInformation size query failed, winerror {c.get_last_error()}")
        buf = (c.c_byte * size.value)()
        if not advapi.GetTokenInformation(token, TOKEN_INFORMATION_CLASS_PRIVILEGES, buf, size, c.byref(size)):
            raise OSError(f"GetTokenInformation failed, winerror {c.get_last_error()}")
        count = w.DWORD.from_buffer(buf).value
        entries = (LUID_AND_ATTRIBUTES * count).from_buffer(buf, c.sizeof(w.DWORD))
        out = []
        for entry in entries:
            luid = LUID(entry.Luid.LowPart, entry.Luid.HighPart)
            length = w.DWORD(128)
            name = c.create_unicode_buffer(length.value)
            if advapi.LookupPrivilegeNameW(None, c.byref(luid), name, c.byref(length)):
                out.append((name.value, int(entry.Attributes)))
        return out
    finally:
        kernel.CloseHandle(token)


def check(query=None, required=REQUIRED):
    """Read-only: report held/enabled for each required privilege."""
    if query is None:
        query = _default_query
    record = {"schema_version": 1, "mode": "release-preflight", "required": list(required),
              "privileges": [], "ready": None, "error": None,
              "note": "Read-only token query (TOKEN_QUERY). No privilege was enabled, requested or adjusted."}
    try:
        held = {name: attrs for name, attrs in query()}
    except Exception as exc:
        record["error"] = f"{type(exc).__name__}: {exc}"
        return record
    for name in required:
        attrs = held.get(name)
        present = attrs is not None and not (attrs & SE_PRIVILEGE_REMOVED)
        record["privileges"].append({
            "name": name, "held": present,
            "enabled": bool(present and attrs & SE_PRIVILEGE_ENABLED),
            "enabled_by_default": bool(present and attrs & SE_PRIVILEGE_ENABLED_BY_DEFAULT),
        })
    record["ready"] = all(p["enabled"] for p in record["privileges"])
    return record


def format_check(record):
    lines = ["Windows host memory release | privilege pre-flight | read-only, nothing changed"]
    if record.get("error"):
        lines.append(f"pre-flight unavailable: {record['error']}")
    for p in record.get("privileges", []):
        state = "enabled" if p["enabled"] else ("held but disabled" if p["held"] else "not held")
        lines.append(f"  {p['name']}: {state}")
    ready = record.get("ready")
    lines.append("live --release expected to work: " + ("unknown" if ready is None else ("yes" if ready else
                 "no (expect NTSTATUS 0xC0000061 STATUS_PRIVILEGE_NOT_HELD)")))
    lines.append(record["note"])
    return "\n".join(lines)