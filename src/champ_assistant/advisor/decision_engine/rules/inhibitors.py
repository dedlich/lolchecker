"""Inhibitor / base-exposure rules (Charter B4).

Rules that fire on inhibitor or inhibitor-turret state changes — both
sides. Suppression matrix entries mostly center on these (Rules 6/7/8
in ``_suppression``):

  * ``base_exposed`` is more specific than generic ``lane_open``
  * ``inhib_down`` supersedes ``base_exposed`` (state advanced past it)
  * ``ally_inhib_down`` (defending base) trumps mid-map calls
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ....lcda.source import LcdaSnapshot

from .._core import (
    ALLY_INHIB_RESPAWN_ALERT_S,
    INHIB_EXPIRY_ALERT_S,
    Recommendation,
    _active_ally_inhibitors_down,
    _active_enemy_inhibitors_down,
    _earliest_ally_inhib_respawn_remaining,
    _earliest_enemy_inhib_respawn_remaining,
    _enemy_turrets_down,
    _team_gold_diff,
)


def rule_enemy_base_exposed(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """Enemy inhibitor turret(s) down → base exposed, push for GG.

    Fires when at least one lane has all three outer/inner/inhib turrets
    fallen, meaning our minions are now pushing into the base and an inhib
    kill creates super-minions. Higher-priority than lane_open.
    """
    active_team = (getattr(snapshot, "active_team", "") or "")
    if not active_team:
        return None
    full_counts = _enemy_turrets_down(snapshot, tiers=("P1", "P2", "P3"))
    exposed = [lane for lane, n in full_counts.items() if n >= 3]
    if not exposed:
        return None
    lanes_str = " + ".join(sorted(exposed))
    gold = _team_gold_diff(snapshot)
    return Recommendation(
        text=f"{lanes_str}-Inhib offen — Basis-Angriff, GG forcen!",
        severity="alert",
        category="lane",
        confidence=0.88,
        risk="LOW",
        ttl_s=90.0,
        kind="base_exposed",
        reasons=(
            f"Enemy {lanes_str}: Outer + Inner + Inhib-Turm gefallen",
            "Inhib-Kill = Super-Minions dauerhafter Pressure",
            f"Gold-Diff: {gold:+d}",
        ),
    )


def rule_ally_inhib_respawning(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """Ally inhibitor respawns soon — transition from defense to objectives (B4).

    Fires in the final ALLY_INHIB_RESPAWN_ALERT_S (60s) before the soonest
    ally inhib respawn, signaling that the defensive pressure window is closing
    and the team can plan a Baron/Dragon call.
    """
    remaining = _earliest_ally_inhib_respawn_remaining(snapshot)
    if remaining is None or remaining > ALLY_INHIB_RESPAWN_ALERT_S:
        return None
    return Recommendation(
        text=f"Ally Inhib respawnt in {int(remaining)}s — dann Objectives möglich!",
        severity="info",
        category="tempo",
        confidence=0.88,
        risk="LOW",
        ttl_s=remaining,
        kind="ally_inhib_respawning",
        reasons=(
            f"Eigener Inhibitor respawnt in {int(remaining)}s",
            "Super-Minions stoppen → Baron/Dragon-Fenster öffnet sich",
            "Ults + Wellen bereit halten",
        ),
    )


def rule_ally_inhib_down(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """Enemy destroyed one or more of OUR inhibitors — defensive alert (B4).

    Super-minions now spawn for the enemy in our lanes. Risk: enemy can
    siege our base towers without any effort. Correct play is wave-clear
    priority over mid-map objectives until the inhibitor respawns.
    Suppressed when numbers_disadv is also active (already showing safety rec).
    """
    count = _active_ally_inhibitors_down(snapshot)
    if count <= 0:
        return None
    label = f"{count}x" if count > 1 else "Dein"
    return Recommendation(
        text=f"{label} Inhib DOWN — Wellen clearen! Basis verteidigen!",
        severity="alert" if count >= 2 else "warn",
        category="safety",
        confidence=0.90,
        risk="HIGH",
        ttl_s=90.0,
        kind="ally_inhib_down",
        reasons=(
            f"{count} eigener Inhibitor zerstört",
            "Feind-Super-Minions spawnen in deiner Lane",
            "Wellen clearen → Nexus-Türme schützen",
        ),
    )


def rule_enemy_inhibitor_down(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """One or more enemy inhibitor buildings are dead → Super-Minions active.

    Unlike base_exposed (inhibitor turret fallen), this fires only after the
    actual inhibitor structure is destroyed, signalling super-minion pressure
    is already live.
    """
    count = _active_enemy_inhibitors_down(snapshot)
    if count <= 0:
        return None
    return Recommendation(
        text=f"{count}x Feind-Inhib DOWN — Super-Minions spawnen! Nexus-Angriff!",
        severity="alert" if count >= 2 else "warn",
        category="lane",
        confidence=0.90,
        risk="LOW",
        ttl_s=90.0,
        kind="inhib_down",
        reasons=(
            f"{count} enemy inhibitor(s) zerstört",
            "Super-Minions = dauerhafter Minion-Pressure",
            "Pushe mit Welle für Nexus-Türme",
        ),
    )


def rule_enemy_inhib_expiring(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """Enemy inhibitor is about to respawn — push NOW before it comes back (B4).

    Inhibitors respawn 300s after being killed. Fires in the final
    INHIB_EXPIRY_ALERT_S (60s) as a last-chance push reminder. Once the
    inhib is back, the super-minion pressure ends and the siege window
    closes.

    Suppressed by numbers_disadv and ally_inhib_down — when defending
    our own base or short-handed, attacking theirs is wrong priority.
    """
    remaining = _earliest_enemy_inhib_respawn_remaining(snapshot)
    if remaining is None or remaining > INHIB_EXPIRY_ALERT_S:
        return None
    severity = "alert" if remaining <= 30 else "warn"
    return Recommendation(
        text=f"Feind-Inhib respawnt in {int(remaining)}s — JETZT Nexus-Türme!",
        severity=severity,
        category="lane",
        confidence=0.90,
        risk="LOW",
        ttl_s=remaining,
        kind="inhib_expiring",
        reasons=(
            f"Enemy Inhibitor respawnt in {int(remaining)}s",
            "Super-Minion-Pressure endet — Fenster schließt sich",
            "Nexus-Türme jetzt angreifen oder Vorteil verlieren",
        ),
    )
