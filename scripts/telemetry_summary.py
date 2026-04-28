"""Offline aggregator for telemetry.jsonl.

Computes the derived metrics from the spec's section #3:

  * avg_widget_visibility_duration   — mean show→hide duration per widget
  * pct_low_confidence               — fraction of session where any LOW
                                        band was active
  * overlap_score                    — avg simultaneous widgets visible
                                        during fight windows
  * cognitive_density_index          — widgets-visible weighted by time
  * idle_vs_combat_render_ratio      — ratio of time spent idle vs in fights

Run:
    .venv/bin/python scripts/telemetry_summary.py
    .venv/bin/python scripts/telemetry_summary.py /path/to/telemetry.jsonl

Output is stdout JSON — easy to grep / pipe / diff between sessions.
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from champ_assistant.telemetry import telemetry_path  # noqa: E402


def _load(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def avg_widget_visibility(events: list[dict]) -> dict[str, float]:
    """Pair each widget_shown with the next widget_hidden for the same
    widget_id and average the deltas. Unmatched shown events (widget
    still visible at session end) get capped at session end."""
    by_widget: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for e in events:
        if e["event"] in ("widget_shown", "widget_hidden"):
            wid = e["payload"].get("widget_id", "?")
            by_widget[wid].append((e["event"], e["ts"]))

    if not events:
        return {}
    session_end = events[-1]["ts"]
    out: dict[str, float] = {}
    for wid, seq in by_widget.items():
        durations: list[float] = []
        open_at: float | None = None
        for ev, ts in seq:
            if ev == "widget_shown":
                open_at = ts
            elif ev == "widget_hidden" and open_at is not None:
                durations.append(ts - open_at)
                open_at = None
        if open_at is not None:
            durations.append(session_end - open_at)
        if durations:
            out[wid] = sum(durations) / len(durations)
    return out


def pct_low_confidence(events: list[dict]) -> float:
    """Fraction of session time where AT LEAST ONE camp was in LOW band.
    Uses confidence_band_change as the only signal — assumes initial
    state is HIGH (matches engine.INITIAL_CONFIDENCE)."""
    if not events:
        return 0.0
    session_start = events[0]["ts"]
    session_end = events[-1]["ts"]
    total = max(0.0001, session_end - session_start)

    in_low: dict[str, bool] = {}
    last_change_ts = session_start
    low_active_total = 0.0
    any_low_now = False

    for e in events:
        if e["event"] != "confidence_band_change":
            continue
        camp = e["payload"]["camp_id"]
        new_band = e["payload"]["to"]
        prev_any_low = any(in_low.values())
        if prev_any_low:
            low_active_total += e["ts"] - last_change_ts
        in_low[camp] = (new_band == "LOW")
        last_change_ts = e["ts"]
        any_low_now = any(in_low.values())

    if any_low_now:
        low_active_total += session_end - last_change_ts

    return low_active_total / total


def fight_window_metrics(events: list[dict]) -> dict[str, float]:
    """Compute total fight time + idle time + overlap score during fights.
    Overlap score = avg number of widgets visible during fight windows."""
    visible: dict[str, bool] = {}
    in_fight = False
    fight_start: float | None = None
    fight_total = 0.0
    overlap_samples: list[int] = []

    if not events:
        return {"fight_seconds": 0.0, "overlap_score": 0.0, "idle_seconds": 0.0}

    session_start = events[0]["ts"]
    session_end = events[-1]["ts"]

    for e in events:
        if e["event"] == "widget_shown":
            visible[e["payload"]["widget_id"]] = True
        elif e["event"] == "widget_hidden":
            visible[e["payload"]["widget_id"]] = False
        elif e["event"] == "fight_window_detected":
            now_active = bool(e["payload"].get("active"))
            if now_active and not in_fight:
                in_fight = True
                fight_start = e["ts"]
                overlap_samples.append(sum(1 for v in visible.values() if v))
            elif not now_active and in_fight:
                in_fight = False
                if fight_start is not None:
                    fight_total += e["ts"] - fight_start
                fight_start = None

    if in_fight and fight_start is not None:
        fight_total += session_end - fight_start

    total = max(0.0001, session_end - session_start)
    return {
        "fight_seconds": fight_total,
        "idle_seconds": total - fight_total,
        "overlap_score": (
            sum(overlap_samples) / len(overlap_samples)
            if overlap_samples else 0.0
        ),
    }


def cognitive_density_index(events: list[dict]) -> float:
    """Time-weighted average of (widgets visible). Higher = more
    cognitive load on screen on average across the session."""
    if not events:
        return 0.0
    visible: dict[str, bool] = {}
    last_ts = events[0]["ts"]
    weighted_sum = 0.0
    total_time = 0.0
    for e in events:
        delta = e["ts"] - last_ts
        if delta > 0:
            weighted_sum += sum(1 for v in visible.values() if v) * delta
            total_time += delta
        last_ts = e["ts"]
        if e["event"] == "widget_shown":
            visible[e["payload"]["widget_id"]] = True
        elif e["event"] == "widget_hidden":
            visible[e["payload"]["widget_id"]] = False
    if total_time <= 0:
        return 0.0
    return weighted_sum / total_time


def main(argv: list[str]) -> int:
    if len(argv) > 1:
        path = Path(argv[1])
    else:
        path = telemetry_path()

    events = _load(path)
    if not events:
        print(json.dumps({"error": f"no telemetry events at {path}"}))
        return 1

    fight = fight_window_metrics(events)
    summary = {
        "session": {
            "events": len(events),
            "start_ts": events[0]["ts"],
            "end_ts": events[-1]["ts"],
            "duration_seconds": events[-1]["ts"] - events[0]["ts"],
        },
        "avg_widget_visibility_duration": avg_widget_visibility(events),
        "pct_low_confidence": pct_low_confidence(events),
        "fight_seconds": fight["fight_seconds"],
        "idle_seconds": fight["idle_seconds"],
        "overlap_score": fight["overlap_score"],
        "cognitive_density_index": cognitive_density_index(events),
        "idle_vs_combat_render_ratio": (
            fight["idle_seconds"] / fight["fight_seconds"]
            if fight["fight_seconds"] > 0 else float("inf")
        ),
    }
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
