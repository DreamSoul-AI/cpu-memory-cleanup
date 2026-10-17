# Local validation

Date: 2026-10-17. Windows 11 build 26200, 64-bit Python 3.13.9.
No elevation was requested, no token privileges changed, no cleanup tools run,
and no other processes or system settings modified.

Executed commands from this repository:

```powershell
python -m cpu_memory_cleanup
python -m cpu_memory_cleanup --json
python -m unittest discover -s tests -v
```

Both CLI formats exited 0. The saved JSON snapshot started at
2026-10-17T13:53:14.513302+00:00 (21:53:14 Asia/Shanghai).
The [raw JSON](inspection-sample.json) and [human-readable output](inspection-sample.txt)
contain the same snapshot; the text was rendered from that JSON to avoid sample skew.
The earlier direct text command also succeeded with different live values.

| Metric | GiB |
| --- | ---: |
| Physical total / used / available | 47.113 / 29.026 / 18.087 |
| Commit used / limit / peak since boot | 56.895 / 95.113 / 94.257 |
| Composite system cache | 17.380 |
| Standby theoretical upper bound | 16.233 |
| Modified / free-zero | 0.125 / 1.748 |
| Pagefile used / total | 21.444 / 48.000 |

498 processes enumerated, 258 readable, 240 skipped. Ranking is incomplete.
Standby is already included in available RAM. Guaranteed additional available RAM
and actual cleanup are both zero. Live values changed between samples without any
cleanup by this tool; a changing number alone is not evidence of cleanup benefit.

Assertions on the actual JSON passed: read-only marker, no cleanup, physical
used + available = total, commit <= limit, process records contain only name and
working_set_bytes, readable + skipped = enumerated, and guaranteed additional = 0.

All 9 unit tests passed in 0.005s on this machine:

- Windows structure layouts
- Exact bound API allowlist
- Process query-only access mask, denied/exited processes, ranking and handle closure
- Pagefile page-to-byte conversion and successful zero-pagefile enumeration
- PDH invalid CStatus yields null and closes the query
- Optional metric failure remains explicit and text rendering works
- Standby estimate clamps to available, distinguishes missing from zero
- Execution switches and invalid top counts rejected before inspection
- JSON error output and nonzero exit status

These tests cover guardrails and failure handling, not a general security proof.
Not checked: administrator cleanup operations, other Windows builds, 32-bit Python,
long-term performance, or manual comparison against RAMMap. Read-only inspection
itself has small CPU/RAM overhead. Protected processes remain skipped.
