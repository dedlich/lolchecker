"""Lane-state rules — CS, level, plate, matchup, MIA, gank risk.

Seven rules + helpers covering the active player's lane situation:
farming efficiency (``rule_cs_deficit``), level matchup
(``rule_lane_level_advantage``), enemy turret pressure
(``rule_lane_pressure``, ``rule_ally_turret_lost``), per-enemy
matchup feedback (``rule_matchup_mismatch``), the plate-window
reminder (``rule_plate_window``), opponent MIA tracking
(``rule_lane_opponent_mia``), and gank risk (``rule_gank_risk``).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ....lcda.source import LcdaSnapshot

from .._core import (
    ALLY_TURRET_ALERT_WINDOW_S,
    Recommendation,
    _active_player,
    _enemy_turrets_down,
    _recent_ally_turret_losses,
    _team_gold_diff,
)
from .._state import _MATCHUP_MISMATCH_HYSTERESIS, _PLATE_WINDOW_HYSTERESIS


# ─── CS deficit thresholds ──────────────────────────────────────────────────
CS_MIN_GAME_TIME_S = 240.0         # 4 min — earliest meaningful sample
CS_LATE_SUPPRESS_S = 1680.0        # 28 min — past here, grouping reduces CS
CS_EXPECTED_PER_MIN = 8.0          # emerald+ farm rate
CS_INFO_DEFICIT = 2.0              # < 6.0/min when expected 8.0
CS_WARN_DEFICIT = 3.5              # < 4.5/min when expected 8.0
CS_DEFICIT_TTL_S = 30.0
_NON_CS_POSITIONS: frozenset[str] = frozenset({"UTILITY", "JUNGLE"})

# ─── Lane-level advantage thresholds (laning phase only) ────────────────────
LANE_LEVEL_ADV_THRESHOLD = 2      # 2-level edge = real advantage
LANE_LEVEL_DOM_THRESHOLD = 3      # 3-level edge = dominance
LANE_PHASE_CUTOFF_S = 1200.0      # 20 min


def rule_cs_deficit(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """Warn when the active laner's CS/min is significantly below the
    expected farming rate (≈8 CS/min for lane roles). Excludes supports
    and junglers who have different gold income paths. Suppressed before
    4 min (not enough waves) and after 28 min (grouping reduces farm).
    """
    game_time = getattr(snapshot, "game_time", 0.0)
    if game_time < CS_MIN_GAME_TIME_S or game_time >= CS_LATE_SUPPRESS_S:
        return None

    player = _active_player(snapshot)
    if player is None:
        return None

    position = (getattr(player, "position", "") or "").upper()
    if position in _NON_CS_POSITIONS:
        return None

    cs = getattr(player, "creep_score", 0)
    if not isinstance(cs, int) or cs < 0:
        return None

    cs_per_min = cs / (game_time / 60.0)
    deficit = CS_EXPECTED_PER_MIN - cs_per_min
    if deficit < CS_INFO_DEFICIT:
        return None

    severity = "warn" if deficit >= CS_WARN_DEFICIT else "info"
    min_elapsed = int(game_time / 60)
    expected_cs = int(CS_EXPECTED_PER_MIN * min_elapsed)

    return Recommendation(
        text=f"CS {cs} ({cs_per_min:.1f}/min, Ziel {CS_EXPECTED_PER_MIN:.0f}/min) — farme Wellen",
        severity=severity,
        category="lane",
        confidence=0.75,
        risk="LOW",
        ttl_s=CS_DEFICIT_TTL_S,
        kind="cs_deficit",
        reasons=(
            f"{cs_per_min:.1f} CS/min (Ziel: {CS_EXPECTED_PER_MIN:.0f}/min)",
            f"Minute {min_elapsed}: {cs} CS, Soll ~{expected_cs}",
        ),
    )


def rule_lane_level_advantage(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """Surface a meaningful level edge in the active player's lane matchup.

    Level 2+ lead over the lane opponent is a reliable all-in / trade
    window that many players miss. Level 2+ deficit is an additional
    safety reminder on top of the team-average rule_level_deficit.
    Only fires during the laning phase (< 20 min) and only when LCDA
    exposes position data for both players so the matchup is unambiguous.
    """
    game_time = getattr(snapshot, "game_time", 0.0)
    if game_time >= LANE_PHASE_CUTOFF_S:
        return None

    player = _active_player(snapshot)
    if player is None:
        return None

    position = (getattr(player, "position", "") or "").upper()
    if not position or position in _NON_CS_POSITIONS:
        return None

    enemies = list(getattr(snapshot, "enemies", []) or [])
    lane_opp = next(
        (e for e in enemies if (getattr(e, "position", "") or "").upper() == position),
        None,
    )
    if lane_opp is None:
        return None

    my_level = int(getattr(player, "level", 0) or 0)
    opp_level = int(getattr(lane_opp, "level", 0) or 0)
    diff = my_level - opp_level

    if abs(diff) < LANE_LEVEL_ADV_THRESHOLD:
        return None

    opp_name = getattr(lane_opp, "champion_name", "") or "Gegner"

    if diff >= LANE_LEVEL_DOM_THRESHOLD:
        return Recommendation(
            text=f"Level-Dominanz +{diff} vs {opp_name} — All-in erzwingen",
            severity="warn",
            category="lane",
            confidence=0.82,
            risk="LOW",
            ttl_s=25.0,
            kind="lane_level_adv",
            reasons=(
                f"Du: Level {my_level}, {opp_name}: Level {opp_level}",
                f"+{diff} Level = statistisch gewonnener All-in",
            ),
        )
    if diff >= LANE_LEVEL_ADV_THRESHOLD:
        return Recommendation(
            text=f"Level-Vorteil +{diff} vs {opp_name} — Trade-Fenster",
            severity="info",
            category="lane",
            confidence=0.75,
            risk="LOW",
            ttl_s=20.0,
            kind="lane_level_adv",
            reasons=(
                f"Du: Level {my_level}, {opp_name}: Level {opp_level}",
                "Level-Edge: Trade erzwingen oder Turm-Plates nehmen",
            ),
        )
    return Recommendation(
        text=f"Level-Nachteil {diff} vs {opp_name} — Safe farmen",
        severity="warn",
        category="lane",
        confidence=0.80,
        risk="HIGH",
        ttl_s=20.0,
        kind="lane_level_disadv",
        reasons=(
            f"Du: Level {my_level}, {opp_name}: Level {opp_level}",
            f"{diff} Level = {opp_name} gewinnt jeden Trade",
        ),
    )


def rule_lane_pressure(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """Enemy outer or inner turrets down → push that lane for objectives.

    Fully open lane (both outer + inner fallen) signals an inhib threat
    and forces enemy rotations — use it to enable drake/baron vision.
    Partial open (only outer) is an info nudge to send waves.
    """
    active_team = (getattr(snapshot, "active_team", "") or "")
    if not active_team:
        return None
    turrets_down = _enemy_turrets_down(snapshot)
    if not turrets_down:
        return None
    fully_open = [lane for lane, n in turrets_down.items() if n >= 2]
    partially_open = [lane for lane, n in turrets_down.items() if n == 1]
    if fully_open:
        lanes_str = " + ".join(sorted(fully_open))
        return Recommendation(
            text=f"{lanes_str}-Lane offen bis Inhib — Pressure + Obj-Vision!",
            severity="warn",
            category="lane",
            confidence=0.82,
            risk="LOW",
            ttl_s=60.0,
            kind="lane_open",
            reasons=(
                f"Enemy {lanes_str}: Outer + Inner Tower fallen",
                "Inhib angreifbar — zwingt Rotationen",
                "Super-Minions nach Inhib = passiver Pressure",
            ),
        )
    if partially_open:
        lanes_str = " + ".join(sorted(partially_open))
        return Recommendation(
            text=f"{lanes_str}-Lane Outer down — Side-Waves pushen",
            severity="info",
            category="lane",
            confidence=0.74,
            risk="LOW",
            ttl_s=45.0,
            kind="lane_open",
            reasons=(
                f"Enemy {lanes_str}: Outer Tower fallen",
                "Side-Waves erzeugen Rotationsdruck",
            ),
        )
    return None


def rule_ally_turret_lost(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """Enemy destroyed one of OUR turrets within the last 60 seconds.

    Fires a short-lived defensive nudge: recall to clear the wave, prevent
    the enemy from extending the advantage. Severity scales with turret tier:
      P1 (Outer)  → info   — wave-clear + rotate
      P2 (Inner)  → warn   — base is now reachable
      P3 (Inhib)  → alert  — inhibitor turret gone, base siege imminent

    Only fires within ALLY_TURRET_ALERT_WINDOW_S (60s) of the kill so the
    signal doesn't linger for the rest of the game. Not suppressed by
    numbers_disadv (defensive signals remain relevant while short-handed).
    """
    losses = _recent_ally_turret_losses(snapshot)
    if not losses:
        return None

    tier_rank = {"P3": 3, "P2": 2, "P1": 1}
    losses.sort(key=lambda t: tier_rank.get(t[1], 0), reverse=True)
    lane, tier, _side, evt_time = losses[0]
    game_time = float(getattr(snapshot, "game_time", 0.0) or 0.0)
    age_s = int(game_time - evt_time)
    gold = _team_gold_diff(snapshot)

    if tier == "P3":
        return Recommendation(
            text=f"Inhib-Turm {lane} verloren — SOFORT Basis verteidigen!",
            severity="alert",
            category="safety",
            confidence=0.88,
            risk="HIGH",
            ttl_s=max(0.0, ALLY_TURRET_ALERT_WINDOW_S - age_s),
            kind="ally_turret_lost",
            reasons=(
                f"Gegner zerstörte unseren {lane} Inhib-Turm (vor {age_s}s)",
                "Inhib jetzt angreifbar — Super-Minions drohen!",
                f"Gold-Diff: {gold:+d}",
            ),
        )
    if tier == "P2":
        return Recommendation(
            text=f"Inner {lane}-Turm verloren — Wave claren, Basis absichern",
            severity="warn",
            category="safety",
            confidence=0.82,
            risk="HIGH",
            ttl_s=max(0.0, ALLY_TURRET_ALERT_WINDOW_S - age_s),
            kind="ally_turret_lost",
            reasons=(
                f"Gegner zerstörte unseren {lane} Inner Tower (vor {age_s}s)",
                "Lane offen bis Inhib-Turm — Wellen drohen Base",
                f"Gold-Diff: {gold:+d}",
            ),
        )
    return Recommendation(
        text=f"Outer {lane}-Turm verloren — Welle claren, dann reagieren",
        severity="info",
        category="safety",
        confidence=0.74,
        risk="MEDIUM",
        ttl_s=max(0.0, ALLY_TURRET_ALERT_WINDOW_S - age_s),
        kind="ally_turret_lost",
        reasons=(
            f"Gegner zerstörte unseren {lane} Outer Tower (vor {age_s}s)",
            "Lane jetzt nur noch Inner Tower — reagieren!",
            f"Gold-Diff: {gold:+d}",
        ),
    )


# ─── Matchup-mismatch thresholds ─────────────────────────────────────────────
# Pros distinguish "I'm tilting" from "I'm losing this lane specifically".
# A 0-3 score with all 3 deaths to the same enemy is a hardstomp — the
# coaching is different from a 0-3 spread across team fights.
MISMATCH_DEFICIT_INFO: int = 2   # net 2 = you're behind in the matchup
MISMATCH_DEFICIT_WARN: int = 3   # net 3+ = lane is lost, defensive only


def _matchup_deficit(active_ids: set[str], events: list[dict]) -> dict[str, int]:
    """For each enemy who has interacted with the active player in
    ``ChampionKill`` events, return ``deaths_from_them − kills_on_them``.

    Positive deficits = you are losing the matchup against that enemy.
    Negative or zero = you're even or winning. Only enemies appearing in
    at least one event are returned.
    """
    deficits: dict[str, int] = {}
    for evt in events:
        if evt.get("EventName") != "ChampionKill":
            continue
        killer = evt.get("KillerName") or ""
        victim = evt.get("VictimName") or ""
        if victim in active_ids and killer:
            deficits[killer] = deficits.get(killer, 0) + 1
        elif killer in active_ids and victim:
            deficits[victim] = deficits.get(victim, 0) - 1
    return deficits


def rule_matchup_mismatch(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """Surface "you're losing the lane to a specific enemy" once per
    deficit-tier per matchup (Charter B5 — matchup awareness).

    Difference vs ``rule_tilt_detection``:
      * Tilt:    aggregate death cadence — "you keep dying"
      * Matchup: per-killer deficit — "you keep dying *to this enemy*"

    A 0-3 score with all three deaths to one enemy = hardstomp matchup.
    A 0-3 spread across team fights = just tilt, this rule stays silent.
    Both can fire together when the player both is on a death streak AND
    the streak comes mostly from one opponent — the messages are
    complementary (tilt = "stop fighting"; mismatch = "you specifically
    can't 1v1 *this* enemy, freeze the wave and wait for help").

    Tier ladder (deficit = deaths_from_X − kills_on_X):
      * deficit 2  → info — "X tötet dich oft, defensiv farmen"
      * deficit 3+ → warn — "X dominiert dich, Lane verloren, Hilfe nötig"

    Per-enemy hysteresis fires once per tier per game; subsequent
    deaths to the same enemy at the same tier don't re-spam. The
    deficit can shrink (you kill them) which doesn't auto-rearm —
    once flagged, the matchup info stays useful.
    """
    active = _active_player(snapshot)
    if active is None:
        return None
    sn = str(getattr(active, "summoner_name", "") or "")
    cn = str(getattr(active, "champion_name", "") or "")
    active_ids: set[str] = {x for x in (sn, cn) if x}
    if not active_ids:
        return None

    events = list(getattr(snapshot, "raw_events", []) or [])
    deficits = _matchup_deficit(active_ids, events)
    if not deficits:
        return None

    h = _MATCHUP_MISMATCH_HYSTERESIS
    best: tuple[int, int, str] | None = None
    for name, deficit in deficits.items():
        if deficit < MISMATCH_DEFICIT_INFO:
            continue
        if deficit >= MISMATCH_DEFICIT_WARN:
            tier = MISMATCH_DEFICIT_WARN
        else:
            tier = MISMATCH_DEFICIT_INFO
        if tier <= h.last_fired_tier.get(name, 0):
            continue
        candidate = (tier, deficit, name)
        if best is None or candidate > best:
            best = candidate

    if best is None:
        return None
    tier, deficit, name = best
    h.last_fired_tier[name] = tier

    if tier >= MISMATCH_DEFICIT_WARN:
        text = (
            f"{name} dominiert dich ({deficit} Diff) — "
            f"Lane verloren. Welle freezen, Hilfe pingen, kein Trade."
        )
        severity, ttl_s, confidence, risk = "warn", 35.0, 0.90, "HIGH"
    else:
        text = (
            f"{name} tötet dich oft ({deficit} Diff) — "
            f"Matchup-Vorsicht: defensiv farmen, Jungler-Hilfe einplanen."
        )
        severity, ttl_s, confidence, risk = "info", 30.0, 0.80, "MEDIUM"

    return Recommendation(
        text=text,
        severity=severity,
        category="lane",
        confidence=confidence,
        risk=risk,
        ttl_s=ttl_s,
        kind="matchup_mismatch",
        reasons=(
            f"Tode gegen {name}: {deficit} mehr als Kills auf {name}",
            "Matchup-Mismatch isolieren von Tilt: das ist diese Lane, nicht das Spiel",
            "Welle freezen + Jungle-Hilfe = einziger gesunder Komeback-Pfad",
        ),
    )


# ─── Plate-window thresholds ────────────────────────────────────────────────
# Outer turret plates exist 0:00 – 14:00. Each pops for 160g + a chunk of the
# turret's HP. After 14:00 plates despawn — uncashed plates are pure waste.
PLATE_WINDOW_OPEN_S: float = 780.0    # 13:00 — final-call reminder kicks in
PLATE_WINDOW_CLOSE_S: float = 840.0   # 14:00 — plates despawn (Riot fixed)


def rule_plate_window(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """Fire once at ~13:00 game time to remind about despawning plates.

    The most expensive lesson early-mid game players learn: plates fall
    off at 14:00 and any plate you didn't pop is gone forever. At 13:00
    you have ~60 s to crash a wave + take whatever plates you can reach.

    Single-fire (hysteresis) so the reminder doesn't spam. Doesn't fire
    before 13:00 (less urgent — you have time) or after 14:00 (too late).
    """
    game_time = float(getattr(snapshot, "game_time", 0.0) or 0.0)
    if game_time < PLATE_WINDOW_OPEN_S or game_time >= PLATE_WINDOW_CLOSE_S:
        return None
    h = _PLATE_WINDOW_HYSTERESIS
    if h.fired:
        return None
    h.fired = True

    remaining = int(PLATE_WINDOW_CLOSE_S - game_time)
    return Recommendation(
        text=(
            f"Turret-Plates fallen in {remaining}s — letzte Chance, "
            "Welle pushen, freie Plates ziehen (160g pro Plate)"
        ),
        severity="info",
        category="objective",
        confidence=0.85,
        risk="LOW",
        ttl_s=30.0,
        kind="plate_window",
        reasons=(
            f"Plates despawn bei 14:00 ({remaining}s)",
            "160g pro Plate × bis zu 30 Plates = ~4800g Tempo-Gold",
            "Nach 14:00: keine Plate-Boni mehr, naked Turrets — Siege-Phase",
        ),
    )


# ─── Lane-opponent MIA advice text ──────────────────────────────────────────
# Phase-aware per-lane action lines. "Push the wave" means a different play
# at 5 min (warding bushes + scouting drake) than at 14 min (Herald setup +
# tower plates). These strings are short and concrete on purpose.

_LANE_ADVICE_EARLY: dict[str, str] = {
    "TOP":     "Welle pushen, Top-Buschwerk wardēn",
    "MIDDLE":  "Welle pushen, Mid-River wardēn",
    "BOTTOM":  "Welle pushen, Drachen-Ward setzen",
}
_LANE_ADVICE_MID: dict[str, str] = {
    "TOP":     "Welle pushen, Plates + Herald-Spawn vorbereiten",
    "MIDDLE":  "Welle pushen, andere Lanes pingen, Mid-Roam vorbereiten",
    "BOTTOM":  "Welle pushen, Drache/Plates kontestieren",
}
LANE_PHASE_EARLY_END_S: float = 480.0   # 8:00 — early lane → mid lane


def _lane_mia_advice(active_position: str, game_time: float) -> str:
    table = _LANE_ADVICE_EARLY if game_time <= LANE_PHASE_EARLY_END_S else _LANE_ADVICE_MID
    return table.get(active_position, "Welle pushen + Vision setzen")


def rule_lane_opponent_mia(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """Surface "your direct lane opponent is missing" with phase-aware advice
    (Charter B2 — lane-side companion to ``rule_gank_risk``).

    Two tiers:
    * info — 30 s no CS while alive: heads-up, push the wave, scout
    * warn — 60 s no CS: they're committed elsewhere (gank, drake setup,
             roam, tower swap) — push hard + ping other lanes

    Skipped while opponent is dead (we already know exactly where they
    are: at base on a respawn timer). Skipped for JUNGLE / UTILITY
    active players — those positions don't have a single CS-tracked
    opponent.
    """
    alert = getattr(snapshot, "lane_opponent_alert", None)
    if alert is None:
        return None
    name = getattr(alert, "opponent_name", "Gegner")
    mia = int(getattr(alert, "seconds_mia", 0))
    severity = str(getattr(alert, "severity", "info"))
    pos = str(getattr(alert, "active_position", ""))
    game_time = float(getattr(snapshot, "game_time", 0.0) or 0.0)
    advice = _lane_mia_advice(pos, game_time)

    if severity == "warn":
        text = f"{name} {mia}s weg — gankt anderswo. {advice}"
        risk, ttl_s, confidence = "MEDIUM", 25.0, 0.78
    else:
        text = f"{name} weg ({mia}s) — {advice}"
        risk, ttl_s, confidence = "LOW", 18.0, 0.70

    return Recommendation(
        text=text,
        severity=severity,
        category="tempo",
        confidence=confidence,
        risk=risk,
        ttl_s=ttl_s,
        kind="lane_mia",
        reasons=(
            f"{name} hat seit {mia}s kein CS gemacht",
            f"Position: {pos}",
            "Welle pushen = ihr CS leakt + du tempogewinnst",
        ),
    )


def rule_gank_risk(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """Warn when the enemy jungler has been unaccounted-for long enough
    to be approaching a lane undetected (Charter B2).

    Uses the GankAlert computed by LcdaSource from ChampionKill event
    timestamps. 60s MIA → info; 90s MIA → warn. Only fires during
    laning phase (4–20 min) for lane roles (TOP, MID, BOT).
    """
    alert = getattr(snapshot, "gank_alert", None)
    if alert is None:
        return None
    jungler = getattr(alert, "jungler_name", "Jungler")
    mia = int(getattr(alert, "seconds_mia", 0))
    severity = str(getattr(alert, "severity", "info"))
    if severity == "warn":
        text = f"{jungler} seit {mia}s verschwunden — Gank möglich! Welle räumen oder zurückziehen."
        risk = "HIGH"
        ttl_s = 15.0
    else:
        text = f"{jungler} seit {mia}s nicht gesehen — Vorsicht in der Lane."
        risk = "MEDIUM"
        ttl_s = 12.0
    return Recommendation(
        text=text,
        severity=severity,
        category="safety",
        confidence=0.72,
        risk=risk,
        ttl_s=ttl_s,
        kind="gank_risk",
        reasons=(f"Jungler {jungler} nicht in Kill-Events seit {mia}s",),
    )
