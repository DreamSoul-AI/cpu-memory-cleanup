"""Offline analysis of read-only --sample JSONL logs. Pure file parsing; no OS calls."""

import json
import math
from pathlib import Path

BYTE_METRICS = ("available_bytes", "commit_used_bytes", "standby_total_bytes")
RATE_METRICS = ("page_reads_per_sec", "pages_input_per_sec",
                "disk_read_bytes_per_sec", "disk_write_bytes_per_sec")
ALL_METRICS = BYTE_METRICS + RATE_METRICS
DEFAULT_TRANSITION_BYTES = 1024 ** 3
OWNER_LINES = 10


def _get(record, *keys):
    value = record
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def metric_values(record):
    """Extract the flat metric set from one snapshot record; missing stays None."""
    cache = record.get("cache") or {}
    standby_parts = [cache.get(key) for key in
                     ("standby_core_bytes", "standby_normal_bytes", "standby_reserve_bytes")]
    standby = sum(standby_parts) if all(isinstance(v, (int, float)) and not isinstance(v, bool)
                                        for v in standby_parts) else None
    rates = record.get("rates") or {}
    values = {
        "available_bytes": _get(record, "physical", "available_bytes"),
        "commit_used_bytes": _get(record, "commit", "used_bytes"),
        "standby_total_bytes": standby,
    }
    for key in RATE_METRICS:
        value = rates.get(key)
        values[key] = value if isinstance(value, (int, float)) and not isinstance(value, bool) else None
    return values


def parse_records(lines):
    """Parse JSONL text lines into records. Blank lines skipped; bad JSON raises ValueError."""
    records = []
    for number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"line {number}: invalid JSON: {exc.msg}") from exc
        if not isinstance(record, dict):
            raise ValueError(f"line {number}: expected a JSON object")
        records.append(record)
    return records


def load_records(path):
    return parse_records(Path(path).read_text(encoding="utf-8").splitlines())


def percentile(sorted_values, fraction):
    """Linear-interpolation percentile of an already sorted non-empty list."""
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    position = fraction * (len(sorted_values) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return float(sorted_values[int(position)])
    weight = position - lower
    return float(sorted_values[lower] * (1 - weight) + sorted_values[upper] * weight)


def describe(values):
    present = sorted(v for v in values if v is not None)
    if not present:
        return {"count": 0, "missing": len(values), "min": None, "median": None,
                "p90": None, "p95": None, "max": None}
    return {"count": len(present), "missing": len(values) - len(present),
            "min": float(present[0]), "median": percentile(present, 0.5),
            "p90": percentile(present, 0.9), "p95": percentile(present, 0.95),
            "max": float(present[-1])}


def detect_transitions(records, threshold_bytes=DEFAULT_TRANSITION_BYTES):
    """Consecutive available/commit jumps at or above threshold_bytes, in file order."""
    transitions = []
    extracted = [metric_values(record) for record in records]
    for index in range(1, len(records)):
        for metric in ("available_bytes", "commit_used_bytes"):
            before, after = extracted[index - 1][metric], extracted[index][metric]
            if before is None or after is None:
                continue
            delta = after - before
            if abs(delta) >= threshold_bytes:
                transitions.append({
                    "index": index, "metric": metric,
                    "started_at_utc": records[index].get("started_at_utc"),
                    "from_bytes": float(before), "to_bytes": float(after),
                    "delta_bytes": float(delta),
                })
    return transitions


def summarize_process_owners(records):
    """Per-name view of the working-set ranking stored with --with-processes.

    For each process name we keep only the largest single instance in each sample.
    Same-name instances are never added together, and working set includes shared
    pages, so nothing here is a reclaimable amount.
    """
    ranked = [r for r in records
              if isinstance(r.get("processes"), dict) and isinstance(r["processes"].get("top"), list)]
    per_name = {}
    for record in ranked:
        largest, instances = {}, {}
        for item in record["processes"]["top"]:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            size = item.get("working_set_bytes")
            if not isinstance(name, str) or not isinstance(size, (int, float)) or isinstance(size, bool):
                continue
            instances[name] = instances.get(name, 0) + 1
            largest[name] = max(largest.get(name, 0), size)
        for name, size in largest.items():
            entry = per_name.setdefault(name, {"sizes": [], "max_instances_in_top": 0})
            entry["sizes"].append(size)
            entry["max_instances_in_top"] = max(entry["max_instances_in_top"], instances[name])
    owners = []
    for name, entry in per_name.items():
        sizes = sorted(entry["sizes"])
        owners.append({"name": name, "samples_present": len(sizes),
                       "max_instances_in_top": entry["max_instances_in_top"],
                       "largest_instance_median_bytes": percentile(sizes, 0.5),
                       "largest_instance_max_bytes": float(sizes[-1])})
    owners.sort(key=lambda o: (-o["largest_instance_median_bytes"], o["name"]))
    return {"ranked_samples": len(ranked), "owners": owners,
            "basis": "largest single instance per sample; same-name instances not summed; "
                     "working set includes shared pages and is not a reclaimable amount"}


def summarize_records(records, transition_threshold_bytes=DEFAULT_TRANSITION_BYTES):
    extracted = [metric_values(record) for record in records]
    warnings = [w for record in records for w in (record.get("warnings") or [])]
    starts = [r.get("started_at_utc") for r in records if r.get("started_at_utc")]
    return {
        "mode": "read-only-analysis", "cleanup_performed": False,
        "sample_count": len(records),
        "run_ids": sorted({r["series"]["run_id"] for r in records
                           if isinstance(r.get("series"), dict) and r["series"].get("run_id")}),
        "started_at_utc": starts[0] if starts else None,
        "finished_at_utc": starts[-1] if starts else None,
        "warning_count": len(warnings),
        "overrun_count": sum(1 for w in warnings if "overrun" in w.lower()),
        "first_tick_rate_warning_count": sum(1 for w in warnings if "no previous PDH interval" in w),
        "metrics": {key: describe([row[key] for row in extracted]) for key in ALL_METRICS},
        "transition_threshold_bytes": transition_threshold_bytes,
        "transitions": detect_transitions(records, transition_threshold_bytes),
        "process_owners": summarize_process_owners(records),
    }


def summarize_file(path, transition_threshold_bytes=DEFAULT_TRANSITION_BYTES):
    return summarize_records(load_records(path), transition_threshold_bytes)


def _gib(value):
    return "n/a" if value is None else f"{value / 1024 ** 3:.3f} GiB"


def _num(value):
    return "n/a" if value is None else f"{value:.3f}"


def format_summary(summary):
    lines = ["Offline JSONL analysis | READ ONLY | cleanup performed: NO",
             f"samples={summary['sample_count']} warnings={summary['warning_count']} "
             f"overruns={summary['overrun_count']} "
             f"first_tick_rate_warnings={summary['first_tick_rate_warning_count']}",
             f"UTC: {summary['started_at_utc']} .. {summary['finished_at_utc']}",
             f"run_ids={', '.join(summary['run_ids']) or 'none'}"]
    for key in ALL_METRICS:
        stats = summary["metrics"][key]
        render = _gib if key in BYTE_METRICS else _num
        lines.append(f"{key}: count={stats['count']} missing={stats['missing']} "
                     f"min={render(stats['min'])} median={render(stats['median'])} "
                     f"p90={render(stats['p90'])} p95={render(stats['p95'])} max={render(stats['max'])}")
    lines.append(f"transitions (|delta| >= {_gib(summary['transition_threshold_bytes'])}): "
                 f"{len(summary['transitions'])}")
    for item in summary["transitions"]:
        lines.append(f"  sample {item['index'] + 1} {item['metric']} "
                     f"{_gib(item['from_bytes'])} -> {_gib(item['to_bytes'])} "
                     f"(delta {_gib(item['delta_bytes'])}) at {item['started_at_utc']}")
    owners = summary.get("process_owners") or {}
    if owners.get("ranked_samples"):
        lines.append(f"process owners from working-set ranking: ranked_samples={owners['ranked_samples']} "
                     f"({owners['basis']})")
        for owner in owners["owners"][:OWNER_LINES]:
            lines.append(f"  {owner['name']}: present={owner['samples_present']} "
                         f"max_instances_in_top={owner['max_instances_in_top']} "
                         f"largest_median={_gib(owner['largest_instance_median_bytes'])} "
                         f"largest_max={_gib(owner['largest_instance_max_bytes'])}")
    else:
        lines.append("process owners: no working-set ranking in this log (sampled without --with-processes)")
    return "\n".join(lines)
