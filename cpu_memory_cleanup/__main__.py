import argparse
import json
import sys

from .windows import snapshot


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


def main(argv=None):
    parser = argparse.ArgumentParser(description="Read-only Windows memory inspection. No cleanup executor exists.")
    parser.add_argument("--json", action="store_true", help="emit JSON with byte units and null for unavailable metrics")
    parser.add_argument("--top", type=positive_int, default=10, help="process count (default: 10)")
    args = parser.parse_args(argv)
    try:
        report = snapshot(args.top)
    except OSError as exc:
        if args.json:
            print(json.dumps({"mode": "read-only", "cleanup_performed": False, "error": str(exc)}))
        else:
            print(f"Inspection failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2, ensure_ascii=True) if args.json else render(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
