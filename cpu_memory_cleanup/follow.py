"""Bounded read-only follow of a tree-series JSONL that another process appends to.

The only I/O is opening that one log file read-only ("rb") once per poll.
No sampling, no process or memory APIs, no actions; stops after the requested
number of polls. Breach logic lives in pressure.LiveTracker.
"""

import json
import time
from pathlib import Path

from .pressure import BASIS, _fmt

MAX_POLLS = 10000
MIN_POLL_SECONDS = 1.0
MAX_POLL_SECONDS = 3600.0


def follow_file(path, polls, poll_interval, tracker, emit, sleep=time.sleep):
    """Bounded read-only tail of a tree-series JSONL that another process appends to.

    Reads the file from the start, then only newly appended complete lines,
    opening it read-only on each poll; stops after ``polls`` polls. No
    sampling and no OS calls beyond reading this one file.
    """
    if not isinstance(polls, int) or isinstance(polls, bool) or not 1 <= polls <= MAX_POLLS:
        raise ValueError(f"polls must be 1..{MAX_POLLS}")
    if not MIN_POLL_SECONDS <= poll_interval <= MAX_POLL_SECONDS:
        raise ValueError(f"poll interval must be {MIN_POLL_SECONDS}..{MAX_POLL_SECONDS} seconds")
    path = Path(path)
    offset, pending, skipped = 0, b"", 0
    for poll in range(polls):
        if poll:
            sleep(poll_interval)
        with open(path, "rb") as handle:
            handle.seek(0, 2)
            size = handle.tell()
            if size < offset:
                raise ValueError("log file shrank while following; stopping")
            handle.seek(offset)
            chunk = handle.read(size - offset)
        offset += len(chunk)
        pending += chunk
        lines = pending.split(b"\n")
        pending = lines.pop()
        for raw in lines:
            text = raw.decode("utf-8-sig", errors="replace").strip()
            if not text:
                continue
            try:
                record = json.loads(text)
            except ValueError:
                skipped += 1
                emit({"kind": "pressure-warning", "warning": "unparsable line skipped"})
                continue
            event = tracker.feed(record)
            if event is not None:
                emit(event)
    return {"kind": "pressure-follow-summary", "mode": "read-only", "actions_taken": False,
            "basis": BASIS, "thresholds": tracker.thresholds(), "polls": polls,
            "poll_interval_s": poll_interval, "samples": tracker.samples,
            "alerts": tracker.alerts, "skipped_lines": skipped,
            "pending_partial_line": bool(pending.strip())}


def format_event(event):
    if event.get("kind") == "pressure-warning":
        return "WARNING: " + event["warning"]
    state = "+".join(event["reasons"]) or "ok"
    lines = [f"{event['local']} | READ ONLY | free {_fmt(event['free_gib'], ' GiB')} "
             f"commit {_fmt(event['commit_pct'], '%')} | {state} streak {event['streak']}"]
    if event["alert"]:
        lines.append(f"  ALERT (proposal thresholds, no action taken): sustained {state}; "
                     f"growth vs {event['growth_baseline']}:")
        for item in event["owner_growth"]["grown"]:
            lines.append(f"    {item['owner']}: +{item['delta_gib']} GiB (now {item['private_gib']} GiB)")
        if event["owner_growth"]["entered_top_list"]:
            lines.append("    entered top list: " + ", ".join(event["owner_growth"]["entered_top_list"]))
    return "\n".join(lines)


def format_follow_summary(summary):
    t = summary["thresholds"]
    return (f"Pressure follow done | READ ONLY | actions taken: NO | {summary['polls']} polls every "
            f"{summary['poll_interval_s']}s; samples {summary['samples']}, alerts {summary['alerts']}, "
            f"skipped lines {summary['skipped_lines']}; thresholds (proposal) free < "
            f"{t['free_below_gib']} GiB or commit > {t['commit_above_pct']}% for >= "
            f"{t['sustain_samples']} samples")
