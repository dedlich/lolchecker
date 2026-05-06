"""Summoner-spell-cooldown rules (Charter B2).

These rules differ from every other rule in the engine: they take a
second positional argument — a ``SpellTracker`` — because user-tracked
spell casts are the only way to know what's actually on cooldown
(LCDA does not expose enemy summoner-spell timers). They are NOT in
``ALL_RULES``; ``_evaluate.evaluate`` calls them in a separate loop
when ``spell_tracker is not None``.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ....lcda.source import LcdaSnapshot
    from ....lcda.spell_tracker import SpellTracker

from .._core import (
    COMBAT_SPELL_ALERT_S,
    FLASH_DOWN_ALERT_S,
    Recommendation,
    TP_DOWN_ALERT_S,
    _COMBAT_SPELLS,
)


def rule_enemy_flash_down(
    snapshot: "LcdaSnapshot",
    spell_tracker: "SpellTracker",
) -> Recommendation | None:
    """Alert when one or more enemies have Flash on cooldown (B2 — engage window).

    Requires a SpellTracker with user-tracked spell casts. Fires when at least
    one tracked enemy flash has more than FLASH_DOWN_ALERT_S remaining so the
    alert is still actionable. Suppressed by _suppress_dominated when the team
    is behind (numbers_disadv) — flash-down is an opportunity only when safe.
    """
    enemies = list(getattr(snapshot, "enemies", []) or [])
    game_time = float(getattr(snapshot, "game_time", 0.0) or 0.0)
    if not enemies or not game_time:
        return None

    flashes_down: list[tuple[str, float]] = []
    for enemy in enemies:
        for spell in (getattr(enemy, "spell_one", None), getattr(enemy, "spell_two", None)):
            if spell is None or getattr(spell, "name", "") != "Flash":
                continue
            name = getattr(enemy, "summoner_name", "") or getattr(enemy, "champion_name", "")
            remaining = spell_tracker.remaining(
                getattr(enemy, "summoner_name", ""), "Flash", game_time
            )
            if remaining > FLASH_DOWN_ALERT_S:
                flashes_down.append((name, remaining))
            break  # each enemy has at most one Flash

    if not flashes_down:
        return None

    count = len(flashes_down)
    names = ", ".join(n for n, _ in flashes_down[:3])
    min_remaining = min(r for _, r in flashes_down)

    if count == 1:
        name, remaining = flashes_down[0]
        text = f"Flash down: {name} ({int(remaining)}s)"
    else:
        text = f"{count}× Flash down — Engage-Fenster!"

    return Recommendation(
        text=text,
        severity="warn",
        category="tempo",
        confidence=0.85,
        risk="MEDIUM",
        ttl_s=min_remaining,
        kind="flash_down",
        reasons=(
            f"{names} ohne Flash",
            f"Flash bereit in ~{int(min_remaining)}s",
            "Gutes Fenster zum Engagen oder Diven",
        ),
    )


def rule_enemy_tp_down(
    snapshot: "LcdaSnapshot",
    spell_tracker: "SpellTracker",
) -> Recommendation | None:
    """Advisory when one or more enemies have Teleport on cooldown (B2).

    TP down blocks global rotations — the enemy can't react to your
    side-lane pressure or TP to save a collapsing teamfight. Fires when
    remaining CD > TP_DOWN_ALERT_S (90s) so the info is still actionable.
    Suppressed by numbers_disadv — don't split when short-handed.

    Severity scales with count:
    - 1 TP down → info (single-person advisory)
    - 2+ TP down → warn (major tempo window)
    """
    enemies = list(getattr(snapshot, "enemies", []) or [])
    game_time = float(getattr(snapshot, "game_time", 0.0) or 0.0)
    if not enemies or not game_time:
        return None

    tps_down: list[tuple[str, float]] = []
    for enemy in enemies:
        for spell in (getattr(enemy, "spell_one", None), getattr(enemy, "spell_two", None)):
            if spell is None or getattr(spell, "name", "") != "Teleport":
                continue
            name = (
                getattr(enemy, "summoner_name", "")
                or getattr(enemy, "champion_name", "")
            )
            remaining = spell_tracker.remaining(
                getattr(enemy, "summoner_name", ""), "Teleport", game_time,
            )
            if remaining > TP_DOWN_ALERT_S:
                tps_down.append((name, remaining))
            break

    if not tps_down:
        return None

    count = len(tps_down)
    names = ", ".join(n for n, _ in tps_down[:3])
    min_remaining = min(r for _, r in tps_down)
    severity = "warn" if count >= 2 else "info"

    if count == 1:
        name, remaining = tps_down[0]
        text = f"TP down: {name} ({int(remaining)}s) — kein Flank-TP!"
    else:
        text = f"{count}× TP down ({int(min_remaining)}s) — keine globale Rotation!"

    return Recommendation(
        text=text,
        severity=severity,
        category="tempo",
        confidence=0.88,
        risk="LOW",
        ttl_s=min_remaining,
        kind="tp_down",
        reasons=(
            f"{names} ohne Teleport",
            f"TP bereit in ~{int(min_remaining)}s",
            "Kein TP = kein globaler Eingriff — Side-Lanes frei!",
        ),
    )


def rule_enemy_combat_spell_down(
    snapshot: "LcdaSnapshot",
    spell_tracker: "SpellTracker",
) -> Recommendation | None:
    """Advisory when enemy has a tracked combat summoner spell on CD (B2).

    Covers Exhaust, Heal, Ignite, Barrier, and Cleanse — spells that
    directly affect trade outcomes. Each has a specific tactical message:
    - Exhaust down → enemy can't kite/reduce your carry
    - Heal down → no sustain/movement speed boost for ADC
    - Ignite down → no kill threat; trades are safer
    - Barrier/Cleanse down → burst/CC window

    Fires when remaining CD > COMBAT_SPELL_ALERT_S (60s). Groups multiple
    down-spells into one card to avoid flooding the overlay. Severity is
    always "info" — these are advisory notes, not urgent signals.
    """
    enemies = list(getattr(snapshot, "enemies", []) or [])
    game_time = float(getattr(snapshot, "game_time", 0.0) or 0.0)
    if not enemies or not game_time:
        return None

    _SPELL_HINTS: dict[str, str] = {
        "Exhaust":  "kein Exhaust — Carry kann all-in gehen",
        "Heal":     "kein Heal — ADC hat keine Sustain",
        "Ignite":   "kein Ignite — kein Kill-Threat in Lane",
        "Barrier":  "kein Barrier — Burst-Fenster!",
        "Cleanse":  "kein Cleanse — CC trifft sicher",
    }

    down: list[tuple[str, str, float]] = []  # (name, spell, remaining)
    for enemy in enemies:
        for spell in (getattr(enemy, "spell_one", None), getattr(enemy, "spell_two", None)):
            spell_name = getattr(spell, "name", "") if spell is not None else ""
            if spell_name not in _COMBAT_SPELLS:
                continue
            summoner = getattr(enemy, "summoner_name", "") or getattr(enemy, "champion_name", "")
            remaining = spell_tracker.remaining(
                getattr(enemy, "summoner_name", ""), spell_name, game_time,
            )
            if remaining > COMBAT_SPELL_ALERT_S:
                down.append((summoner, spell_name, remaining))
            break

    if not down:
        return None

    min_remaining = min(r for _, _, r in down)

    if len(down) == 1:
        name, spell_name, remaining = down[0]
        hint = _SPELL_HINTS.get(spell_name, f"kein {spell_name}")
        text = f"{spell_name} down: {name} ({int(remaining)}s) — {hint}"
    else:
        summary = ", ".join(f"{s}({n})" for n, s, _ in down[:3])
        text = f"Spells down: {summary}"

    return Recommendation(
        text=text,
        severity="info",
        category="tempo",
        confidence=0.83,
        risk="LOW",
        ttl_s=min_remaining,
        kind="combat_spell_down",
        reasons=tuple(
            f"{s} down: {n} — {_SPELL_HINTS.get(s, s)} ({int(r)}s)"
            for n, s, r in down
        ),
    )
