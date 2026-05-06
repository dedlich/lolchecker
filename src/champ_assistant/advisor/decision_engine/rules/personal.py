"""Personal-coaching rules — recall, skill points, tilt, power spikes.

Five rules that fire on the active player's individual state rather
than team-wide signals: HP/mana/gold for recall timing, unspent
skill-point detection, death-pattern coaching, and the power-spike
panel signals (own and enemy).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ....lcda.source import LcdaSnapshot

from .._core import Recommendation
from .._state import _RECALL_HYSTERESIS


# ─── Recall-window thresholds (B5 — Recommendation Service) ──────────────────
# These match the way pros actually think about resource state, not raw HP/mana
# numbers. Tuned conservatively: false positives are worse than missed calls
# because the player will mute a noisy assistant within one game.

HP_CRITICAL_PCT: float = 0.30   # below this you die to a single combo
HP_LOW_PCT: float = 0.50        # below this, trades aren't safe
MANA_DEPLETED_PCT: float = 0.20  # below this, you can't trade or escape
MANA_LOW_PCT: float = 0.30      # below this, you're at most 1 ability away from dry

# Gold tiers — generic component thresholds the player can map to their build.
GOLD_BACK_WORTH: float = 1100.0       # Sheen / Tear / first boots
GOLD_COMPONENT_SPIKE: float = 1300.0  # Lost Chapter / Caulfield's tier
GOLD_LARGE_SPIKE: float = 1600.0      # Pickaxe / BF Sword tier

# Recall coaching is most valuable in lane + early mid-game. After 20:00,
# back timing is dictated by team rotations, not personal resources.
RECALL_PHASE_END_S: float = 1200.0    # 20:00

# Hysteresis re-arm thresholds — each tier re-arms only after the player
# crosses these (HP > 35 %, mana > 30 %, gold drops back below threshold).
HP_RECALL_REARM_PCT: float = 0.35
MANA_RECALL_REARM_PCT: float = 0.30
GOLD_RECALL_REARM_BUFFER: float = 200.0


# ─── Skill-point unspent thresholds ──────────────────────────────────────────
# Pros tap their level-up keybind in ~1 second. After 60 seconds of game
# time the wave has hit and any unspent point is a real miss. We gate on
# HP because nagging during a trade/teamfight is worse than missing the call.
SKILL_POINT_GAME_TIME_MIN_S: float = 60.0
SKILL_POINT_HP_GATE_PCT: float = 0.50


# ─── Tilt phase boundaries ───────────────────────────────────────────────────
# Coaching cutoffs, not hard mechanical phases — late-game advice
# (group 5, no splits) gets dangerous before 25:00 in solo-queue.
_TILT_LANE_PHASE_END_S: float = 840.0
_TILT_MID_GAME_END_S: float = 1500.0


def _tilt_phase_advice(game_time: float) -> str:
    """One-liner of *what to do during the next walk-back* given the
    current game phase. Returned advice is concrete, not motivational."""
    if game_time <= _TILT_LANE_PHASE_END_S:
        return "Welle unter Turm freezen, Jungler pingen, kein 1v1"
    if game_time <= _TILT_MID_GAME_END_S:
        return "Mit Team gruppieren, kein Side-Lane, Vision setzen"
    return "Death-Timer 50s+ — niemals alleine zeigen, nur 5er Plays"


def rule_recall_check(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """Recall-window coaching driven by HP %, mana %, and gold (Charter B5).

    Picks at most one of four signals, in priority order:

    1. **Critical HP** (alert) — HP < 30 %; surfaces "back NOW" regardless
       of gold or game phase. Any next interaction kills you.

    2. **Resource depleted + back-worth gold** (warn) — HP < 50 % OR mana
       < 25 %, AND gold ≥ 1100. The classic "you need a reset, and you
       have value to bank" signal. Pros recall here every time.

    3. **Pure gold opportunity** (info) — gold ≥ 1300 in lane phase, even
       at full HP. The next trip back is worth a real spike; don't sit
       on uncashed gold while losing tempo.

    4. **Mana check** (info) — mana < 20 % in lane phase. Tells the
       player they're now in their opponent's all-in window, and to
       freeze the wave / use Doran's regen until mana is back.

    Skipped while dead (hp_pct ≤ 0). Does **not** fire after 20:00 except
    for tier 1 (critical HP); recall timing past 20:00 is dictated by
    team rotation, not personal resources.
    """
    state = getattr(snapshot, "active_combat", None)
    if state is None:
        return None
    hp_pct = float(getattr(state, "hp_pct", 1.0))
    mana_pct = float(getattr(state, "mana_pct", 1.0))
    gold = float(getattr(state, "gold", 0.0))
    is_mana_user = bool(getattr(state, "is_mana_user", False))
    game_time = float(getattr(snapshot, "game_time", 0.0) or 0.0)
    h = _RECALL_HYSTERESIS

    if hp_pct <= 0.0:
        h.reset()
        return None

    if hp_pct >= HP_RECALL_REARM_PCT:
        h.critical = True
    if hp_pct >= HP_LOW_PCT and (not is_mana_user or mana_pct >= MANA_LOW_PCT):
        h.resource = True
    if gold < GOLD_COMPONENT_SPIKE - GOLD_RECALL_REARM_BUFFER:
        h.gold = True
    if not is_mana_user or mana_pct >= MANA_RECALL_REARM_PCT:
        h.mana = True

    if hp_pct < HP_CRITICAL_PCT and not h.critical:
        return None
    if hp_pct < HP_CRITICAL_PCT:
        h.critical = False
        pct = int(hp_pct * 100)
        return Recommendation(
            text=f"{pct}% HP — RECALL JETZT, nächster Trade tötet dich",
            severity="alert",
            category="safety",
            confidence=0.95,
            risk="HIGH",
            ttl_s=15.0,
            kind="recall_critical",
            reasons=(
                f"HP: {pct}%",
                f"Gold dabei: {int(gold)}g",
                "Jeder Skillshot / Auto = Tod",
            ),
        )

    resource_low = hp_pct < HP_LOW_PCT or (is_mana_user and mana_pct < MANA_LOW_PCT)
    if (
        resource_low and gold >= GOLD_BACK_WORTH
        and game_time <= RECALL_PHASE_END_S
        and h.resource
    ):
        h.resource = False
        triggers: list[str] = []
        if hp_pct < HP_LOW_PCT:
            triggers.append(f"HP {int(hp_pct*100)}%")
        if is_mana_user and mana_pct < MANA_LOW_PCT:
            triggers.append(f"Mana {int(mana_pct*100)}%")
        spike_tier = (
            "Large Item" if gold >= GOLD_LARGE_SPIKE
            else "Component Spike" if gold >= GOLD_COMPONENT_SPIKE
            else "Component"
        )
        return Recommendation(
            text=f"Recall lohnt — {' + '.join(triggers)}, {int(gold)}g für {spike_tier}",
            severity="warn",
            category="safety",
            confidence=0.85,
            risk="MEDIUM",
            ttl_s=20.0,
            kind="recall_resource",
            reasons=(
                *triggers,
                f"Gold: {int(gold)}g (≥{int(GOLD_BACK_WORTH)}g back-worth)",
                "Reset-Tempo > vor-pushen + halb-tot bleiben",
            ),
        )

    if (
        gold >= GOLD_COMPONENT_SPIKE
        and game_time <= RECALL_PHASE_END_S
        and h.gold
    ):
        h.gold = False
        return Recommendation(
            text=f"{int(gold)}g — Recall-Fenster, Component-Spike kaufen + sicher zurück",
            severity="info",
            category="tempo",
            confidence=0.70,
            risk="LOW",
            ttl_s=20.0,
            kind="recall_gold",
            reasons=(
                f"Gold: {int(gold)}g (≥{int(GOLD_COMPONENT_SPIKE)}g Component-Spike)",
                f"HP {int(hp_pct*100)}% — sicherer Reset möglich",
            ),
        )

    if (
        is_mana_user and mana_pct < MANA_DEPLETED_PCT
        and game_time <= RECALL_PHASE_END_S
        and h.mana
    ):
        h.mana = False
        return Recommendation(
            text=f"Mana {int(mana_pct*100)}% — Gegner-All-In-Fenster offen, Welle freezen + warten",
            severity="info",
            category="safety",
            confidence=0.65,
            risk="MEDIUM",
            ttl_s=15.0,
            kind="mana_check",
            reasons=(
                f"Mana: {int(mana_pct*100)}% (<{int(MANA_DEPLETED_PCT*100)}%)",
                "Kein Trade-Antwort verfügbar — defensive Position",
            ),
        )

    return None


def rule_unspent_skill_points(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """Surface "you have an unspent skill point" — the cheapest meaningful
    coaching call in the game (Charter B5 — micro-coaching).

    Detection: ``unspent_skill_points`` is recomputed every tick from
    ``activePlayer.abilities`` vs player level. Fire info-level when:
      * unspent ≥ 1
      * game_time ≥ 60 s (game-start grace — first wave hasn't crashed yet)
      * hp_pct ≥ 50 % (below this the player is in a trade; nagging
        about a skill-up icon while they're trying to survive is worse
        than missing the cue)
      * player is alive (hp_pct > 0)

    Solo-queue routinely forgets skill points mid-fight or right after a
    kill confirmation. Pros never miss this. Externalising the cue
    closes one of the most frequent skill-cap micro-mistakes.
    """
    state = getattr(snapshot, "active_combat", None)
    if state is None:
        return None
    unspent = int(getattr(state, "unspent_skill_points", 0))
    if unspent <= 0:
        return None
    hp_pct = float(getattr(state, "hp_pct", 1.0))
    if hp_pct <= 0.0:
        return None
    if hp_pct < SKILL_POINT_HP_GATE_PCT:
        return None
    game_time = float(getattr(snapshot, "game_time", 0.0) or 0.0)
    if game_time < SKILL_POINT_GAME_TIME_MIN_S:
        return None

    plural = "Punkte" if unspent > 1 else "Punkt"
    return Recommendation(
        text=f"{unspent} Skill-{plural} offen — Q / W / E / R upgraden",
        severity="info",
        category="lane",
        confidence=0.95,
        risk="LOW",
        ttl_s=10.0,
        kind="skill_point_unspent",
        reasons=(
            f"{unspent} ungenutzte Skill-Punkte",
            "Skill-Up = freier DMG / Sustain / Mobility — kein Grund zu warten",
        ),
    )


def rule_tilt_detection(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """Surface the active player's death pattern as a coaching call.

    Five tiers:
      * caution  — first lane death; freeze + ping
      * tilt     — 2 deaths in 90s; the classic tilt window
      * re_engage — 2 deaths in 60s; "1-and-done", do nothing for 30s
      * spiral   — 3 deaths in 180s OR 2 in 60s; hard reset

    Modifiers append to the rec text:
      * bounty_lost: "+ Bounty (3+ Streak) verloren — ~600g extra für Gegner"
      * solo_death:  "+ Alleine gestorben — keine Side-Lane mehr"

    The phase-aware advice line replaces the generic "play safe" because
    "play safe" means different things in lane vs mid-game vs late-game.
    Solo-queue users who *know* what playing safe means don't need
    coaching; the rest get concrete actions.
    """
    state = getattr(snapshot, "tilt_state", None)
    if state is None or getattr(state, "severity", "ok") == "ok":
        return None

    severity_tier = state.severity
    game_time = float(getattr(snapshot, "game_time", 0.0) or 0.0)
    phase_advice = _tilt_phase_advice(game_time)

    if severity_tier == "spiral":
        text = f"DEATH SPIRAL — {state.deaths_recent_180s} Tode in 3min. STOP. 60s NICHT zeigen — {phase_advice}"
        severity, ttl_s, confidence, risk = "alert", 90.0, 0.95, "HIGH"
    elif severity_tier == "re_engage":
        text = f"1-AND-DONE — 2 Tode in 60s. Direkt nach Respawn wieder rein = Disaster. 30s warten — {phase_advice}"
        severity, ttl_s, confidence, risk = "alert", 75.0, 0.90, "HIGH"
    elif severity_tier == "tilt":
        text = f"Tilt-Fenster — 2 Tode in 90s. Länger basen, 2 Components kaufen, {phase_advice}"
        severity, ttl_s, confidence, risk = "warn", 60.0, 0.85, "HIGH"
    else:
        text = f"Erster Tod — {phase_advice}, kein Comeback-1v1 versuchen"
        severity, ttl_s, confidence, risk = "info", 30.0, 0.65, "MEDIUM"

    modifiers: list[str] = []
    reasons: list[str] = [
        f"Tode total: {state.deaths_total}",
        f"Tode in 90s: {state.deaths_recent_90s}",
    ]
    if state.bounty_lost:
        modifiers.append("+ Bounty (3+ Streak) verloren — ~600g extra Gegner")
        reasons.append("Bounty: 3+ unanswered kills vor letztem Tod verloren")
    if state.solo_death:
        modifiers.append("+ Alleine gestorben — keine Side-Lane mehr")
        reasons.append("Letzter Tod ohne Ally-Beteiligung — Positionierungsfehler")
    if modifiers:
        text = text + "  " + "  ".join(modifiers)

    return Recommendation(
        text=text,
        severity=severity,
        category="safety",
        confidence=confidence,
        risk=risk,
        ttl_s=ttl_s,
        kind="tilt",
        reasons=tuple(reasons),
    )


def rule_power_spike(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """Alert when the active player just crossed a power-spike threshold
    (level 6/11/16 or first/second/third legendary item).

    The ``PowerSpikePanel`` in the main overlay hides during gameplay —
    this rule surfaces the same signal in the floating RecommendationPanel
    so the alert is visible while a game is running.

    TTL is short (20-30s) so the card expires before the window closes.
    """
    spikes = getattr(snapshot, "new_spikes", []) or []
    if not spikes:
        return None
    spike = max(spikes, key=lambda s: (1 if getattr(s, "kind", "") == "level" else 0, getattr(s, "value", 0)))
    kind = getattr(spike, "kind", "")
    value = getattr(spike, "value", 0)
    label = getattr(spike, "label", "Power Spike")
    detail = getattr(spike, "detail", "")

    text = f"{label} — {detail}" if detail else label

    if kind == "level" and value == 6:
        severity, ttl_s = "alert", 20.0
    elif kind == "level":
        severity, ttl_s = "warn", 25.0
    elif kind == "items" and value == 2:
        severity, ttl_s = "warn", 30.0
    else:
        severity, ttl_s = "info", 30.0

    return Recommendation(
        text=text,
        severity=severity,
        category="tempo",
        confidence=1.0,
        risk="LOW",
        ttl_s=ttl_s,
        kind="power_spike",
        reasons=(detail,) if detail else (),
    )


def rule_enemy_item_spike(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """Warn when an enemy champion just completed their 1st/2nd/3rd legendary
    item. The most dangerous spike (highest legendary count) surfaces first
    so the player knows which enemy is now scarier.

    2nd legendary is the critical threshold for carries — that's typically
    their mid-game power peak. 1st legendary fires as info only.
    """
    spikes = getattr(snapshot, "enemy_spikes", []) or []
    if not spikes:
        return None

    top = max(spikes, key=lambda s: (getattr(s, "legendary_count", 0), getattr(s, "champion_name", "")))
    champ = getattr(top, "champion_name", "Gegner")
    count = getattr(top, "legendary_count", 1)

    if count >= 2:
        severity, ttl_s = "warn", 30.0
        text = f"{champ} hat {count}. Item — Vorsicht, starker Spike!"
    else:
        severity, ttl_s = "info", 25.0
        text = f"{champ} hat 1. Item fertig"

    return Recommendation(
        text=text,
        severity=severity,
        category="safety",
        confidence=0.90,
        risk="MEDIUM" if count >= 2 else "LOW",
        ttl_s=ttl_s,
        kind="enemy_spike",
        reasons=(f"{champ}: {count}. Legendary Item abgeschlossen",),
    )
