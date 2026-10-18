

## A/B protocol runner, dry-run only (2026-10-19 00:55 Asia/Shanghai)

The A/A lesson above (one null pair is a thin ceiling) is now built into a single bounded entry
point, so the live run cannot be done ad hoc: `python -m cpu_memory_cleanup --ab-protocol OUTDIR`
(module `cpu_memory_cleanup/abprotocol.py`, commit 83eed17).

- Default plan: pairs `AA,AB,AA,AB`, each window 30 samples x 10 s, 8 windows, about 40 min. The
  release (existing `perform_release("both")`, working-set + standby) happens once, only right
  before the second window of each AB pair; AA pairs never release. Windows are written to a fresh
  OUTDIR, then every AB pair is judged by the `--ab-compare` rule with all AA pairs as the noise
  ceiling (each AA pair is also judged against the other AA pairs as a sanity check), and
  `protocol.json` keeps the plan, guard results, every NTSTATUS and the per-pair verdicts.
- Dry-run unless `--ab-live`: prints the plan and guards, samples nothing, releases nothing,
  creates nothing.
- Guards (checked before any sampling and again right before each release): refuse while any
  `logs/tree-series*.jsonl` was written in the last 180 s (so the age/residual series cannot be
  contaminated), and refuse when the read-only privilege pre-flight is not ready (the tool never
  enables or requests the privilege). A release that does not succeed stops the run; it is
  reported, not retried, no elevation. Reusing an OUTDIR that already holds windows is refused.
- Dry-run read at 00:53 Asia/Shanghai: `guards: BLOCKED` for both reasons, i.e. the two read-only
  series are still writing (age ends about 01:11, residual about 02:04) and
  SeProfileSingleProcessPrivilege is held but disabled. So even after 02:04 the live protocol stays
  blocked until Enmao decides on that privilege.
- 12 mock tests (109 total) pass; tests inject fake sample/release/pre-flight functions and never
  call the real NT release. No release was performed.
