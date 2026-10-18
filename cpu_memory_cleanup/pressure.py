"""Offline read-only pressure report over tree-series JSONL (mitigation design M0).

Pure file parsing; no OS calls and no actions of any kind. Thresholds are
configuration proposals to evaluate, not findings. ``free_gib`` is the
Win32_OperatingSystem FreePhysicalMemory value recorded by
scripts/tree-series.ps1; commit percent is commit_used_gib / commit_limit_gib.
Owner trees outside a sample's top list are unknown there, not zero.
"""

from pathlib import Path

from .analysis import parse_records

DEFAULT_FREE_BELOW_GIB = 4.0
DEFAULT_COMMIT_ABOVE_PCT = 85.0
DEFAULT_SUSTAIN = 3
TOP_GROWTH = 5
BASIS = ("read-only offline report; no process, memory or system action is taken; "
         "thresholds are proposals; owner growth compares only owners present in both samples")


def _num(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def sample_view(record):
    host = record.get("host") if isinstance(record.get("host"), dict) else {}
    used = _num(host.get("commit_used_gib"))
    limit = _num(host.get("commit_limit_gib"))
    owners = {}
    for item in record.get("top_owners") or []:
        if isinstance(item, dict) and isinstance(item.get("owner"), str):
            private = _num(item.get("private_gib"))
            if private is not None:
                owners[item["owner"]] = private
    return {
        "local": record.get("local"),
        "free_gib": _num(host.get("free_gib")),
        "commit_pct": used / limit * 100.0 if used is not None and limit else None,
        "owners": owners,
    }


def breach_reasons(view, free_below_gib, commit_above_pct):
    reasons = []
    if view["free_gib"] is not None and view["free_gib"] < free_below_gib:
        reasons.append("free_below")
    if view["commit_pct"] is not None and view["commit_pct"] > commit_above_pct:
        reasons.append("commit_above")
    return reasons


def owner_growth(before, after, top=TOP_GROWTH):
    grown = []
    for owner, value in after["owners"].items():
        if owner in before["owners"]:
            delta = value - before["owners"][owner]
            if delta > 0:
                grown.append({"owner": owner, "private_gib": round(value, 3),
                              "delta_gib": round(delta, 3)})
    grown.sort(key=lambda item: (-item["delta_gib"], item["owner"]))
    entered = sorted(owner for owner in after["owners"] if owner not in before["owners"])
    return {"grown": grown[:top], "entered_top_list": entered}


def _episode(views, reasons, start, end):
    baseline = views[start - 1] if start > 0 else views[start]
    frees = [v["free_gib"] for v in views[start:end + 1] if v["free_gib"] is not None]
    pcts = [v["commit_pct"] for v in views[start:end + 1] if v["commit_pct"] is not None]
    kinds = sorted({reason for item in reasons[start:end + 1] for reason in item})
    return {
        "start_local": views[start]["local"],
        "end_local": views[end]["local"],
        "samples": end - start + 1,
        "reasons": kinds,
        "min_free_gib": round(min(frees), 3) if frees else None,
        "max_commit_pct": round(max(pcts), 1) if pcts else None,
        "growth_baseline": "previous sample" if start > 0 else "first breaching sample",
        "owner_growth": owner_growth(baseline, views[end]),
    }


def evaluate_run(records, free_below_gib, commit_above_pct, sustain):
    views = [sample_view(record) for record in records]
    reasons = [breach_reasons(view, free_below_gib, commit_above_pct) for view in views]
    episodes, longest, run_start = [], 0, None
    for index, item in enumerate(reasons):
        if item and run_start is None:
            run_start = index
        if run_start is not None and (not item or index == len(reasons) - 1):
            end = index if item else index - 1
            longest = max(longest, end - run_start + 1)
            if end - run_start + 1 >= sustain:
                episodes.append(_episode(views, reasons, run_start, end))
            run_start = None
    frees = [v["free_gib"] for v in views if v["free_gib"] is not None]
    pcts = [v["commit_pct"] for v in views if v["commit_pct"] is not None]
    first = records[0] if records else {}
    return {
        "run_id": first.get("run_id"),
        "interval_s": _num(first.get("interval_s")),
        "samples": len(views),
        "first_local": views[0]["local"] if views else None,
        "last_local": views[-1]["local"] if views else None,
        "breaching_samples": sum(1 for item in reasons if item),
        "longest_breach_samples": longest,
        "min_free_gib": round(min(frees), 3) if frees else None,
        "max_commit_pct": round(max(pcts), 1) if pcts else None,
        "episodes": episodes,
    }


def evaluate(records, free_below_gib=DEFAULT_FREE_BELOW_GIB,
             commit_above_pct=DEFAULT_COMMIT_ABOVE_PCT, sustain=DEFAULT_SUSTAIN):
    if not isinstance(sustain, int) or sustain < 1:
        raise ValueError("sustain must be an integer of at least 1")
    runs, order = {}, []
    for record in records:
        if record.get("kind") != "tree-series":
            continue
        key = record.get("run_id")
        if key not in runs:
            runs[key] = []
            order.append(key)
        runs[key].append(record)
    return {
        "mode": "read-only",
        "actions_taken": False,
        "basis": BASIS,
        "thresholds": {"free_below_gib": free_below_gib, "commit_above_pct": commit_above_pct,
                       "sustain_samples": sustain, "status": "proposal"},
        "runs": [evaluate_run(runs[key], free_below_gib, commit_above_pct, sustain)
                 for key in order],
    }


def evaluate_file(path, **kwargs):
    return evaluate(parse_records(Path(path).read_text(encoding="utf-8").splitlines()), **kwargs)


def _fmt(value, suffix=""):
    return "n/a" if value is None else f"{value}{suffix}"


def format_report(report):
    t = report["thresholds"]
    lines = ["Pressure report | READ ONLY | actions taken: NO",
             f"thresholds (proposal): free < {t['free_below_gib']} GiB or commit > "
             f"{t['commit_above_pct']}% for >= {t['sustain_samples']} consecutive samples",
             report["basis"]]
    if not report["runs"]:
        lines.append("no tree-series records found")
    for run in report["runs"]:
        lines.append(f"run {run['run_id']}: {run['samples']} samples every "
                     f"{_fmt(run['interval_s'], 's')} ({run['first_local']} .. {run['last_local']}); "
                     f"breaching {run['breaching_samples']}, longest {run['longest_breach_samples']}; "
                     f"min free {_fmt(run['min_free_gib'], ' GiB')}, "
                     f"max commit {_fmt(run['max_commit_pct'], '%')}")
        if not run["episodes"]:
            lines.append("  no sustained episodes")
        for ep in run["episodes"]:
            lines.append(f"  episode {ep['start_local']} .. {ep['end_local']} ({ep['samples']} samples, "
                         f"{'+'.join(ep['reasons'])}); min free {_fmt(ep['min_free_gib'], ' GiB')}, "
                         f"max commit {_fmt(ep['max_commit_pct'], '%')}; growth vs {ep['growth_baseline']}:")
            for item in ep["owner_growth"]["grown"]:
                lines.append(f"    {item['owner']}: +{item['delta_gib']} GiB (now {item['private_gib']} GiB)")
            if ep["owner_growth"]["entered_top_list"]:
                lines.append("    entered top list: " + ", ".join(ep["owner_growth"]["entered_top_list"]))
    return "\n".join(lines)
