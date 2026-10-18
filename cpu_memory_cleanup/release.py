"""360-style one-click memory release (authorised 2026-10-19, A/B only).

Mechanisms are our own implementation of publicly documented Windows
memory-list commands, not copied code:
  NtSetSystemInformation(SystemMemoryListInformation=80) with a
  MEMORY_LIST_COMMAND value.
v1 covers only:
  - MemoryEmptyWorkingSets (2)
  - MemoryPurgeStandbyList (4)
No process is terminated, no per-process handle is opened, no setting /
pagefile / registry value is changed, and no privilege is requested or
elevation attempted. If the caller's token lacks the required privilege
the NTSTATUS is reported plainly and the caller fails gracefully.
Tests must inject a fake nt_call and must never call the real one.
"""

import ctypes as c
from ctypes import wintypes as w
from datetime import datetime, timezone
import sys

SYSTEM_MEMORY_LIST_INFORMATION = 80
MEMORY_EMPTY_WORKING_SETS = 2
MEMORY_PURGE_STANDBY_LIST = 4

COMMANDS = {
    "working-set": ("MemoryEmptyWorkingSets", MEMORY_EMPTY_WORKING_SETS),
    "standby": ("MemoryPurgeStandbyList", MEMORY_PURGE_STANDBY_LIST),
}
RELEASE_WHATS = ("working-set", "standby", "both")

STATUS_NAMES = {
    0x00000000: "STATUS_SUCCESS",
    0xC000000D: "STATUS_INVALID_PARAMETER",
    0xC0000022: "STATUS_ACCESS_DENIED",
    0xC0000061: "STATUS_PRIVILEGE_NOT_HELD (SeProfileSingleProcessPrivilege may be missing)",
}


def commands_for(what="both"):
    if what == "both":
        keys = ("working-set", "standby")
    elif what in COMMANDS:
        keys = (what,)
    else:
        raise ValueError(f"what must be one of {RELEASE_WHATS}, got {what!r}")
    return [(key, COMMANDS[key][0], COMMANDS[key][1]) for key in keys]


def status_name(status):
    if status is None:
        return None
    return STATUS_NAMES.get(status & 0xFFFFFFFF, "NTSTATUS")


def _default_nt_call(command):
    """Real NT call. Never used by tests; never attempts elevation."""
    if sys.platform != "win32":
        raise OSError("Windows is required; no release was performed.")
    ntdll = c.WinDLL("ntdll.dll", use_last_error=True)
    func = ntdll.NtSetSystemInformation
    func.argtypes = [c.c_int, c.c_void_p, w.ULONG]
    func.restype = c.c_long
    value = c.c_int(command)
    status = func(SYSTEM_MEMORY_LIST_INFORMATION, c.byref(value), c.sizeof(value))
    return status & 0xFFFFFFFF


def _summary(report):
    if not report:
        return None
    cache = report.get("cache") or {}
    standby_keys = ("standby_core_bytes", "standby_normal_bytes", "standby_reserve_bytes")
    standby_vals = [cache.get(k) for k in standby_keys]
    standby = sum(standby_vals) if all(v is not None for v in standby_vals) else None
    physical = report.get("physical") or {}
    commit = report.get("commit") or {}
    return {
        "available_bytes": physical.get("available_bytes"),
        "physical_used_bytes": physical.get("used_bytes"),
        "commit_used_bytes": commit.get("used_bytes"),
        "commit_limit_bytes": commit.get("limit_bytes"),
        "standby_bytes": standby,
        "free_zero_bytes": cache.get("free_zero_bytes"),
        "modified_bytes": cache.get("modified_bytes"),
        "cache_bytes": cache.get("cache_bytes"),
    }


def _deltas(before, after):
    out = {}
    for key in before:
        b, a = before.get(key), (after or {}).get(key)
        out[key + "_delta"] = (a - b) if (a is not None and b is not None) else None
    return out


def perform_release(what="both", *, nt_call=None, snapshot_fn=None, preflight_fn=None):
    """Run the authorised release once. All NT access is injectable.

    Returns a plain dict; NT / privilege failures are recorded, not raised.
    cleanup_performed is True only if at least one NT call returned success.
    """
    selected = commands_for(what)
    if snapshot_fn is None:
        from .windows import snapshot as _snapshot
        snapshot_fn = lambda: _snapshot(10, include_processes=False)
    if nt_call is None:
        nt_call = _default_nt_call
        if preflight_fn is None:
            from .privcheck import check as preflight_fn
    preflight = preflight_fn() if preflight_fn is not None else None
    started = datetime.now(timezone.utc).isoformat()
    before_report = snapshot_fn()
    actions = []
    for key, name, command in selected:
        try:
            status = nt_call(command)
            status = None if status is None else (int(status) & 0xFFFFFFFF)
            ok = status == 0
            actions.append({"what": key, "mechanism": name, "command": command,
                            "ntstatus": status,
                            "ntstatus_hex": None if status is None else f"0x{status:08x}",
                            "ntstatus_name": status_name(status), "ok": ok, "error": None})
        except Exception as exc:  # graceful: report, never traceback / elevate
            actions.append({"what": key, "mechanism": name, "command": command,
                            "ntstatus": None, "ntstatus_hex": None, "ntstatus_name": None,
                            "ok": False, "error": f"{type(exc).__name__}: {exc}"})
    after_report = snapshot_fn()
    before, after = _summary(before_report), _summary(after_report)
    performed = any(a["ok"] for a in actions)
    warnings = [
        "Standby already counts as available: a standby purge moves standby to free, so guaranteed additional available is ~0 from that mechanism alone.",
        "A free/available rise alone is not a benefit claim. Benefit requires the A/B refault test in docs/mitigation-design.md.",
        "Working-set emptying does not release commit; pages not touched again stay out, pages touched again must be faulted back in.",
    ]
    if not performed:
        warnings.append("No release action succeeded; see actions[].ntstatus / error. No elevation was attempted.")
    if preflight is not None and preflight.get("ready") is False:
        missing = ", ".join(x["name"] for x in preflight.get("privileges", []) if not x["enabled"])
        warnings.append(f"Pre-flight: {missing} not enabled in this token, so 0xC0000061 is expected. "
                        "The tool does not enable or request privileges.")
    return {
        "schema_version": 1, "mode": "release", "release_what": what,
        "cleanup_performed": performed,
        "started_at_utc": started, "finished_at_utc": datetime.now(timezone.utc).isoformat(),
        "before": before, "after": after, "deltas": _deltas(before, after),
        "preflight": preflight, "actions": actions, "warnings": warnings,
    }


def _size(value):
    return "unavailable" if value is None else f"{value / 1024**3:.3f} GiB"


def _signed(value):
    return "unavailable" if value is None else f"{value / 1024**3:+.3f} GiB"


def format_release(record):
    lines = [f"Windows host memory release | 360-style | cleanup performed: {'YES' if record['cleanup_performed'] else 'NO'}",
             f"UTC: {record['started_at_utc']} .. {record['finished_at_utc']}",
             f"release_what: {record['release_what']}"]
    pre = record.get("preflight")
    if pre:
        states = ", ".join(f"{x['name']}={'enabled' if x['enabled'] else ('held-disabled' if x['held'] else 'not-held')}"
                           for x in pre.get("privileges", []))
        lines.append(f"pre-flight: {states or pre.get('error') or 'unavailable'}")
    for action in record["actions"]:
        if action["error"]:
            lines.append(f"  {action['what']} ({action['mechanism']}, command {action['command']}): FAILED {action['error']}")
        else:
            lines.append(f"  {action['what']} ({action['mechanism']}, command {action['command']}): "
                         f"{'OK' if action['ok'] else 'FAILED'} NTSTATUS {action['ntstatus_hex']} {action['ntstatus_name']}")
    for label in ("before", "after"):
        s = record[label] or {}
        lines.append(f"{label}: available={_size(s.get('available_bytes'))} commit_used={_size(s.get('commit_used_bytes'))} "
                     f"standby={_size(s.get('standby_bytes'))} free_zero={_size(s.get('free_zero_bytes'))}")
    d = record["deltas"]
    lines.append(f"deltas: available={_signed(d.get('available_bytes_delta'))} commit_used={_signed(d.get('commit_used_bytes_delta'))} "
                 f"standby={_signed(d.get('standby_bytes_delta'))} free_zero={_signed(d.get('free_zero_bytes_delta'))}")
    lines.extend("WARNING: " + x for x in record["warnings"])
    return "\n".join(lines)
