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

## Bounded read-only series

Date: 2026-10-18 (06:35 Asia/Shanghai). Same machine and Python as above.
No elevation, no cleanup tool, and no other process or setting was changed.

```powershell
python -m unittest discover -s tests -v
python -m cpu_memory_cleanup --sample --interval 1 --count 2 --jsonl logs\series.jsonl
python -m cpu_memory_cleanup --json --top 1
```

All 15 unit tests passed in 0.010s. The two-sample series and the one-shot JSON
inspection both exited 0. The series is saved as [series-sample.jsonl](series-sample.jsonl).
Both lines share `run_id` `90e5adce4c8c4c8290d7dd4f5e9f1de4`. Process ranking was off.
`cleanup_performed` is false and guaranteed additional available RAM is 0 on both lines.

| Sample | Available GiB | Commit used GiB | Page reads/s | Pages input/s | Disk read B/s | Disk write B/s |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 1, started 2026-10-17T22:35:05.452596+00:00 | 2.549 | 66.518 | null | null | null | null |
| 2, finished 2026-10-17T22:35:06.453852+00:00 | 2.679 | 66.392 | 7.998 | 22.994 | 118751 | 5904775 |

The first sample has four warnings, one per rate counter, because PDH has no previous
interval yet. The second sample has no warnings. `page_reads_per_sec` counts disk read
operations that satisfy hard faults; it is not a one-to-one fault count.
`pages_input_per_sec` is the corresponding page count. This pair of samples only
checks that the series starts, stops, and emits rates. It is not an experiment-load
baseline and does not measure throughput or tail latency.

## Idle baseline (no experimental load)

Date: 2026-10-18, 20:09:23-20:24:13 Asia/Shanghai (12:09:23-12:24:13 UTC).
Same machine and Python as above. No experimental workload was intentionally
started for this run. This is an idle baseline only; it is not a baseline
under experimental load and does not measure throughput or tail latency.
No elevation, no cleanup tool, and no other process or setting was changed
by this tool. Every record has `cleanup_performed: false`.

The truncated run from the 07:06 Asia/Shanghai shutdown (76 records) was
discarded and deleted first; this run started from an empty file.

```powershell
python -m unittest discover -s tests -v
python -m cpu_memory_cleanup --sample --interval 10 --count 90 --jsonl logs\idle-baseline.jsonl
python -m cpu_memory_cleanup --execute
python -m cpu_memory_cleanup --clean
```

All 15 unit tests passed in 0.012s before the run. `--execute` and `--clean`
were both rejected as unrecognized arguments (exit 2) before any inspection.
The series completed all 90 samples, exited 0, and stopped on its own.
All 90 lines share `run_id` `e33ec090ffe6444b921596ba8e98bb8c`.
The raw series stays in gitignored `logs\idle-baseline.jsonl`; it is not
committed. There were 4 warnings in total, all on the first sample (one per
rate counter, no previous PDH interval yet), and 0 overrun warnings.

| Metric (90 samples; rates on 89) | Min | Median | Max |
| --- | ---: | ---: | ---: |
| Available GiB | 1.445 | 27.144 | 34.988 |
| Commit used GiB | 43.527 | 46.463 | 62.756 |
| Standby total (core+normal+reserve) GiB | 1.200 | 18.536 | 28.332 |
| Page reads/sec | 1.400 | 26.800 | 3444.141 |
| Pages input/sec | 3.800 | 144.500 | 31742.669 |
| Disk read bytes/sec | 90108 | 621773 | 129319763 |
| Disk write bytes/sec | 298195 | 1224329 | 1946843849 |

This run was not homogeneous, so the full-range table must not be read as a
steady idle level. For roughly the first 20 samples available RAM stayed
near 1.4-2.1 GiB with commit near 61-63 GiB; around 20:11 Asia/Shanghai the
modified list briefly reached about 22 GiB and available RAM then rose,
with commit falling to about 44-47 GiB by about 20:14. That release was
external to this tool (it performed no cleanup on any record); the process
responsible was not identified because process ranking was off. The last
60 samples (31-90) are the steadier portion:

| Metric (samples 31-90; rates on 60) | Min | Median | Max |
| --- | ---: | ---: | ---: |
| Available GiB | 25.839 | 27.151 | 33.061 |
| Commit used GiB | 43.556 | 46.445 | 47.348 |
| Standby total GiB | 17.562 | 18.536 | 20.002 |
| Page reads/sec | 1.400 | 8.700 | 726.695 |
| Pages input/sec | 3.800 | 46.101 | 31742.669 |
| Disk read bytes/sec | 90108 | 202726 | 129319763 |
| Disk write bytes/sec | 298195 | 1026258 | 6988843 |

Even in the steadier portion there are short rate spikes, so medians, not
maxima, describe the idle level. Standby remains part of available RAM;
guaranteed additional available RAM and actual cleanup are both still 0.
A changing number alone is not evidence of cleanup benefit, and this idle
run gives no reason to authorize any cleanup action.

## Offline analyzer and idle summary

Date: 2026-10-18 (Asia/Shanghai). `python -m cpu_memory_cleanup --analyze
logs\idle-baseline.jsonl` (pure file parsing, no OS calls) reproduces the
committed summary in [idle-baseline-summary.md](idle-baseline-summary.md):
90 samples, 4 warnings (all first-tick rate warnings), 0 overruns, 12
transitions at or above 1 GiB. Headline full-run medians: available
27.144 GiB, commit used 46.463 GiB, standby total 18.536 GiB; rates on
89 samples (first sample null). The large transitions around 20:11-20:14
Asia/Shanghai were an external release, not a cleanup benefit of this tool.
Unit tests grew from 15 to 22 with `tests/test_analysis.py`.

## Memory gap cross-check (desktop pressure, read-only)

Date: 2026-10-18 22:19:39 Asia/Shanghai. One point-in-time snapshot under
ordinary desktop load, no experimental load. Sources: PDH `\Memory\*`
counters, `Win32_OperatingSystem`, and `Win32_Process` (`WorkingSetSize`,
`PrivatePageCount`, `ParentProcessId`, `CreationDate`; command lines were
reduced to script basenames and not stored). All queries were read-only;
nothing was trimmed, flushed, terminated or reconfigured. A Task Manager /
RAMMap GUI comparison was not done in this round (no interactive operator);
it stays optional.

| Item | GiB |
| --- | ---: |
| Installed / visible physical | 47.112 |
| Available (standby normal 6.641 + reserve 0.872 + core 0.000, free & zero 0.343) | 7.856 |
| Physical in use (total - available) | ~39.26 |
| Commit used / limit (page file usage 27.0 %) | 64.600 / 95.112 |
| Paged pool (resident 3.036) | 3.845 |
| Nonpaged pool | 2.898 |
| System cache resident | 1.242 |
| Driver resident | 0.035 |
| Modified list | 0.120 |
| Sum of process private commit, 1012 processes | 48.113 |
| Sum of process working sets (double-counts shared pages) | 42.822 |

Where the gap sits (largest single process ~1.8 GiB vs ~39 GiB in use and
64.6 GiB commit):

- It is breadth, not one big process: 1012 processes. Process private commit
  explains ~48.1 of 64.6 GiB commit; paged + nonpaged pool add ~6.7 GiB
  (~54.9 total). The remaining ~9.7 GiB is not attributed by these counters;
  likely candidates are page-file-backed shared sections (Chromium / Electron
  shared memory), page tables and kernel stacks. Unverified: needs RAMMap or a
  dedicated counter.
- Physical: kernel-side resident (paged resident + nonpaged + system cache +
  drivers + modified) is ~7.35 GiB, leaving ~31.9 GiB resident for processes.
  This is inferred by subtraction, not measured directly.
- Grouped by name (sums, for accounting only): node.exe x124 7.115 GiB
  private, ChatGPT.exe x23 6.781, 360se.exe x22 5.184, Figma.exe x11 2.910,
  Feishu.exe x14 2.256, Cursor.exe x16 1.946, codex.exe x11 1.898.
- Grouped by owner tree: all descendants of `codex.exe` (Codex desktop) are
  546 processes with 9.886 GiB private commit (working-set sum 16.1 GiB,
  overcounted): 123 node.exe (6.987 GiB), 118 node_repl.exe, 206 conhost.exe,
  78 cmd.exe, 11 codex.exe. 78 of the node.exe run the Codex computer-use
  runtime `cua_node\...\server.mjs` via cmd.exe (5.445 GiB private), 39 run
  `cua-repl.mjs` (0.788 GiB). Every one started within the last ~6 h, 87 of
  124 node.exe within the last hour, about 14 new `server.mjs` per 10 minutes
  between 22:00 and 22:20.

Reading: the pattern is consistent with Codex child processes accumulating
without being reaped, and it matches the evening drop of available memory,
but one snapshot does not prove a leak; a bounded per-tree time series is the
next read-only step. Caveats: single sample; working set includes shared
pages; `PrivatePageCount` is commit charge, not residency; nothing here is a
cleanup benefit, and nothing here makes terminating these processes safe or
allowed (process termination stays a red line for this tool).
