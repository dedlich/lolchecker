"""Enemy jungler MIA detection — gank window alert (Charter B2).

LCDA does not expose real-time map coordinates, so we infer the enemy
jungler's activity from the cumulative ChampionKill event log. When the
jungler hasn't appeared in any kill/assist/death event for long enough
AND is currently alive, they are unaccounted for on the map — the
classic "ss" call situation.

State model
-----------
Two pieces of state persist across ticks (owned by LcdaSource):

* ``jungler_last_seen_gt`` — the game-time (in seconds) of the most recent
  event that placed the jungler somewhere on the map. Updated when:
    - a ChampionKill event lists the jungler as killer, victim, or assister
    - the jungler transitions dead → alive (respawn resets the MIA clock
      because they're now known to be at base walking back)

* ``jungler_was_alive`` — bool from the previous tick, to detect the
  dead → alive transition.

Alert thresholds
----------------
* 60 s unseen → info   (heads-up, but not critical)
* 90 s unseen → warn   (gank approach window is now open)

Laning phase only: 4:00 – 20:00. Before 4:00 the jungler is nearly
always farming their first clear; after 20:00 teams group and the
laning model breaks down.

Only fires for lane roles (TOP, MIDDLE, BOTTOM). The active jungler
or support (UTILITY) has different spatial awareness needs.
"""
from __future__ import annotations

from dataclasses import dataclass

GANK_PHASE_START_S: float = 240.0   # 4 min — first clear typically done
GANK_PHASE_END_S: float = 1200.0    # 20 min — laning phase over
MIA_INFO_S: float = 60.0            # 60 s unseen → info
MIA_WARN_S: float = 90.0            # 90 s unseen → warn

_GANKABLE_POSITIONS: frozenset[str] = frozenset({"TOP", "MIDDLE", "BOTTOM"})


@dataclass(frozen=True)
class GankAlert:
    """Enemy jungler has been unaccounted-for long enough to warrant a warning."""
    jungler_name: str     # champion name, for the UI label
    seconds_mia: float    # how long since last combat-event sighting
    severity: str         # "info" or "warn"


def find_enemy_jungler(enemies: list) -> object | None:
    """Return the first enemy with position == 'JUNGLE', or None."""
    for p in enemies:
        if getattr(p, "position", "") == "JUNGLE":
            return p
    return None


def last_combat_time(jungler_ids: set[str], events: list[dict]) -> float:
    """Scan the cumulative event log for the most recent game-time at which
    the jungler appeared as killer, victim, or assister in a ChampionKill.

    ``jungler_ids`` contains both summoner_name and champion_name so we match
    regardless of which identifier LCDA uses in a given API version.
    Returns 0.0 if the jungler has never appeared in a kill event.
    """
    best = 0.0
    for evt in events:
        if evt.get("EventName") != "ChampionKill":
            continue
        t = float(evt.get("EventTime") or 0.0)
        if t <= best:
            continue
        killer = evt.get("KillerName") or ""
        victim = evt.get("VictimName") or ""
        assisters = evt.get("Assisters") or []
        if not isinstance(assisters, list):
            assisters = []
        if (killer in jungler_ids
                or victim in jungler_ids
                or any(a in jungler_ids for a in assisters)):
            best = t
    return best


def detect_gank_risk(
    *,
    active_position: str,
    enemies: list,
    events: list[dict],
    game_time: float,
    prev_last_seen_gt: float,
    prev_was_alive: bool,
) -> tuple[GankAlert | None, float, bool]:
    """One-tick gank-window check.

    Returns ``(alert, new_last_seen_gt, new_was_alive)``.

    ``new_last_seen_gt`` and ``new_was_alive`` must be stored by the caller
    and passed back as ``prev_*`` on the next tick.
    """
    # Only lane roles need gank warnings.
    if active_position not in _GANKABLE_POSITIONS:
        return None, prev_last_seen_gt, prev_was_alive

    if not (GANK_PHASE_START_S <= game_time <= GANK_PHASE_END_S):
        return None, prev_last_seen_gt, prev_was_alive

    jungler = find_enemy_jungler(enemies)
    if jungler is None:
        return None, prev_last_seen_gt, prev_was_alive

    champion_name = str(getattr(jungler, "champion_name", "") or "Jungler")
    summoner_name = str(getattr(jungler, "summoner_name", "") or "")
    is_alive = bool(getattr(jungler, "is_alive", True))

    # Build the identifier set used to match this jungler in event records.
    jungler_ids: set[str] = set()
    if champion_name:
        jungler_ids.add(champion_name)
    if summoner_name:
        jungler_ids.add(summoner_name)

    # Dead → alive transition: jungler just respawned, so their MIA clock
    # resets — they're known to be at base walking back.
    new_last_seen_gt = prev_last_seen_gt
    if is_alive and not prev_was_alive:
        new_last_seen_gt = game_time

    # Advance last_seen_gt from kill events.
    event_last = last_combat_time(jungler_ids, events) if jungler_ids else 0.0
    if event_last > new_last_seen_gt:
        new_last_seen_gt = event_last

    new_was_alive = is_alive

    # No alert while jungler is dead — they can't gank.
    if not is_alive:
        return None, new_last_seen_gt, new_was_alive

    # MIA duration — capped at game_time so early-game noise is avoided.
    baseline = new_last_seen_gt or GANK_PHASE_START_S
    mia_s = max(0.0, game_time - baseline)

    if mia_s >= MIA_WARN_S:
        severity = "warn"
    elif mia_s >= MIA_INFO_S:
        severity = "info"
    else:
        return None, new_last_seen_gt, new_was_alive

    return (
        GankAlert(jungler_name=champion_name, seconds_mia=mia_s, severity=severity),
        new_last_seen_gt,
        new_was_alive,
    )
