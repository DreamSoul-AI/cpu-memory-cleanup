import argparse
from pathlib import Path
import json
import sys

from .abcompare import compare_files as ab_compare_files, format_comparison
from . import abprotocol
from .analysis import format_summary, summarize_file
from .pressure import (DEFAULT_COMMIT_ABOVE_PCT, DEFAULT_FREE_BELOW_GIB, DEFAULT_SUSTAIN,
                       LiveTracker, evaluate_file, format_report)
from .treesummary import format_summary as format_tree_summary, summarize_file as summarize_tree_file
from .follow import (MAX_POLL_SECONDS, MAX_POLLS, MIN_POLL_SECONDS, follow_file, format_event,
                     format_follow_summary)
from .release import RELEASE_WHATS, format_release, perform_release
from .privcheck import check as privilege_check, format_check
from .windows import (MAX_INTERVAL_SECONDS, MAX_SAMPLE_COUNT, MIN_INTERVAL_SECONDS,
                      iter_snapshots, snapshot)


def size(value):
    return "unavailable" if value is None else f"{value / 1024**3:.3f} GiB"


def render(report):
    lines = ["Windows host memory inspection | READ ONLY | cleanup performed: NO",
             f"UTC: {report['started_at_utc']} .. {report['finished_at_utc']}"]
    for section in ("physical", "commit", "cache", "pagefile"):
        lines.append(section + ":")
        values = report[section]
        if values is None:
            lines.append("  unavailable")
        else:
            for key, value in values.items():
                lines.append(f"  {key}: {size(value) if key.endswith('_bytes') else value}")
    processes = report["processes"]
    lines.append("Processes by working set (shared pages included; do not sum):")
    if processes is None:
        lines.append("  unavailable")
    else:
        lines.append(f"  enumerated={processes['enumerated']} readable={processes['readable']} skipped={processes['skipped']}")
        for process in processes["top"]:
            lines.append(f"  {process['name']}: {size(process['working_set_bytes'])}")
    estimate = report["estimate"]
    lines.extend(["Theoretical repurposable standby:",
                  f"  upper bound: {size(estimate['standby_repurposable_upper_bytes'])}",
                  "  guaranteed additional available: 0 bytes; actual cleanup: 0 bytes",
                  "  " + estimate["basis"]])
    lines.extend("WARNING: " + warning for warning in report["warnings"])
    return "\n".join(lines)


def positive_int(text):
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return value


def finite_float(text):
    value = float(text)
    if value != value or value in (float("inf"), float("-inf")):
        raise argparse.ArgumentTypeError("must be a finite number")
    return value


def sample_count(text):
    value = int(text)
    if not 1 <= value <= MAX_SAMPLE_COUNT:
        raise argparse.ArgumentTypeError(f"must be 1..{MAX_SAMPLE_COUNT}")
    return value


def interval_seconds(text):
    value = float(text)
    if value != value or value in (float("inf"), float("-inf")) or not MIN_INTERVAL_SECONDS <= value <= MAX_INTERVAL_SECONDS:
        raise argparse.ArgumentTypeError(f"must be {MIN_INTERVAL_SECONDS}..{MAX_INTERVAL_SECONDS} seconds")
    return value


def poll_count(text):
    value = int(text)
    if not 1 <= value <= MAX_POLLS:
        raise argparse.ArgumentTypeError(f"must be 1..{MAX_POLLS}")
    return value


def poll_seconds(text):
    value = float(text)
    if value != value or not MIN_POLL_SECONDS <= value <= MAX_POLL_SECONDS:
        raise argparse.ArgumentTypeError(f"must be {MIN_POLL_SECONDS}..{MAX_POLL_SECONDS} seconds")
    return value


def compact(record):
    return json.dumps(record, ensure_ascii=True, separators=(",", ":"))


def rate_text(value):
    return "n/a" if value is None else f"{value:.3f}"


def progress(report):
    series = report["series"]
    rates = report.get("rates") or {}
    return (f"sample {series['index'] + 1}/{series['count']} | READ ONLY | cleanup performed: NO | "
            f"available={size(report['physical']['available_bytes'])} "
            f"commit_used={size(report['commit']['used_bytes'])} "
            f"page_reads/s={rate_text(rates.get('page_reads_per_sec'))} "
            f"disk_read_B/s={rate_text(rates.get('disk_read_bytes_per_sec'))} "
            f"warnings={len(report['warnings'])}")


def emit_failure(as_json, message):
    if as_json:
        print(json.dumps({"mode": "read-only", "cleanup_performed": False, "error": message}))
    else:
        print(message, file=sys.stderr)


def dump_line(report):
    return json.dumps(report, ensure_ascii=True, separators=(",", ":"))


def run_series(args):
    handle = None
    written = 0
    if args.jsonl is not None:
        if not args.jsonl.parent.is_dir():
            emit_failure(args.json, f"JSONL directory does not exist: {args.jsonl.parent}")
            return 1
        try:
            handle = open(args.jsonl, "a", encoding="utf-8", newline="\n")
        except OSError as exc:
            emit_failure(args.json, f"JSONL open failed: {exc}")
            return 1
    series = iter_snapshots(args.count, args.interval, top=args.top,
                            include_processes=args.with_processes)
    try:
        for report in series:
            line = dump_line(report)
            if handle is not None:
                handle.write(line + "\n")
                handle.flush()
            written += 1
            print(line if args.json else progress(report), flush=True)
    except OSError as exc:
        emit_failure(args.json, f"Sampling stopped after {written} records: {exc}")
        return 1
    finally:
        series.close()
        if handle is not None:
            handle.close()
    return 0


def run_release(args):
    try:
        record = perform_release(args.release_what or "both")
    except (OSError, ValueError) as exc:
        emit_failure(args.json, f"Release failed before any action: {exc}")
        return 1
    print(json.dumps(record, indent=2, ensure_ascii=True) if args.json else format_release(record))
    return 0 if record.get("cleanup_performed") else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description="Windows memory inspection. Read-only unless the explicit --release flag is given (authorised 2026-10-19).")
    parser.add_argument("--json", action="store_true", help="emit JSON with byte units and null for unavailable metrics")
    parser.add_argument("--release", action="store_true",
                        help="EXPLICIT OPT-IN: run the authorised 360-style release once (working-set + standby by default), "
                             "with before/after snapshots. This is the only entry point that performs a release.")
    parser.add_argument("--release-what", choices=RELEASE_WHATS, default=None,
                        help="with --release: working-set, standby, or both (default: both, the one-click behaviour)")
    parser.add_argument("--release-preflight", action="store_true",
                        help="read-only: report whether the token holds / has enabled the privilege --release needs; "
                             "changes nothing and enables nothing")
    parser.add_argument("--top", type=positive_int, default=10, help="process count when ranking is enabled (default: 10)")
    parser.add_argument("--sample", action="store_true",
                        help="bounded read-only series; requires --interval, --count, and --jsonl or --json")
    parser.add_argument("--interval", type=interval_seconds, help=f"seconds between sample starts ({MIN_INTERVAL_SECONDS}..{MAX_INTERVAL_SECONDS})")
    parser.add_argument("--count", type=sample_count, help=f"number of samples (1..{MAX_SAMPLE_COUNT})")
    parser.add_argument("--jsonl", type=Path, help="append one JSON object per line; parent directory must exist")
    parser.add_argument("--with-processes", action="store_true",
                        help="include working-set ranking on every sample (slower; off by default)")
    parser.add_argument("--analyze", type=Path, metavar="JSONL",
                        help="offline summary of an existing --sample JSONL log; read-only file parsing, no inspection")
    parser.add_argument("--pressure", type=Path, metavar="JSONL",
                        help="offline read-only pressure report over a scripts/tree-series.ps1 JSONL log; takes no action")
    parser.add_argument("--free-below-gib", type=finite_float,
                        help=f"--pressure threshold proposal for free memory (default {DEFAULT_FREE_BELOW_GIB})")
    parser.add_argument("--commit-above-pct", type=finite_float,
                        help=f"--pressure threshold proposal for commit percent of limit (default {DEFAULT_COMMIT_ABOVE_PCT})")
    parser.add_argument("--sustain", type=positive_int,
                        help=f"--pressure consecutive samples needed for an episode (default {DEFAULT_SUSTAIN})")
    parser.add_argument("--pressure-follow", type=Path, metavar="JSONL",
                        help="bounded read-only follow of a tree-series JSONL another process is appending; "
                             "requires --polls and --poll-interval; takes no action")
    parser.add_argument("--polls", type=poll_count, help=f"--pressure-follow poll count (1..{MAX_POLLS})")
    parser.add_argument("--poll-interval", type=poll_seconds,
                        help=f"--pressure-follow seconds between polls ({MIN_POLL_SECONDS}..{MAX_POLL_SECONDS})")
    parser.add_argument("--tree-summary", type=Path, metavar="JSONL",
                        help="offline read-only summary of a scripts/tree-series.ps1 JSONL log "
                             "(process trees, member age buckets, commit residual); takes no action")
    parser.add_argument("--ab-compare", type=Path, nargs=2, metavar=("A_JSONL", "B_JSONL"),
                        help="offline read-only A/B comparison of two --sample JSONL windows (A no release, "
                             "B after one --release) with the pre-registered verdict; takes no action")
    parser.add_argument("--ab-null", type=Path, nargs=2, action="append", metavar=("A_JSONL", "B_JSONL"),
                        help="with --ab-compare: an A/A null pair (no release in either window); repeatable. "
                             "A B refault spike only counts if it exceeds the largest A/A excess")
    parser.add_argument("--ab-protocol", type=Path, metavar="OUTDIR",
                        help="bounded A/A + A/B release protocol into a fresh OUTDIR; DRY-RUN unless --ab-live "
                             "(prints the plan and guards, samples nothing, releases nothing)")
    parser.add_argument("--ab-live", action="store_true",
                        help="with --ab-protocol: actually sample, releasing once before each B window; refuses "
                             "while a tree-series log is being written or the token does not hold the needed privilege")
    parser.add_argument("--ab-pattern", help="with --ab-protocol: comma list of AA/AB pairs (default AA,AB,AA,AB)")
    parser.add_argument("--ab-interval", type=float, help="with --ab-protocol: seconds between samples (default 10)")
    parser.add_argument("--ab-count", type=int, help="with --ab-protocol: samples per window (default 30)")
    args = parser.parse_args(argv)
    ab_proto_flags = ("ab_live", "ab_pattern", "ab_interval", "ab_count")
    if args.ab_protocol is None and any(getattr(args, k) not in (None, False) for k in ab_proto_flags):
        parser.error("--ab-live, --ab-pattern, --ab-interval and --ab-count require --ab-protocol")
    if args.ab_protocol is not None:
        allowed = ("json", "top", "ab_protocol") + ab_proto_flags
        if any(v not in (None, False) for k, v in vars(args).items() if k not in allowed) or args.top != 10:
            parser.error("--ab-protocol can only be combined with --json, --ab-live, --ab-pattern, --ab-interval, --ab-count")
        try:
            pattern = abprotocol.parse_pattern(args.ab_pattern) if args.ab_pattern else abprotocol.DEFAULT_PATTERN
            plan = abprotocol.build_plan(pattern,
                                         abprotocol.DEFAULT_INTERVAL if args.ab_interval is None else args.ab_interval,
                                         abprotocol.DEFAULT_COUNT if args.ab_count is None else args.ab_count)
            result = abprotocol.run_protocol(plan, args.ab_protocol, live=args.ab_live)
        except (OSError, ValueError) as exc:
            emit_failure(args.json, f"A/B protocol failed: {exc}")
            return 1
        print(json.dumps(result, indent=2, ensure_ascii=True) if args.json else abprotocol.format_protocol(result))
        return 0 if (not args.ab_live or result["status"] == "completed") else 1
    if args.ab_null is not None and args.ab_compare is None:
        parser.error("--ab-null requires --ab-compare")
    if args.release_preflight:
        others = {k: v for k, v in vars(args).items() if k not in ("json", "release_preflight", "top")}
        if any(v not in (None, False) for v in others.values()) or args.top != 10:
            parser.error("--release-preflight can only be combined with --json")
        record = privilege_check()
        print(json.dumps(record, indent=2, ensure_ascii=True) if args.json else format_check(record))
        return 1 if record.get("error") else 0
    if args.release:
        if (args.sample or args.analyze is not None or args.pressure is not None or args.pressure_follow is not None
                or args.tree_summary is not None or args.ab_compare is not None or args.interval is not None or args.count is not None
                or args.jsonl is not None or args.with_processes or args.polls is not None or args.poll_interval is not None
                or args.free_below_gib is not None or args.commit_above_pct is not None or args.sustain is not None):
            parser.error("--release can only be combined with --json and --release-what")
        return run_release(args)
    if args.release_what is not None:
        parser.error("--release-what requires --release")
    if args.ab_compare is not None:
        if (args.tree_summary is not None or args.pressure is not None or args.pressure_follow is not None
                or args.analyze is not None or args.sample or args.interval is not None or args.count is not None
                or args.jsonl is not None or args.with_processes or args.polls is not None
                or args.poll_interval is not None or args.free_below_gib is not None
                or args.commit_above_pct is not None or args.sustain is not None):
            parser.error("--ab-compare can only be combined with --json")
        try:
            comparison = ab_compare_files(*args.ab_compare, null_paths=args.ab_null)
        except (OSError, ValueError) as exc:
            emit_failure(args.json, f"A/B comparison failed: {exc}")
            return 1
        print(json.dumps(comparison, indent=2, ensure_ascii=True) if args.json
              else format_comparison(comparison))
        return 0
    if args.tree_summary is not None:
        if (args.pressure is not None or args.pressure_follow is not None or args.analyze is not None
                or args.sample or args.interval is not None or args.count is not None or args.jsonl is not None
                or args.with_processes or args.polls is not None or args.poll_interval is not None
                or args.free_below_gib is not None or args.commit_above_pct is not None
                or args.sustain is not None):
            parser.error("--tree-summary can only be combined with --json")
        try:
            tree_summary = summarize_tree_file(args.tree_summary)
        except (OSError, ValueError) as exc:
            emit_failure(args.json, f"Tree summary failed: {exc}")
            return 1
        print(json.dumps(tree_summary, indent=2, ensure_ascii=True) if args.json
              else format_tree_summary(tree_summary))
        return 0
    threshold_flags = (args.free_below_gib is not None or args.commit_above_pct is not None
                       or args.sustain is not None)
    if args.pressure_follow is not None:
        if (args.pressure is not None or args.analyze is not None or args.sample or args.interval is not None
                or args.count is not None or args.jsonl is not None or args.with_processes):
            parser.error("--pressure-follow cannot be combined with --pressure, --analyze, --sample, "
                         "--interval, --count, --jsonl or --with-processes")
        if args.polls is None or args.poll_interval is None:
            parser.error("--pressure-follow requires both --polls and --poll-interval")
        try:
            tracker = LiveTracker(
                free_below_gib=DEFAULT_FREE_BELOW_GIB if args.free_below_gib is None else args.free_below_gib,
                commit_above_pct=DEFAULT_COMMIT_ABOVE_PCT if args.commit_above_pct is None else args.commit_above_pct,
                sustain=DEFAULT_SUSTAIN if args.sustain is None else args.sustain)
            render_event = compact if args.json else format_event
            summary = follow_file(args.pressure_follow, args.polls, args.poll_interval, tracker,
                                  lambda event: print(render_event(event), flush=True))
        except (OSError, ValueError) as exc:
            emit_failure(args.json, f"Pressure follow failed: {exc}")
            return 1
        print(compact(summary) if args.json else format_follow_summary(summary), flush=True)
        return 0
    if args.polls is not None or args.poll_interval is not None:
        parser.error("--polls and --poll-interval require --pressure-follow")
    if args.pressure is not None:
        if (args.analyze is not None or args.sample or args.interval is not None or args.count is not None
                or args.jsonl is not None or args.with_processes):
            parser.error("--pressure cannot be combined with --analyze, --sample, --interval, --count, --jsonl or --with-processes")
        try:
            report = evaluate_file(
                args.pressure,
                free_below_gib=DEFAULT_FREE_BELOW_GIB if args.free_below_gib is None else args.free_below_gib,
                commit_above_pct=DEFAULT_COMMIT_ABOVE_PCT if args.commit_above_pct is None else args.commit_above_pct,
                sustain=DEFAULT_SUSTAIN if args.sustain is None else args.sustain)
        except (OSError, ValueError) as exc:
            emit_failure(args.json, f"Pressure report failed: {exc}")
            return 1
        print(json.dumps(report, indent=2, ensure_ascii=True) if args.json else format_report(report))
        return 0
    if threshold_flags:
        parser.error("--free-below-gib, --commit-above-pct and --sustain require --pressure or --pressure-follow")
    if args.analyze is not None:
        if args.sample or args.interval is not None or args.count is not None or args.jsonl is not None or args.with_processes:
            parser.error("--analyze cannot be combined with --sample, --interval, --count, --jsonl or --with-processes")
        try:
            summary = summarize_file(args.analyze)
        except (OSError, ValueError) as exc:
            emit_failure(args.json, f"Analysis failed: {exc}")
            return 1
        print(json.dumps(summary, indent=2, ensure_ascii=True) if args.json else format_summary(summary))
        return 0
    series_flags = args.interval is not None or args.count is not None or args.jsonl is not None or args.with_processes
    if args.sample:
        if args.interval is None or args.count is None:
            parser.error("--sample requires both --interval and --count")
        if args.jsonl is None and not args.json:
            parser.error("--sample requires --jsonl or --json so the series is retained")
    elif series_flags:
        parser.error("--interval, --count, --jsonl and --with-processes require --sample")
    if not args.sample:
        try:
            report = snapshot(args.top)
        except OSError as exc:
            emit_failure(args.json, f"Inspection failed: {exc}" if not args.json else str(exc))
            return 1
        print(json.dumps(report, indent=2, ensure_ascii=True) if args.json else render(report))
        return 0
    return run_series(args)


if __name__ == "__main__":
    raise SystemExit(main())
