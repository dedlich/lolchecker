"""Lane-opponent MIA detection (Charter B2 — companion to gank_window).

Where ``gank_window`` answers "where is the enemy jungler?", this module
answers "where is *my direct lane opponent?*" — the second of the two
"who is unaccounted for?" questions a pro player tracks every wave.

Detection signal
================
Per LCDA tick, each enemy's cumulative ``creep_score`` is compared to
the previous tick. The clock ``last_cs_at[champion_name]`` advances
whenever CS goes up. If the lane opponent's CS hasn't increased for
≥ 30 s while alive, they're missing from lane.

Why CS-delta beats other signals
--------------------------------
* Kill-event scanning (the gank_window approach) tells us if a player
  was *seen on the map*, not whether they're *in their lane*. A
  jungle-camp pull or a recall doesn't appear as a ChampionKill but
  is exactly the absence we want to surface.
* Map coordinates are not exposed by LCDA.
* ``creepScore`` includes minion *and* jungle camp kills — so a
  midlaner who pulled their own jungle camps still has CS ticking.
  The signal correctly differentiates "actively farming somewhere"
  from "moved to do something else".

Why 30 s / 60 s thresholds
--------------------------
30 s is the realistic minimum for a recall: ~8 s base channel + ~10 s
walk back to lane = ~25 s round trip in the early game. Anything
above 30 s of no CS while alive means they're doing something other
than coming back. 60 s is unambiguous — they're committed to a play
elsewhere (gank, drake setup, river vision, base race counterplay).

Out of scope
------------
* JUNGLE active player: junglers don't have a single lane opponent.
  This is covered by the gank_window rule from the other side.
* UTILITY active player: supports don't farm CS reliably, so flat CS
  is the normal state for them. Detection signal degrades to noise.
* Bot 2-vs-2: we follow the ADC (BOTTOM position), not the support.
  ADC presence is the "is the lane online" indicator; support roams
  are tracked separately by the team's overall map coverage.
"""
from __future__ import annotations

from dataclasses import dataclass

# Detection thresholds — pro-tuned, see module docstring for rationale.
LANE_PHASE_START_S: float = 90.0      # 1:30 — first wave hits lane
LANE_PHASE_END_S: float = 1200.0      # 20:00 — laning resolves
MIA_INFO_S: float = 30.0              # 30 s no CS → info
MIA_WARN_S: float = 60.0              # 60 s no CS → warn

# Active-player positions for which this rule fires. JUNGLE has no
# single opponent (gank_window covers the other side). UTILITY
# (support) doesn't farm reliably, so the CS-delta signal is unreliable.
_LANE_POSITIONS: frozenset[str] = frozenset({"TOP", "MIDDLE", "BOTTOM"})


@dataclass(frozen=True)
class LaneOpponentMia:
    """Active player's lane opponent has been absent from CS for too long."""
    opponent_name: str          # champion name for the UI
    seconds_mia: float          # how long since they last gained CS
    severity: str               # "info" or "warn"
    active_position: str        # TOP / MIDDLE / BOTTOM — drives advice text


def find_lane_opponent(active_position: str, enemies: list) -> object | None:
    """Pick the enemy at the same lane position as the active player.

    Bot lane has two enemies (BOTTOM ADC + UTILITY support); we follow
    the ADC since their CS is the lane-online indicator. JUNGLE / UTILITY
    active players are filtered before this is called, so we don't
    need to handle those cases here.
    """
    if active_position not in _LANE_POSITIONS:
        return None
    for p in enemies:
        if getattr(p, "position", "") == active_position:
            return p
    return None


def detect_lane_opponent_mia(
    *,
    active_position: str,
    enemies: list,
    game_time: float,
    prev_last_cs_at: dict[str, float],
    prev_cs: dict[str, int],
    prev_alive: dict[str, bool],
) -> tuple[LaneOpponentMia | None, dict[str, float], dict[str, int], dict[str, bool]]:
    """One-tick lane-MIA check. Returns ``(alert, …new state…)``.

    State carried across snapshots:
    * ``last_cs_at`` — game-time when each enemy's CS last went up.
      Drives the MIA computation. Reset on dead→alive transition so
      a fresh respawn doesn't start the clock from pre-death.
    * ``cs`` — last seen CS, used to detect deltas.
    * ``alive`` — was-alive last tick, used to detect respawn.

    Caller passes back the returned dicts on the next call. Empty dicts
    are fine for the first call.
    """
    # Position guard.
    if active_position not in _LANE_POSITIONS:
        return None, prev_last_cs_at, prev_cs, prev_alive
    # Phase guard — outside the laning window the signal is noise.
    if not (LANE_PHASE_START_S <= game_time <= LANE_PHASE_END_S):
        return None, prev_last_cs_at, prev_cs, prev_alive

    # Update state for every enemy this tick (not just the lane opponent —
    # cheap, and lets us track role swaps correctly without rebuilding state).
    new_last_cs_at = dict(prev_last_cs_at)
    new_cs = dict(prev_cs)
    new_alive = dict(prev_alive)
    for e in enemies:
        name = str(getattr(e, "champion_name", "") or "")
        if not name:
            continue
        cs = int(getattr(e, "creep_score", 0) or 0)
        is_alive = bool(getattr(e, "is_alive", True))

        # Dead→alive transition — reset MIA clock to "just respawned".
        was_alive = new_alive.get(name, True)
        if is_alive and not was_alive:
            new_last_cs_at[name] = game_time

        # CS went up → reset clock.
        prev = new_cs.get(name)
        if prev is None:
            # First sighting — anchor the clock so the first 30 s aren't
            # misread as absence on a midgame join.
            new_last_cs_at[name] = game_time
        elif cs > prev:
            new_last_cs_at[name] = game_time

        new_cs[name] = cs
        new_alive[name] = is_alive

    # Now decide whether to surface an alert for the lane opponent.
    opponent = find_lane_opponent(active_position, enemies)
    if opponent is None:
        return None, new_last_cs_at, new_cs, new_alive

    name = str(getattr(opponent, "champion_name", "") or "Gegner")
    is_alive = bool(getattr(opponent, "is_alive", True))
    if not is_alive:
        # Dead enemies don't warrant a "where are they?" alert — we
        # already know exactly where they are (at base, on a respawn timer).
        return None, new_last_cs_at, new_cs, new_alive

    last_at = new_last_cs_at.get(name, game_time)
    mia_s = max(0.0, game_time - last_at)

    if mia_s >= MIA_WARN_S:
        severity = "warn"
    elif mia_s >= MIA_INFO_S:
        severity = "info"
    else:
        return None, new_last_cs_at, new_cs, new_alive

    return (
        LaneOpponentMia(
            opponent_name=name,
            seconds_mia=mia_s,
            severity=severity,
            active_position=active_position,
        ),
        new_last_cs_at, new_cs, new_alive,
    )
