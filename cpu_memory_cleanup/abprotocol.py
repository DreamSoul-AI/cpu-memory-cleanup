"""Bounded A/A + A/B release protocol runner (release authorised 2026-10-19, A/B only).

Dry-run by default: prints the window plan and the guard results and performs
nothing. Only with ``live=True`` does it sample, and only at the start of a B
window does it call the existing ``perform_release`` once. Guards stop the run
before any sampling or release when a read-only ``scripts/tree-series.ps1``
log is still being written (so the release cannot contaminate that series) or
when the read-only token pre-flight says the needed privilege is not held
(a held-but-disabled privilege is enabled by the release itself only for the
release calls, authorised 2026-10-19; the tool never elevates). A release
that does not succeed stops the protocol; it is reported, not retried.
No process is killed, no service stopped, no setting / pagefile / registry
value changed. All OS access is injectable; tests never touch the real calls.
"""

import json
import time
from datetime import datetime, timezone
from pathlib import Path

from .abcompare import compare
from .analysis import load_records

DEFAULT_PATTERN = ("AA", "AB", "AA", "AB")
DEFAULT_INTERVAL = 10
DEFAULT_COUNT = 30
MAX_PAIRS = 8
SERIES_GLOB = "tree-series*.jsonl"
SERIES_BUSY_SECONDS = 180


def parse_pattern(text):
    pairs = tuple(p.strip().upper() for p in str(text).split(",") if p.strip())
    if not pairs or any(p not in ("AA", "AB") for p in pairs):
        raise ValueError("pattern must be a comma list of AA (null pair) and AB (release pair), e.g. AA,AB,AA,AB")
    if "AA" not in pairs or "AB" not in pairs:
        raise ValueError("pattern needs at least one AA null pair and one AB release pair")
    if len(pairs) > MAX_PAIRS:
        raise ValueError(f"at most {MAX_PAIRS} pairs")
    return pairs


def build_plan(pattern=DEFAULT_PATTERN, interval=DEFAULT_INTERVAL, count=DEFAULT_COUNT):
    if not 1 <= interval <= 3600:
        raise ValueError("interval must be 1..3600 seconds")
    if not 3 <= count <= 10000:
        raise ValueError("count must be 3..10000 samples per window")
    windows = []
    for index, kind in enumerate(pattern, 1):
        for slot in (1, 2):
            windows.append({"pair": index, "kind": kind, "slot": slot,
                            "release_before": kind == "AB" and slot == 2,
                            "file": f"pair{index}-{kind}-{slot}.jsonl"})
    return {"pattern": list(pattern), "interval_seconds": interval, "count_per_window": count,
            "windows": windows, "releases_planned": sum(w["release_before"] for w in windows),
            "estimated_minutes": round(len(windows) * interval * count / 60.0, 1)}


def busy_series(log_dir, now=None, busy_seconds=SERIES_BUSY_SECONDS):
    now = time.time() if now is None else now
    busy = []
    folder = Path(log_dir)
    if folder.is_dir():
        for path in sorted(folder.glob(SERIES_GLOB)):
            age = now - path.stat().st_mtime
            if age < busy_seconds:
                busy.append({"file": path.name, "seconds_since_write": round(age, 1)})
    return busy


def _default_preflight():
    from .privcheck import check
    return check()


def check_guards(log_dir, preflight_fn=None, now=None):
    preflight_fn = _default_preflight if preflight_fn is None else preflight_fn
    busy = busy_series(log_dir, now)
    pre = preflight_fn()
    blockers = []
    if busy:
        blockers.append("read-only series still being written (" + ", ".join(b["file"] for b in busy) +
                        "); wait for it to end so a release cannot contaminate it")
    if pre is None or pre.get("error"):
        blockers.append("privilege pre-flight unavailable: " + str((pre or {}).get("error")))
    elif pre.get("ready_with_scoped_enable", pre.get("ready")) is False:
        missing = ", ".join(p["name"] for p in pre.get("privileges", []) if not p.get("held"))
        blockers.append(f"{missing or 'required privilege'} not usable by this token, so the release would return "
                        "0xC0000061; the tool never adds privileges or elevates")
    return {"busy_series": busy, "preflight": pre, "blockers": blockers, "ok": not blockers}


def _default_sample(count, interval):
    from .windows import iter_snapshots
    return iter_snapshots(count, interval, include_processes=False)


def _default_release():
    from .release import perform_release
    return perform_release("both")


def _now():
    return datetime.now(timezone.utc).isoformat()


def _write_json(path, record):
    Path(path).write_text(json.dumps(record, indent=2, ensure_ascii=True), encoding="utf-8")


def summarize(outdir, plan):
    outdir = Path(outdir)
    records = {w["file"]: load_records(outdir / w["file"]) for w in plan["windows"]
               if (outdir / w["file"]).is_file()}
    nulls, results = [], []
    for index, kind in enumerate(plan["pattern"], 1):
        first, second = f"pair{index}-{kind}-1.jsonl", f"pair{index}-{kind}-2.jsonl"
        if kind == "AA" and records.get(first) and records.get(second):
            nulls.append((records[first], records[second]))
    for index, kind in enumerate(plan["pattern"], 1):
        first, second = f"pair{index}-{kind}-1.jsonl", f"pair{index}-{kind}-2.jsonl"
        if not (records.get(first) and records.get(second)):
            continue
        if kind == "AA":
            others = [n for n in nulls if n != (records[first], records[second])]
            r = compare(records[first], records[second], null_pairs=others or None)
        else:
            r = compare(records[first], records[second], null_pairs=nulls or None)
        results.append({"pair": index, "kind": kind, "verdict": r["verdict"], "reason": r["reason"],
                        "deltas": r["deltas"], "null_pairs_used": (r["null_calibration"] or {}).get("pairs", 0),
                        "refault": r["refault"]})
    return {"null_pairs": len(nulls), "pairs": results}


def run_protocol(plan, outdir, *, live=False, log_dir="logs", sample_fn=None, release_fn=None,
                 preflight_fn=None, now=None):
    """Dry-run unless live. Returns a plain dict; never raises on NT failures."""
    outdir = Path(outdir)
    guards = check_guards(log_dir, preflight_fn, now)
    result = {"schema_version": 1, "mode": "ab-protocol-live" if live else "ab-protocol-dry-run",
              "cleanup_performed": False, "plan": plan, "outdir": str(outdir), "guards": guards,
              "events": [], "status": None, "summary": None}
    if not live:
        result["status"] = "dry-run: nothing sampled, nothing released" + ("" if guards["ok"] else "; live run would be blocked")
        return result
    if not guards["ok"]:
        result["status"] = "aborted before any sampling: " + "; ".join(guards["blockers"])
        return result
    if outdir.exists() and any(outdir.glob("*.jsonl")):
        result["status"] = "aborted: output directory already holds JSONL windows; use a fresh directory"
        return result
    outdir.mkdir(parents=False, exist_ok=True)
    sample_fn = _default_sample if sample_fn is None else sample_fn
    release_fn = _default_release if release_fn is None else release_fn
    for window in plan["windows"]:
        if window["release_before"]:
            recheck = check_guards(log_dir, preflight_fn)
            if not recheck["ok"]:
                result["events"].append({"at_utc": _now(), "event": "release-skipped", "window": window["file"],
                                         "blockers": recheck["blockers"]})
                result["status"] = "stopped before release: " + "; ".join(recheck["blockers"])
                break
            record = release_fn()
            name = f"pair{window['pair']}-release.json"
            _write_json(outdir / name, record)
            ok = bool(record.get("cleanup_performed"))
            result["cleanup_performed"] = result["cleanup_performed"] or ok
            result["events"].append({"at_utc": _now(), "event": "release", "window": window["file"], "record": name,
                                     "ok": ok, "ntstatus": [a.get("ntstatus_hex") for a in record.get("actions", [])]})
            if not ok:
                result["status"] = "stopped: release did not succeed (see record); not retried, no elevation"
                break
        written = 0
        series = sample_fn(plan["count_per_window"], plan["interval_seconds"])
        try:
            with open(outdir / window["file"], "w", encoding="utf-8", newline="\n") as handle:
                for report in series:
                    handle.write(json.dumps(report, ensure_ascii=True, separators=(",", ":")) + "\n")
                    handle.flush()
                    written += 1
        finally:
            close = getattr(series, "close", None)
            if close:
                close()
        result["events"].append({"at_utc": _now(), "event": "window", "window": window["file"], "samples": written})
    if result["status"] is None:
        result["status"] = "completed"
    result["summary"] = summarize(outdir, plan)
    _write_json(outdir / "protocol.json", result)
    return result


def _gib(value):
    return "n/a" if value is None else f"{value / 1024 ** 3:+.2f} GiB"


def format_protocol(result):
    plan = result["plan"]
    lines = [f"A/B release protocol | {result['mode']} | cleanup performed: {'YES' if result['cleanup_performed'] else 'NO'}",
             f"pattern {','.join(plan['pattern'])} | {plan['count_per_window']} x {plan['interval_seconds']} s per window | "
             f"{len(plan['windows'])} windows, {plan['releases_planned']} release(s), about {plan['estimated_minutes']} min",
             f"output: {result['outdir']}"]
    g = result["guards"]
    lines.append("guards: " + ("ok" if g["ok"] else "BLOCKED"))
    lines.extend("  - " + b for b in g["blockers"])
    for event in result["events"]:
        if event["event"] == "release":
            lines.append(f"  {event['at_utc']} release before {event['window']}: {'OK' if event['ok'] else 'FAILED'} {event['ntstatus']}")
        elif event["event"] == "window":
            lines.append(f"  {event['at_utc']} window {event['window']}: {event['samples']} samples")
        else:
            lines.append(f"  {event['at_utc']} {event['event']} {event.get('window')}")
    lines.append("status: " + str(result["status"]))
    summary = result.get("summary")
    if summary:
        lines.append(f"null pairs: {summary['null_pairs']}")
        for p in summary["pairs"]:
            d = p["deltas"]
            lines.append(f"  pair {p['pair']} {p['kind']}: {p['verdict']} | available tail {_gib(d['available_tail_median_bytes'])}, "
                         f"commit {_gib(d['commit_median_bytes'])}, standby {_gib(d['standby_median_bytes'])} | {p['reason']}")
    return "\n".join(lines)