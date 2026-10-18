"""Offline A/B comparison of two read-only --sample JSONL windows (pre-registered verdict).

Pure file parsing; no OS calls, no release, no action. Window A is the
no-release control, window B is the identical sampling window started right
after one ``--release``. The verdict rule is the one pre-registered in
docs/mitigation-design.md section 7.3; thresholds below are proposals and are
reported alongside the verdict so a reader can re-judge.
"""

from .analysis import describe, load_records, metric_values

GIB = 1024 ** 3
DEFAULT_GAIN_GIB = 0.5        # sustained available gain / commit fall that counts as material
DEFAULT_SPIKE_FACTOR = 1.5    # B mean above A mean x factor (+ floor) = refault spike
SPIKE_FLOORS = {              # absolute floors so a near-zero A window cannot flag noise
    "page_reads_per_sec": 5.0,
    "pages_input_per_sec": 50.0,
    "disk_read_bytes_per_sec": 5.0 * 1024 ** 2,
}
REFAULT_METRICS = tuple(SPIKE_FLOORS)
INFO_METRICS = ("disk_write_bytes_per_sec",)
LEVEL_METRICS = ("available_bytes", "commit_used_bytes", "standby_total_bytes")


def _mean(values):
    present = [v for v in values if v is not None]
    return sum(present) / len(present) if present else None


def _median_tail(values, fraction=1 / 3):
    """Median of the last ``fraction`` of the window (the 'does it persist' part)."""
    if not values:
        return None
    start = int(len(values) * (1 - fraction))
    return describe(values[start:])["median"]


def _window(records):
    rows = [metric_values(r) for r in records]
    out = {"samples": len(rows), "run_ids": sorted({(r.get("series") or {}).get("run_id") for r in records
                                                     if (r.get("series") or {}).get("run_id")}),
           "started_at_utc": records[0].get("started_at_utc") if records else None,
           "finished_at_utc": records[-1].get("finished_at_utc") if records else None,
           "cleanup_performed_in_log": any(r.get("cleanup_performed") is True for r in records),
           "metrics": {}}
    for key in LEVEL_METRICS + REFAULT_METRICS + INFO_METRICS:
        values = [row[key] for row in rows]
        stats = describe(values)
        stats["mean"] = _mean(values)
        stats["first"] = next((v for v in values if v is not None), None)
        stats["tail_median"] = _median_tail([v for v in values if v is not None])
        out["metrics"][key] = stats
    return out


def _diff(b, a):
    return None if a is None or b is None else b - a


def compare(records_a, records_b, gain_gib=DEFAULT_GAIN_GIB, spike_factor=DEFAULT_SPIKE_FACTOR):
    if not records_a or not records_b:
        raise ValueError("both A and B windows need at least one record")
    a, b = _window(records_a), _window(records_b)
    gain = gain_gib * GIB
    am, bm = a["metrics"], b["metrics"]
    available_tail_gain = _diff(bm["available_bytes"]["tail_median"], am["available_bytes"]["tail_median"])
    commit_change = _diff(bm["commit_used_bytes"]["median"], am["commit_used_bytes"]["median"])
    standby_change = _diff(bm["standby_total_bytes"]["median"], am["standby_total_bytes"]["median"])
    spikes, missing = {}, []
    for key in REFAULT_METRICS:
        a_mean, b_mean = am[key]["mean"], bm[key]["mean"]
        if a_mean is None or b_mean is None:
            missing.append(key)
            spikes[key] = None
            continue
        limit = a_mean * spike_factor + SPIKE_FLOORS[key]
        spikes[key] = {"a_mean": a_mean, "b_mean": b_mean, "limit": limit, "spike": b_mean > limit}
    any_spike = any(s and s["spike"] for s in spikes.values())
    commit_fell = commit_change is not None and commit_change <= -gain
    sustained_gain = available_tail_gain is not None and available_tail_gain >= gain
    if missing or available_tail_gain is None or commit_change is None:
        verdict = "insufficient-data"
        reason = "missing metrics: " + ", ".join(missing + [k for k, v in (
            ("available_bytes", available_tail_gain), ("commit_used_bytes", commit_change)) if v is None])
    elif any_spike:
        verdict = "cosmetic-or-harmful"
        reason = "refault cost in B materially above A: " + ", ".join(k for k, s in spikes.items() if s and s["spike"])
    elif commit_fell or sustained_gain:
        verdict = "benefit-for-this-workload"
        reason = ("commit fell" if commit_fell else "available gain persisted into the last third of B") + \
                 " with no hard-fault / pages-input / disk-read spike above A"
    else:
        verdict = "cosmetic"
        reason = "no commit fall and no sustained available gain (free/standby movement alone does not count)"
    return {
        "mode": "offline-ab-compare", "cleanup_performed": False,
        "thresholds": {"gain_gib": gain_gib, "spike_factor": spike_factor,
                       "spike_floors": SPIKE_FLOORS, "tail_fraction": "last third of each window"},
        "A": a, "B": b,
        "deltas": {"available_tail_median_bytes": available_tail_gain,
                   "commit_median_bytes": commit_change,
                   "standby_median_bytes": standby_change},
        "refault": spikes,
        "verdict": verdict, "reason": reason,
        "caveats": ["A and B are different time windows of the same load; background drift can move levels.",
                    "Verdict is per mechanism and per workload; do not generalise to experiment load.",
                    "Hard-fault rates are the responsiveness proxy; no app latency is measured here."],
    }


def compare_files(path_a, path_b, **kwargs):
    return compare(load_records(path_a), load_records(path_b), **kwargs)


def _gib(value):
    return "n/a" if value is None else f"{value / GIB:+.2f} GiB" if value < 0 or value > 0 else "+0.00 GiB"


def _lvl(value):
    return "n/a" if value is None else f"{value / GIB:.2f} GiB"


def _rate(value):
    return "n/a" if value is None else f"{value:.1f}"


def format_comparison(result):
    a, b = result["A"], result["B"]
    lines = ["Offline A/B comparison (read-only; no release performed by this command)",
             f"A: {a['samples']} samples {a['started_at_utc']} .. {a['finished_at_utc']}",
             f"B: {b['samples']} samples {b['started_at_utc']} .. {b['finished_at_utc']}"]
    for key in LEVEL_METRICS:
        lines.append(f"  {key}: A median {_lvl(a['metrics'][key]['median'])}, tail {_lvl(a['metrics'][key]['tail_median'])}; "
                     f"B first {_lvl(b['metrics'][key]['first'])}, median {_lvl(b['metrics'][key]['median'])}, "
                     f"tail {_lvl(b['metrics'][key]['tail_median'])}")
    for key in REFAULT_METRICS + INFO_METRICS:
        lines.append(f"  {key}: A mean {_rate(a['metrics'][key]['mean'])}, B mean {_rate(b['metrics'][key]['mean'])}, "
                     f"B max {_rate(b['metrics'][key]['max'])}")
    d = result["deltas"]
    lines.append(f"Deltas B-A: available tail {_gib(d['available_tail_median_bytes'])}, "
                 f"commit median {_gib(d['commit_median_bytes'])}, standby median {_gib(d['standby_median_bytes'])}")
    lines.append(f"Verdict: {result['verdict']} ({result['reason']})")
    return "\n".join(lines)
