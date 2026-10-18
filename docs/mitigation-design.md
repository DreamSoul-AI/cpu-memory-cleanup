# Mitigation design (evidence-based, not implemented)

Date: 2026-10-18 (Asia/Shanghai). Status: **design and validation gates only**.
Nothing in this document is implemented, and nothing here authorizes running
any cleanup action. The tool still has no cleanup function and rejects
`--execute` / `--clean`. Red lines in the README stay in force for every
option below: no process termination, no service stops, no system setting or
page-file changes, no automatic elevation.

This is a draft. It will be revised when the 120-sample age-bucket series
(backlog item 4, running until about 01:11 Asia/Shanghai on 2026-10-19) and an
experiment-load baseline (backlog item 5) are available.

## 1. Evidence so far (labelled)

All figures come from the committed records in [validation.md](validation.md),
[idle-baseline-summary.md](idle-baseline-summary.md) and
[desktop-pressure-summary.md](desktop-pressure-summary.md). Same host:
47.1 GiB physical RAM, commit limit 95.1 GiB, Windows 11 build 26200.
None of the runs had experimental (training / evaluation) load.

| Source | Window (Asia/Shanghai, 2026-10-18) | Load | Samples x interval | Key medians / values |
| --- | --- | --- | --- | --- |
| Idle baseline, steady part | 20:14-20:24 | idle desktop | 60 x 10 s (samples 31-90) | available 27.15 GiB, commit 46.45 GiB, standby 18.54 GiB, pages input/s 46.1 |
| Desktop pressure | 21:59-22:14 | daily desktop | 90 x 10 s | available 8.23 GiB (min 6.37), commit 64.70 GiB, standby 6.73 GiB, pages input/s 52.5 (p95 about 5187) |
| Memory gap cross-check | 22:19:39 | daily desktop | 1 snapshot | 1012 processes; private commit sum 48.1 GiB; paged + nonpaged pool 6.7 GiB; about 9.7 GiB of 64.6 GiB commit unattributed; largest single process about 1.8 GiB |
| Per-owner-tree series | 22:30:52-22:59:52 | daily desktop | 30 x 60 s | Codex tree 401-593 processes, 7.94-10.69 GiB private; 407 born, 298 exited; not monotonic |
| Age-bucket series, first sample only | 23:12:06 | daily desktop | 1 of 120 x 60 s | Codex tree 687 processes, 12.50 GiB private; `server.mjs` 92 (6.41 GiB): 20 under 10 min, 50 at 10-60 min, 22 at 1-3 h, 0 over 3 h |

Note (2026-10-18 23:50 Asia/Shanghai): later tree-series runs will also emit a
per-beat read-only `commit_residual` object (private + pools + driver vs commit)
so the ~9 GiB unattributed commit can be tracked over time. The in-flight
age-bucket series (run_id f813ba37) does not include it.

What this supports:

- **The pressure is commit and breadth, not cache.** Between idle and desktop
  pressure, commit rose by about 18 GiB and standby shrank from about 18.5 to
  about 6.7 GiB. Standby is already part of available memory, and under
  pressure there is little of it left. Purging standby would mostly turn
  cached file pages into free pages and add disk reads later; it cannot lower
  commit.
- **No continuous paging was observed.** Hard-fault medians under desktop
  pressure were low (pages input/s median 52.5) with short bursts. The host
  was short on headroom, not thrashing, in these windows.
- **No single process explains it.** The largest process is under 2 GiB.
  About a thousand processes share the load; the biggest owner tree is the
  ChatGPT / Codex desktop tree (Codex subtree about 7.9-12.5 GiB private
  across the series), followed by trees such as `sesvc.exe`, Figma, Weixin,
  Feishu, Cursor and Steam at about 2-5.5 GiB each (23:12:06 sample).
- **Codex child processes are reaped, but the level follows activity.** The
  30-sample series showed births and exits in groups, with no member of the
  `server.mjs` population older than about 3 h in the first age-bucket sample.
  Whether long-lived instances hold a meaningful share is the open question
  for backlog item 4.
- **Large drops seen so far were external.** The 20:11-20:14 release in the
  idle baseline and the `sesvc.exe` tree drop at 22:41 were not caused by
  this tool. A falling number is not a cleanup benefit.

What it does not support: any claim that a system-level cleanup would improve
experiment throughput or tail latency. No experiment-load data exists yet.

## 2. Options considered

| Option | Mechanism | Fits the evidence? | Decision |
| --- | --- | --- | --- |
| M0. Read-only pressure report | Bounded read-only sampling plus owner-tree attribution; prints when available memory or commit headroom crosses a threshold, and which owner trees grew | Yes: it addresses the real driver (many user processes) by telling the user what grew | **Proposed next step** (backlog item 7), still read-only |
| M1. User-driven app hygiene | User closes idle sessions / apps through their own UI, guided by M0 output | Yes: the controllable lever is app-level process count | Documentation only; the tool never closes anything |
| M2. Purge low-priority (priority 0) standby | Native memory-list command, needs a privileged token | Weak: standby is small exactly when pressure is high; does not lower commit | **Deferred.** Only reconsider with experiment-load evidence, in an isolated environment, with explicit approval from Enmao |
| Full standby purge | Same, all priorities | No: larger cold-cache cost, same lack of commit benefit | Rejected |
| Working-set trimming (targeted or global) | `EmptyWorkingSet` / quota changes | No: does not release commit, adds hard faults | Rejected (red line) |
| System file cache trim, modified-list flush | Native privileged commands | No evidence of a cache or modified-list problem (modified list 0.12 GiB at 22:19) | Rejected (red line) |
| Terminate processes, stop services, change page file or settings, auto-elevate | n/a | n/a | Forbidden regardless of evidence |

## 3. M0 sketch (read-only, not yet built)

- Reuse the existing bounded sampler and `scripts/tree-series.ps1`
  aggregation; no new system interfaces beyond read-only PDH / PSAPI / CIM.
- Report, per sample: available, commit used / limit, hard-fault rates, and
  the top owner trees by private commit with their change since the previous
  sample. Only aggregates; no PIDs, command lines or paths.
- Thresholds are configuration, not hard-coded claims. Example values to
  evaluate (proposals, not findings): available under 4 GiB, or commit above
  85 % of the limit, sustained for 3 consecutive samples.
- Output goes to stdout / JSONL only. No notifications that act, no
  background service, stops after the requested count like the sampler.
- Tests: static allowlist tests (no write / terminate / trim calls), threshold
  logic unit tests on recorded JSONL, single-sample read-only smoke test.

### 3.1 Status: offline evaluator built (23:24 Asia/Shanghai)

- `python -m cpu_memory_cleanup --pressure <tree-series JSONL>` evaluates
  recorded `scripts/tree-series.ps1` logs. Pure file parsing, no OS calls,
  no actions. Options: `--free-below-gib`, `--commit-above-pct`,
  `--sustain`; defaults (4 GiB, 85 %, 3 samples) are the proposals above.
- `free_gib` is Win32_OperatingSystem FreePhysicalMemory as recorded by the
  script; commit percent is used / limit. Owner growth compares only owners
  present in both samples' top lists (absent means unknown, not zero).
- 10 new unit tests (sustain logic, end-of-file episodes, missing values,
  run separation, CLI flag rules, no-OS-call static check); 40 tests pass.
- Applied to the two everyday-desktop series (not experiment load, 60 s
  interval): 22:30:52-22:59:52 (30 samples) min free 7.073 GiB, max commit
  67.6 %; 23:12:06-23:24:06 (first 13 of 120 samples, run still going)
  min free 6.403 GiB, max commit 68.9 %. No sample crossed the default
  proposal thresholds, so no episode is reported. This says the desktop was
  not at the proposed alarm level in those windows; it is not a cleanup
  result and not a threshold calibration.

### 3.2 Status: live bounded follow built (23:34 Asia/Shanghai)

- `python -m cpu_memory_cleanup --pressure-follow <tree-series JSONL>
  --polls N --poll-interval S` follows a log that `scripts/tree-series.ps1`
  is appending in another window. It reads the file from the start, then
  only newly appended complete lines; it opens that one file read-only
  ("rb") once per poll, does no sampling of its own, makes no process or
  memory API calls and takes no action. It stops after N polls
  (1..10000, 1..3600 s apart) or if the file shrinks.
- Per sample it prints free GiB, commit percent and the breach streak; when
  the streak first reaches `--sustain` it prints one alert with the owner
  trees that grew since the sample before the streak (same rules as 3.1).
  A new run_id resets the streak. Thresholds remain proposals.
- Code: breach tracking in `pressure.LiveTracker`, file following in
  `cpu_memory_cleanup/follow.py`; 13 new tests (streak / alert-once,
  run reset, partial-line and shrink handling, bounds, CLI flag rules, a
  static check that `follow.py` has exactly one `open(path, "rb")` and no
  write or process calls); 53 tests pass.
- Live smoke run 23:33:05-23:34:06 against the running 120-sample age
  series (everyday desktop, 60 s interval, not experiment load): 2 polls
  61 s apart read 22 samples including one appended during the follow;
  0 alerts at default thresholds. Not a cleanup result.
- Backlog item 7 is complete. M0 is therefore usable by hand; it is still
  not a background service and does not run on its own.

## 4. Gates before any action-taking option (unchanged in spirit from README)

1. Evidence: an experiment-load baseline aligned in UTC with experiment
   throughput and tail latency, showing a host-memory-related slowdown.
2. Isolation: the candidate is first tried on an isolated test environment,
   never first on the experiment machine.
3. A/B: repeated with / without runs under the same load; report available,
   commit, hard faults, disk I/O, throughput and p95 / p99 latency, plus the
   cold-cache recovery cost. No enable without repeatable sustained net
   benefit.
4. Safety mechanics in place first: default dry-run, explicit single-run
   entry point, before / after logs with log failure blocking execution,
   cooldown and rate limit, stop on failure with no fallback to a more
   aggressive action, and no reliance on process names to protect
   experiments.
5. Approval: Enmao reviews the evidence, mechanism and risk write-up before
   any privileged code is written.

## 5. Open questions

- Age-bucket series: do any `server.mjs` / REPL instances survive for hours,
  and what share of private commit do they hold? (backlog item 4)
- About 9.7 GiB of commit was not attributed by process private commit and
  pool counters at 22:19. Candidates (unverified): page-file-backed shared
  sections, page tables, kernel stacks. Needs RAMMap or a dedicated counter.
- Behaviour under real experiment load is unknown. (backlog item 5)


## 6. Note 2026-10-18 late: Enmao's 360-style goal, and the decision needed

Enmao clarified (2026-10-18 23:44 Asia/Shanghai) that the product goal he
has in mind is a 360-style one-click memory releaser (acceleration-ball
style), and asked whether GitHub already has such projects. The fresh survey
is in github_survey.md, section "Fresh re-check 2026-10-18". Headlines:

- Yes, GitHub has many such tools (memreduct ~10.9k stars, WinMemoryCleaner
  ~5.2k stars, plus newer Rust ones). Their release mechanisms are all the
  same small set: purge standby, empty working sets, flush modified list,
  trim file cache. None releases commit. The only mechanism that releases
  commit is ending processes, which is what 360 publicly evidences
  (user-chosen programs) and what this repo's red lines forbid the tool to do.
- On this host's evidence (commit 64.7/95.1 GiB from ~1000 processes, standby
  only ~6.7 GiB under pressure, low hard faults), a standby/working-set
  releaser would mostly improve the displayed free number, not headroom,
  and may cost refaults. This does not refute Enmao's goal; it reframes it:
  a faithful 360 clone would be a cosmetic releaser here, while the
  effective 360-like action (closing heavy trees) stays human-driven (M1).

Decision Enmao needs to make before any code crosses the read-only red line:

1. Mechanism choice, explicitly one of: (a) stay read-only M0/M1 (report +
   human closes apps); (b) cosmetic releaser (standby purge and/or working-
   set trim) knowing the expected benefit is a prettier number, to be proven
   or refuted by A/B; (c) a targeted working-set trim on self-owned test
   processes only, as an isolated experiment.
2. Explicit go-ahead that implementing and running the chosen mechanism is
   authorised, superseding the current "no cleanup implementation/execution"
   red line for that mechanism only. Process termination, service stops,
   settings/page-file changes and auto-elevation stay forbidden regardless.
3. A/B validation plan agreed in advance, per section 4: same load with and
   without, repeated; report available, commit, hard faults / pages input,
   disk I/O, experiment throughput and p95/p99 latency, and cold-cache
   recovery cost. A run that only shows Free rising is a failed validation,
   not a success.

Until Enmao makes that decision, M0 remains the direction and nothing in
this note or the survey is an implementation authorisation.
