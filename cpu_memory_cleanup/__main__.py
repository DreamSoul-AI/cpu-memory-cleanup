import argparse
from pathlib import Path
import json
import sys

from .analysis import format_summary, summarize_file
from .pressure import (DEFAULT_COMMIT_ABOVE_PCT, DEFAULT_FREE_BELOW_GIB, DEFAULT_SUSTAIN,
                       LiveTracker, evaluate_file, format_report)
from .follow import (MAX_POLL_SECONDS, MAX_POLLS, MIN_POLL_SECONDS, follow_file, format_event,
                     format_follow_summary)
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


def main(argv=None):
    parser = argparse.ArgumentParser(description="Read-only Windows memory inspection. No cleanup executor exists.")
    parser.add_argument("--json", action="store_true", help="emit JSON with byte units and null for unavailable metrics")
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
    args = parser.parse_args(argv)
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
