"""Compute next-spawn timers for Dragon, Baron, Herald from the LCDA event log.

Spawn rules baseline (League patch ~14.x):
  Dragon  — first 5:00, respawn 5:00 after kill.
  Baron   — first 25:00, respawn 6:00 after kill.
  Herald  — first 14:00, respawn 6:00 after kill, despawns ~19:55.

These are deliberately patch-agnostic constants — Riot tweaks them every
season. When a future patch changes them, update the four constants below;
the rest of the logic stays the same.
"""
from __future__ import annotations

from dataclasses import dataclass

DRAGON_FIRST_SPAWN = 300.0
DRAGON_RESPAWN = 300.0

BARON_FIRST_SPAWN = 1500.0
BARON_RESPAWN = 360.0

HERALD_FIRST_SPAWN = 840.0
HERALD_RESPAWN = 360.0
HERALD_DESPAWN = 1195.0  # last possible spawn time


@dataclass(frozen=True)
class ObjectiveTimer:
    name: str
    next_spawn_seconds: float | None  # None = won't respawn this game
    last_killed_seconds: float | None
    last_killer: str | None = None
    detail: str | None = None  # e.g. "Earth", "Cloud"

    def remaining(self, game_time: float) -> float | None:
        if self.next_spawn_seconds is None:
            return None
        return max(0.0, self.next_spawn_seconds - game_time)

    def is_up(self, game_time: float) -> bool:
        rem = self.remaining(game_time)
        return rem is not None and rem <= 0.0


def _latest(events: list[dict], name: str) -> dict | None:
    matching = [e for e in events if e.get("EventName") == name]
    return matching[-1] if matching else None


def _dragon(events: list[dict]) -> ObjectiveTimer:
    kill = _latest(events, "DragonKill")
    if kill is None:
        return ObjectiveTimer(
            name="Dragon",
            next_spawn_seconds=DRAGON_FIRST_SPAWN,
            last_killed_seconds=None,
        )
    last = float(kill["EventTime"])
    return ObjectiveTimer(
        name="Dragon",
        next_spawn_seconds=last + DRAGON_RESPAWN,
        last_killed_seconds=last,
        last_killer=kill.get("KillerName"),
        detail=kill.get("DragonType"),
    )


def _baron(events: list[dict]) -> ObjectiveTimer:
    kill = _latest(events, "BaronKill")
    if kill is None:
        return ObjectiveTimer(
            name="Baron",
            next_spawn_seconds=BARON_FIRST_SPAWN,
            last_killed_seconds=None,
        )
    last = float(kill["EventTime"])
    return ObjectiveTimer(
        name="Baron",
        next_spawn_seconds=last + BARON_RESPAWN,
        last_killed_seconds=last,
        last_killer=kill.get("KillerName"),
    )


def _herald(events: list[dict], game_time: float) -> ObjectiveTimer:
    kill = _latest(events, "HeraldKill")
    if kill is None:
        # Before first spawn, or pre-despawn window — herald is upcoming.
        next_t = (
            HERALD_FIRST_SPAWN if game_time < HERALD_DESPAWN else None
        )
        return ObjectiveTimer(
            name="Herald",
            next_spawn_seconds=next_t,
            last_killed_seconds=None,
        )
    last = float(kill["EventTime"])
    next_t = last + HERALD_RESPAWN
    if next_t > HERALD_DESPAWN:
        next_t = None  # no further heralds this game
    return ObjectiveTimer(
        name="Herald",
        next_spawn_seconds=next_t,
        last_killed_seconds=last,
        last_killer=kill.get("KillerName"),
    )


def compute_objectives(
    events: list[dict],
    game_time: float,
) -> list[ObjectiveTimer]:
    """Build Dragon/Baron/Herald timers from the LCDA event log."""
    return [
        _dragon(events),
        _baron(events),
        _herald(events, game_time),
    ]
