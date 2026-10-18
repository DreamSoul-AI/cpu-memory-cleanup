"""Offline read-only summary of scripts/tree-series.ps1 JSONL runs.

Pure file parsing; no OS calls and no actions of any kind. Answers backlog
item 4 (do old server.mjs / cua-repl / node_repl members stay alive, and how
much private commit do long-lived members hold) and item 8 (does the
unattributed commit residual move with process count) from an existing log.
Fields missing from older runs (age_buckets, commit_residual) are reported as
null / zero-count, never guessed. Values are point-in-time aggregates per
sample; private commit of members is not reclaimable memory.
"""

from pathlib import Path

from .analysis import parse_records

AGE_KINDS = ("server_mjs", "cua_repl", "node_repl")
AGE_BUCKETS = ("lt10m", "10to60m", "1to3h", "gt3h", "unknown")
BASIS = ("read-only offline summary; no process, memory or system action is taken; "
         "per-sample aggregates from scripts/tree-series.ps1; private commit is not reclaimable memory")


def _num(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _stats(values):
    values = [v for v in values if v is not None]
    if not values:
        return {"samples": 0, "first": None, "last": None, "min": None, "max": None, "mean": None}
    return {"samples": len(values), "first": round(values[0], 3), "last": round(values[-1], 3),
            "min": round(min(values), 3), "max": round(max(values), 3),
            "mean": round(sum(values) / len(values), 3)}


def _dict(value):
    return value if isinstance(value, dict) else {}


def _pearson(pairs):
    pairs = [(x, y) for x, y in pairs if x is not None and y is not None]
    n = len(pairs)
    if n < 3:
        return None
    mx = sum(x for x, _ in pairs) / n
    my = sum(y for _, y in pairs) / n
    sxx = sum((x - mx) ** 2 for x, _ in pairs)
    syy = sum((y - my) ** 2 for _, y in pairs)
    if sxx == 0 or syy == 0:
        return None
    return round(sum((x - mx) * (y - my) for x, y in pairs) / (sxx * syy) ** 0.5, 3)


def _age_kind(records, kind):
    per_bucket = {}
    oldest = []
    present = 0
    for record in records:
        entry = None
        for item in _dict(record.get("codex_tree")).get("age_buckets") or []:
            if isinstance(item, dict) and item.get("kind") == kind:
                entry = item
                break
        if entry is None:
            for bucket in AGE_BUCKETS:
                per_bucket.setdefault(bucket, ([], []))
            oldest.append(None)
            continue
        present += 1
        oldest.append(_num(entry.get("oldest_minutes")))
        for bucket in AGE_BUCKETS:
            procs, private = per_bucket.setdefault(bucket, ([], []))
            data = _dict(entry.get(bucket))
            procs.append(_num(data.get("processes")))
            private.append(_num(data.get("private_gib")))
    return {
        "samples_with_field": present,
        "oldest_minutes": _stats(oldest),
        "buckets": {bucket: {"processes": _stats(per_bucket[bucket][0]),
                             "private_gib": _stats(per_bucket[bucket][1])}
                    for bucket in AGE_BUCKETS},
    }


def summarize_run(records):
    first = records[0] if records else {}
    tree = [_dict(r.get("codex_tree")) for r in records]
    host = [_dict(r.get("host")) for r in records]
    residual = [_dict(r.get("commit_residual")) for r in records]
    processes = [_num(r.get("processes")) for r in records]
    residual_gib = [_num(x.get("residual_gib")) for x in residual]
    return {
        "run_id": first.get("run_id"),
        "interval_s": _num(first.get("interval_s")),
        "samples": len(records),
        "declared_count": _num(first.get("count")),
        "first_local": first.get("local"),
        "last_local": records[-1].get("local") if records else None,
        "host_free_gib": _stats([_num(h.get("free_gib")) for h in host]),
        "host_commit_used_gib": _stats([_num(h.get("commit_used_gib")) for h in host]),
        "processes": _stats(processes),
        "born_total": sum(_num(r.get("born_since_prev")) or 0 for r in records[1:]),
        "exited_total": sum(_num(r.get("exited_since_prev")) or 0 for r in records[1:]),
        "codex_tree": {
            "processes": _stats([_num(t.get("processes")) for t in tree]),
            "private_gib": _stats([_num(t.get("private_gib")) for t in tree]),
            "server_mjs": _stats([_num(t.get("server_mjs")) for t in tree]),
            "server_mjs_private_gib": _stats([_num(t.get("server_mjs_private_gib")) for t in tree]),
            "born_total": sum(_num(t.get("born_since_prev")) or 0 for t in tree[1:]),
            "exited_total": sum(_num(t.get("exited_since_prev")) or 0 for t in tree[1:]),
        },
        "age": {kind: _age_kind(records, kind) for kind in AGE_KINDS},
        "commit_residual": {
            "residual_gib": _stats(residual_gib),
            "residual_pct_of_commit": _stats([_num(x.get("residual_pct_of_commit")) for x in residual]),
            "pearson_residual_vs_processes": _pearson(list(zip(processes, residual_gib))),
        },
    }


def summarize(records):
    runs, order = {}, []
    for record in records:
        if record.get("kind") != "tree-series":
            continue
        key = record.get("run_id")
        if key not in runs:
            runs[key] = []
            order.append(key)
        runs[key].append(record)
    return {"mode": "read-only", "actions_taken": False, "basis": BASIS,
            "runs": [summarize_run(runs[key]) for key in order]}


def summarize_file(path):
    return summarize(parse_records(Path(path).read_text(encoding="utf-8").splitlines()))


def _s(stats, suffix=""):
    if not stats["samples"]:
        return "n/a"
    return (f"first {stats['first']}{suffix}, last {stats['last']}{suffix}, "
            f"min {stats['min']}{suffix}, max {stats['max']}{suffix}")


def format_summary(summary):
    lines = ["Tree-series summary | READ ONLY | actions taken: NO", summary["basis"]]
    if not summary["runs"]:
        lines.append("no tree-series records found")
    for run in summary["runs"]:
        lines.append(f"run {run['run_id']}: {run['samples']}/{run['declared_count']} samples every "
                     f"{run['interval_s']}s ({run['first_local']} .. {run['last_local']})")
        lines.append(f"  host free: {_s(run['host_free_gib'], ' GiB')}")
        lines.append(f"  host commit used: {_s(run['host_commit_used_gib'], ' GiB')}")
        lines.append(f"  processes: {_s(run['processes'])}; born {run['born_total']}, exited {run['exited_total']}")
        tree = run["codex_tree"]
        lines.append(f"  codex tree processes: {_s(tree['processes'])}; born {tree['born_total']}, "
                     f"exited {tree['exited_total']}")
        lines.append(f"  codex tree private: {_s(tree['private_gib'], ' GiB')}")
        lines.append(f"  server.mjs: {_s(tree['server_mjs'])}; private {_s(tree['server_mjs_private_gib'], ' GiB')}")
        for kind, data in run["age"].items():
            if not data["samples_with_field"]:
                lines.append(f"  age {kind}: no age_buckets in this run")
                continue
            gt3 = data["buckets"]["gt3h"]
            lines.append(f"  age {kind}: oldest {_s(data['oldest_minutes'], ' min')}; "
                         f"gt3h processes {_s(gt3['processes'])}; gt3h private {_s(gt3['private_gib'], ' GiB')}")
        res = run["commit_residual"]
        if not res["residual_gib"]["samples"]:
            lines.append("  commit residual: no commit_residual fields in this run")
        else:
            lines.append(f"  commit residual: {_s(res['residual_gib'], ' GiB')}; "
                         f"pct {_s(res['residual_pct_of_commit'], '%')}; "
                         f"pearson vs processes {res['pearson_residual_vs_processes']}")
    return "\n".join(lines)
