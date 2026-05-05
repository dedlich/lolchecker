"""Personal tilt / death-pattern detection (Charter B4 — Risk Detection).

This is a coaching signal, not a stats display. Pro players track their
own death cadence internally and adjust their risk-taking; this module
externalizes that instinct for solo-queue users who don't have it yet.

Pro patterns we detect (in increasing severity)
================================================

caution — *single early death in lane*
   One death isn't tilt, but it's a freeze-the-wave + ping-jungler
   moment. Lane phase only (< 14:00). The most important thing here
   is that the player doesn't try to "make it back" with a risky
   trade — that's how 0/1 becomes 0/3.

tilt — *2 deaths in 90 seconds (the "tilt window")*
   This is where most lost lanes happen. The player respawns, buys
   one component, walks back, sees a wave they think they can clear,
   the enemy is one step ahead, and dies again. Pro response: stay
   at base 20-30s longer than feels necessary, buy two components,
   set a vision ward on jungle entrance, hold the wave under tower
   until lvl-up parity is restored.

re-engage — *2 deaths within 60s ("1-and-done")*
   A more specific subset of tilt: died, respawned, walked into the
   exact same fight, died again. Indicates the player isn't reading
   the threat — usually because they're emotionally re-engaged. The
   recommended fix is the same as tilt + a 30s "do nothing" timer.

spiral — *3 deaths in 3 minutes OR 2 deaths in 60s*
   Hard tilt. Past this point the player is feeding momentum back
   to the enemy faster than the team can recover. Pro response: do
   NOT show in lane for 60s. Buy. Ward your jungle. Walk to the
   safest lane to clear stacked waves under tower. Mute self if
   raging.

bounty_lost — *died while on a 3+ killstreak (modifier flag)*
   Killing streaks carry a bounty (extra gold to the killer); losing
   a 3+ streak gives ~600+ extra gold to the enemy. This modifier
   adds urgency to whatever severity we already detected.

solo_death — *died alone with no ally involvement (modifier flag)*
   No allies in the kill event's Assisters AND no ally death within
   ±5s of yours. Indicates a positioning mistake (caught out in
   side-lane / fog) rather than a lost teamfight. Different fix:
   stop side-laning, group with team.

Out of scope
------------
* Tower-dive death detection (no positional data in LCDA).
* "Throw" detection (intentional feeding) — not a coaching target.
* Recovery suggestions based on champion-specific build paths.
"""
from __future__ import annotations

from dataclasses import dataclass

# Time windows used by the tier ladder, in seconds.
WINDOW_RE_ENGAGE_S: float = 60.0   # 2 deaths in 60s = re-engage / spiral
WINDOW_TILT_S: float = 90.0        # 2 deaths in 90s = tilt window
WINDOW_SPIRAL_S: float = 180.0     # 3 deaths in 3 min = spiral

# Severity thresholds.
TILT_DEATH_COUNT: int = 2
SPIRAL_RECENT_180_COUNT: int = 3
# 3 deaths in 60s = mass-feeding (worse than re-engage which is "exactly 2").
SPIRAL_RECENT_60_COUNT: int = 3

# Lane phase upper bound — caution tier only fires while still in lane.
LANE_PHASE_END_S: float = 840.0    # 14:00

# Bounty threshold — Riot's bounty system kicks in at 3 unanswered kills.
BOUNTY_KILL_STREAK: int = 3

# Solo-death window — within ±5s of an ally death, the death is
# considered "shared" (a teamfight loss, not a positioning mistake).
SOLO_DEATH_TEAMFIGHT_WINDOW_S: float = 5.0


@dataclass(frozen=True)
class TiltState:
    """Snapshot of the active player's death pattern this tick.

    ``severity`` is the user-facing tier:
      * ``"ok"``       — no recent deaths worth surfacing
      * ``"caution"``  — one lane death; stay safe
      * ``"tilt"``     — 2 deaths in 90s (the classic tilt window)
      * ``"re_engage"`` — 2 deaths in 60s (1-and-done pattern)
      * ``"spiral"``   — 3 deaths in 180s OR 2 deaths in 60s
    """
    severity: str
    deaths_total: int
    deaths_recent_60s: int
    deaths_recent_90s: int
    deaths_recent_180s: int
    last_death_at: float          # game_time of most recent death
    bounty_lost: bool             # died while on a 3+ killstreak
    solo_death: bool              # last death had no ally involvement


def _is_active_death(evt: dict, active_ids: set[str]) -> bool:
    if evt.get("EventName") != "ChampionKill":
        return False
    return (evt.get("VictimName") or "") in active_ids


def _is_active_kill(evt: dict, active_ids: set[str]) -> bool:
    if evt.get("EventName") != "ChampionKill":
        return False
    return (evt.get("KillerName") or "") in active_ids


def _kill_streak_before(
    last_death_t: float, kill_times: list[float], death_times: list[float]
) -> int:
    """Count active player's kills since the previous death (or game start)
    up to ``last_death_t``. Drives the bounty-lost modifier.
    """
    # Find the previous death (the one before last_death_t).
    prior = [t for t in death_times if t < last_death_t]
    floor = max(prior) if prior else 0.0
    return sum(1 for kt in kill_times if floor < kt < last_death_t)


def _was_solo_death(
    last_death_t: float,
    last_death_evt: dict,
    ally_ids: set[str],
    death_events_by_team: list[tuple[float, str]],
) -> bool:
    """True if the last death had no ally involvement and no nearby (±5s)
    ally death — meaning the player got caught alone, not in a teamfight.

    ``death_events_by_team`` is a list of ``(EventTime, victim_team_id)``
    sorted by time. We don't have positional data, so the heuristic is
    purely temporal: if no ally died within the teamfight window, the
    active player's death wasn't part of a fight.
    """
    assisters = last_death_evt.get("Assisters") or []
    if not isinstance(assisters, list):
        assisters = []
    # Any ally credited as assister? Then it was at least a 2-vs-N skirmish.
    if any(a in ally_ids for a in assisters):
        return False
    # Any ally died within ±5s of our death?
    for evt_t, team_marker in death_events_by_team:
        if team_marker != "ally":
            continue
        if abs(evt_t - last_death_t) <= SOLO_DEATH_TEAMFIGHT_WINDOW_S and evt_t != last_death_t:
            return False
    return True


def detect_tilt(
    *,
    active_ids: set[str],
    ally_ids: set[str],
    events: list[dict],
    game_time: float,
) -> TiltState | None:
    """Compute the active player's tilt state from cumulative kill events.

    Returns ``None`` only when there's no data to score (no deaths yet).
    Otherwise returns a ``TiltState`` whose ``severity`` may be ``"ok"``
    (no rec needed), ``"caution"``, ``"tilt"``, ``"re_engage"``, or
    ``"spiral"``.

    ``active_ids`` should contain both the active player's
    ``summoner_name`` and ``champion_name`` so we match regardless of
    which identifier LCDA uses in events for a given API version.
    ``ally_ids`` is the same union for every ally (excluding the active
    player). Used for solo-death detection.
    """
    if not active_ids:
        return None

    death_times: list[float] = []
    kill_times: list[float] = []
    last_death_evt: dict | None = None
    death_events_by_team: list[tuple[float, str]] = []  # for solo-death heuristic

    for evt in events:
        if evt.get("EventName") != "ChampionKill":
            continue
        t = float(evt.get("EventTime") or 0.0)
        victim = evt.get("VictimName") or ""
        if victim in active_ids:
            death_times.append(t)
            last_death_evt = evt
        elif victim in ally_ids:
            death_events_by_team.append((t, "ally"))
        if (evt.get("KillerName") or "") in active_ids:
            kill_times.append(t)

    if not death_times:
        return None  # no deaths yet — nothing to score

    death_times.sort()
    kill_times.sort()
    last_death_at = death_times[-1]
    deaths_total = len(death_times)

    recent_60 = sum(1 for t in death_times if game_time - t <= WINDOW_RE_ENGAGE_S)
    recent_90 = sum(1 for t in death_times if game_time - t <= WINDOW_TILT_S)
    recent_180 = sum(1 for t in death_times if game_time - t <= WINDOW_SPIRAL_S)

    # Bounty modifier — was on a kill-streak when the latest death happened.
    streak = _kill_streak_before(last_death_at, kill_times, death_times)
    bounty_lost = streak >= BOUNTY_KILL_STREAK

    # Solo-death modifier — last death had no ally involvement.
    solo = False
    if last_death_evt is not None and ally_ids:
        solo = _was_solo_death(
            last_death_at, last_death_evt, ally_ids, death_events_by_team,
        )

    # Tier ladder — most severe first.
    if recent_60 >= SPIRAL_RECENT_60_COUNT or recent_180 >= SPIRAL_RECENT_180_COUNT:
        severity = "spiral"
    elif recent_60 >= TILT_DEATH_COUNT:
        # 2 deaths in 60s but not yet enough for spiral → re-engage pattern.
        severity = "re_engage"
    elif recent_90 >= TILT_DEATH_COUNT:
        severity = "tilt"
    elif recent_90 >= 1 and game_time <= LANE_PHASE_END_S:
        severity = "caution"
    else:
        severity = "ok"

    return TiltState(
        severity=severity,
        deaths_total=deaths_total,
        deaths_recent_60s=recent_60,
        deaths_recent_90s=recent_90,
        deaths_recent_180s=recent_180,
        last_death_at=last_death_at,
        bounty_lost=bounty_lost,
        solo_death=solo,
    )
