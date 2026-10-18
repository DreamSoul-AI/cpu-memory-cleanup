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

## Per-owner-tree series (desktop load, read-only)

Date: 2026-10-18, 22:30:52-22:59:52 Asia/Shanghai (14:30:52-14:59:52 UTC).
Ordinary desktop load, no experimental load. `scripts/tree-series.ps1`
(read-only CIM queries of `Win32_Process` and `Win32_OperatingSystem`),
30 samples at 60 s intervals, `run_id` `b9132d4f-f300-4cb1-ae77-b0ab2907d320`.
The run completed all 30 samples and stopped on its own; each query took
0.53-0.79 s. Only aggregates were written (no PIDs, command lines or paths);
the raw series stays in gitignored `logs\tree-series.jsonl`. Nothing was
trimmed, flushed, terminated or reconfigured.

| Metric | First 22:30:52 | Min | Median | Max | Last 22:59:52 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Codex tree processes | 484 | 401 | 457.5 | 593 | 593 |
| Codex tree private commit GiB | 9.175 | 7.939 | 8.836 | 10.687 | 10.687 |
| `server.mjs` node processes | 66 | 52 | 61 | 80 | 80 |
| `server.mjs` private commit GiB | 4.601 | 3.625 | 4.324 | 5.579 | 5.579 |
| `cua-repl.mjs` processes | 33 | 26 | 30.5 | 40 | 40 |
| All processes | 943 | 845 | 910.5 | 1046 | 1046 |
| Sum of process private commit GiB | 47.629 | 42.963 | 45.175 | 48.059 | 45.926 |
| Free physical GiB (`FreePhysicalMemory`) | 7.690 | 7.073 | 9.712 | 11.712 | 9.060 |
| Commit used GiB (limit 95.112) | 63.736 | 59.012 | 61.289 | 64.294 | 62.155 |

Reading:

- No monotonic accumulation in this window. The Codex tree fell from 484
  to 401 processes (9.18 to 7.94 GiB) between 22:30 and 22:43, stayed near
  that level until about 22:49, then rose to 593 processes (10.69 GiB) by
  22:59. Over the run 407 tree members were born and 298 exited (net +109);
  host-wide 460 were born and 357 exited.
- Exits are real and mostly come in steps of 13 processes, which looks like
  groups of child processes (shell, console host, `server.mjs`, REPL) being
  started and reaped together per tool session. That grouping is a hunch;
  group composition was not recorded. The 22:19 single-snapshot reading
  ("accumulating without being reaped") is not supported by this series.
- The level follows activity: the largest one-minute jumps were 41 births at
  22:50 and 52 at 22:59. A least-squares slope over the run is about
  +0.027 GiB/min of tree private commit and +0.38 `server.mjs`/min, but the
  curve is V-shaped, so that slope is not an accumulation rate and must not
  be extrapolated.
- `oldest_minutes` rose exactly one minute per sample (135.4 to 164.4), so
  at least one member lived through the whole run (probably the tree root,
  not verified). Per-member ages were not recorded, so whether individual
  `server.mjs` instances survive for hours is still open.
- In this attribution the `ChatGPT.exe` owner tree contains the Codex tree;
  it went from 508 processes / 14.75 GiB to 617 / 16.14 GiB. Separately the
  `sesvc.exe` tree dropped from 35 processes / 7.35 GiB to 24 / 4.56 GiB
  between 22:40:52 and 22:41:52, when free memory rose 8.95 to 11.32 GiB and
  commit fell 62.07 to 59.46 GiB. That release was external to this tool.

Caveats: 30 samples over 29 minutes under one evening's desktop use;
`PrivatePageCount` is commit charge, not residency; `FreePhysicalMemory` is
the WMI figure, not the PDH available counter used elsewhere in this file.
None of these changes is a cleanup benefit, and nothing here makes
terminating any of these processes safe or allowed.

## Commit residual re-check (desktop load, read-only)

Date: 2026-10-18 23:38:55 Asia/Shanghai. One point-in-time snapshot under
ordinary desktop load, no experimental load, taken while the age-bucket tree
series (backlog item 4) was running. Sources: PDH via `Get-Counter`
(`\Memory\*`, `\Process(_Total)\Private Bytes`, `\GPU Adapter Memory(*)`,
`\GPU Process Memory(*)`) and `Win32_Process.PrivatePageCount`. All queries
were read-only; nothing was trimmed, flushed, terminated or reconfigured.

| Item | GiB |
| --- | ---: |
| Commit used / limit | 58.861 / 95.112 |
| Process private commit, `\Process(_Total)\Private Bytes` | 42.488 |
| Same, summed over 769 `Win32_Process` rows | 42.489 |
| Paged pool | 4.178 |
| Nonpaged pool | 2.904 |
| System driver total | 0.014 |
| Unattributed (commit - private - pools - driver) | ~9.28 (15.8 % of commit) |
| GPU shared (system-memory) usage, all adapters | 0.279 |
| GPU dedicated usage, all adapters (VRAM, not host commit) | 3.682 |
| Available (PDH) | 11.390 |

Reading:

- The residual is ~9.28 GiB, close to the ~9.74 GiB at 22:19:39 (previous
  section) although the process count fell 1012 to 769 and process private
  commit fell 48.11 to 42.49 GiB. With two points only, this suggests that
  most of the residual does not track process count; it is not established.
- GPU shared usage is 0.279 GiB, so GPU driver allocations in system memory
  do not explain the residual.
- Remaining candidates are unchanged: page-file-backed shared sections
  (Chromium / Electron shared memory), page tables and kernel stacks. These
  counters cannot separate them. Next read-only step: record the residual per
  beat in the tree series to see whether it moves with process count; a
  RAMMap comparison stays optional and manual.

Caveats: two single snapshots on one evening; counters are read at slightly
different instants; the residual is inferred by subtraction, not measured.
It is not reclaimable memory and not a cleanup target by itself.

## Per-beat commit residual fields (script)

As of 2026-10-18 23:50 Asia/Shanghai, `scripts/tree-series.ps1` records a
read-only `commit_residual` object on every beat when PDH counters succeed:
`process_private_gib` (PDH `\Process(_Total)\Private Bytes`, else CIM
`private_sum_gib`), `paged_pool_gib`, `nonpaged_pool_gib`,
`system_driver_gib`, `residual_gib`, and `residual_pct_of_commit`.
Formula: `residual_gib = commit_used_gib - process_private_gib -
paged_pool_gib - nonpaged_pool_gib - system_driver_gib` (same subtraction as
the Commit residual re-check above). Counter failures set the pool/driver/
residual fields to null and do not abort the series. The age-bucket series
started at 23:12 (run_id `f813ba37`) does **not** include these fields; use
them on the next series after that run finishes.


## Authorised release button: implementation tests (no live release yet)

Date: 2026-10-19 (Asia/Shanghai). Recorded under Enmao's 2026-10-19
authorisation (see mitigation-design.md section 7).

- Implementation: `cpu_memory_cleanup/release.py` +
  `python -m cpu_memory_cleanup --release [--release-what working-set|standby|both]`.
- Unit tests (`tests/test_release.py`, 10 tests) mock the NT layer
  (`nt_call`) and snapshot source; **no real release was performed by any
  test, on any machine**. They cover: command mapping (2 / 4), before/after
  deltas, single vs both mechanisms, `STATUS_PRIVILEGE_NOT_HELD` reported
  plainly with `cleanup_performed: false`, partial success, a static check
  that the release module contains no termination / elevation / per-process
  calls, bare invocation staying read-only, `--release` as the only entry,
  exit 1 (no traceback) on privilege failure, and rejection of
  `--execute` / `--clean` / `--release-what` without `--release` /
  incompatible flag combinations.
- Full suite at implementation time: see the commit message / handover
  note for the count (60 pre-existing + 10 new).

### A/B tooling and pre-flight check (2026-10-19 00:20 Asia/Shanghai)

- Offline comparator built: `python -m cpu_memory_cleanup --ab-compare logs\ab-A.jsonl logs\ab-B.jsonl [--json]`
  (`cpu_memory_cleanup/abcompare.py`, pure file parsing, takes no action, 11 tests, 81 total pass).
  It applies the pre-registered rule of mitigation-design.md 7.3 mechanically: verdict
  `benefit-for-this-workload` needs the B-minus-A median commit to fall by >= 0.5 GiB **or** the
  median available over the last third of B to stay >= 0.5 GiB above the last third of A, **and**
  no refault spike (B mean of page_reads/s, pages_input/s or disk read B/s above A mean x 1.5 plus a
  floor of 5/s, 50/s, 5 MiB/s); any spike gives `cosmetic-or-harmful`; neither gives `cosmetic`;
  missing rates give `insufficient-data`. Thresholds are proposals and are printed with the verdict.
  Smoke run only (not an A/B): idle-baseline.jsonl vs desktop-pressure.jsonl (90 x 10 s each, two
  different loads on 2026-10-18) parsed cleanly and correctly reported no benefit.
- Read-only pre-flight (`whoami /priv`, 00:14 Asia/Shanghai, from the patrol shell, which is in the
  Administrators role): `SeProfileSingleProcessPrivilege` and `SeIncreaseQuotaPrivilege` are **held but
  disabled** in the token. `release.py` deliberately never calls `AdjustTokenPrivileges`, so the live
  `--release` is expected to return `STATUS_PRIVILEGE_NOT_HELD (0xC0000061)` and stop. Enabling an
  already-held privilege in the tool's own token (what one-click releasers do) is not UAC elevation, but
  it is a privilege change in code, so it is **left for Enmao to decide** and not implemented. If the
  live run returns 0xC0000061 it is recorded below as the blocker, as pre-registered.

### Live A/B results (stub ? pending)

Status: **PENDING**. No live release was run at implementation time,
because the 60 s x 120 age-bucket series (run_id f813ba37) was still
running until ~01:11 Asia/Shanghai 2026-10-19 and a release would
contaminate it. A second read-only 60 s x 120 commit-residual series
(run_id f9059e66, `logs/tree-series-residual.jsonl`) runs until ~02:04
Asia/Shanghai 2026-10-19 and must not be contaminated either. The 5-minute
patrol should run, only after confirming both series have ended:

```powershell
Set-Location D:\GitHub\DreamSoul\cpu-memory-cleanup
# A window (no release), then B window (one release at start), desktop load:
python -m cpu_memory_cleanup --sample --interval 10 --count 90 --jsonl logs\ab-A.jsonl
python -m cpu_memory_cleanup --release --json
python -m cpu_memory_cleanup --sample --interval 10 --count 90 --jsonl logs\ab-B.jsonl
```

Then fill in, per mechanism: before/after commit / available / standby /
free, the post-release available/commit/standby trajectory vs A, and
page_reads/s, pages_input/s and disk read/write B/s vs A. Apply the
pre-registered verdict in mitigation-design.md 7.3: free/standby movement
alone is cosmetic and fails as a benefit claim; benefit needs a commit
fall or a sustained available gain with no hard-fault/disk spike above A;
a rapid refault spike is cosmetic-or-harmful for that mechanism. If the
release call returns `STATUS_PRIVILEGE_NOT_HELD`, record that here as the
blocker ? do not elevate and do not retry as administrator from code.

### Privilege pre-flight tool, live read (2026-10-19 00:24 Asia/Shanghai)

`python -m cpu_memory_cleanup --release-preflight`, run once from the elevated Administrator
shell used for this project's checks (read-only token query, nothing enabled):
`SeProfileSingleProcessPrivilege: held but disabled`, so a live `--release` from that shell is
expected to fail with 0xC0000061 until Enmao decides whether the tool may enable that
already-held privilege in its own token. No release was run; both read-only series
(age f813ba37, residual f9059e66) were left undisturbed. 90 tests pass.

## A/A null calibration of the comparator (2026-10-19 00:24 Asia/Shanghai)

Why: before any live A/B, the comparator's refault-spike rule must be checked against natural
desktop noise with **no release in either window**; otherwise an ordinary burst in B could be
misread as release cost (or a quiet B as benefit).

- Smoke (labelled: daily desktop load, no experiment load, no release, 2 windows x 3 samples at
  2 s, 00:23:49-00:23:57 Asia/Shanghai, temp files only): `--ab-compare` verdict `cosmetic`, no
  spike flagged. Level deltas were tiny (available tail -0.03 GiB, commit +0.04 GiB), but refault
  means moved a lot with no release at all: pages_input/s 1.7 -> 44.5, page_reads/s 1.0 -> 3.2,
  disk read 0.11 -> 0.24 MB/s. So short windows are noisy, the absolute floors in the spike rule
  are doing real work, and a single A/B pair is weak evidence. 3 samples per window is far too few
  to conclude anything; this only shows the pipeline runs end to end on real records.
- Running (read-only, bounded): two back-to-back windows `--sample --interval 10 --count 30`
  writing gitignored `logs/aa-A1.jsonl` and `logs/aa-A2.jsonl` (started 00:24:13, ends about
  00:34 Asia/Shanghai), same shape as the planned A/B windows but shorter. Result to be added
  here: `--ab-compare logs/aa-A1.jsonl logs/aa-A2.jsonl` should NOT say benefit or spike; if it
  does, thresholds must be revised before the live A/B, not after.
- Proposed rule (not yet adopted): run at least one A/A pair right before each live A/B and report
  the A/A refault spread alongside the A/B result, so a release-induced spike is judged against
  same-evening noise.

### A/A result (2026-10-19 00:35 Asia/Shanghai): the raw spike rule gives a false positive

Labelled: daily desktop load, no experiment load, **no release in either window**, 2 windows x 30
samples at 10 s (A1 00:24:14-00:29:04, A2 00:29:04-00:33:54 Asia/Shanghai), age and residual
series running alongside (read-only).

- `--ab-compare logs/aa-A1.jsonl logs/aa-A2.jsonl` returned **`cosmetic-or-harmful`** with the
  pre-registered rule, i.e. it would have charged refault cost to a release that never happened.
- Cause: one natural two-sample burst in A2 at about 00:30:54-00:31:04 (page_reads/s 1648 and 2124,
  disk read up to 18.4 MB/s; source not identified). Outside it the windows are alike: page_reads/s
  median 2.5 vs 3.6, pages_input/s median 17.8 vs 15.1, disk read median 0.14 vs 0.14 MB/s.
  Means are dominated by the burst: page_reads/s 6.0 vs 143.6 (limit 14.0, excess ratio 10.2),
  pages_input/s 34.1 vs 428.2 (limit 101.2, ratio 4.2), disk read 0.19 vs 1.67 MB/s (ratio 0.3,
  not flagged).
- Levels barely moved with no release: available tail -0.03 GiB, commit median -0.27 GiB, standby
  median +0.12 GiB. So the 0.5 GiB level threshold looks adequate; the refault rule does not.
- Consequence, as pre-committed above: the rule is revised **before** any live A/B. `--ab-compare`
  now takes repeatable `--ab-null A B` A/A pairs; the largest A/A excess ratio per metric becomes the
  noise ceiling, and a B refault spike only counts as release cost if it exceeds that ceiling.
  Without null pairs the output says the rule is uncalibrated. Refault medians are now printed next
  to means. 6 new tests (97 total) pass; no release was performed.
- One A/A pair is itself a thin ceiling. Proposal for the live run: alternate windows
  A/A, A/B, A/A, A/B (at least 2 null pairs) and report every pair, not only the verdict.
