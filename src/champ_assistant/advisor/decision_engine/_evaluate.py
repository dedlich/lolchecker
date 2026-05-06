"""Top-level engine driver — severity sort + suppression + evaluate().

Imported through the package's ``__init__`` as the public ``evaluate``
function plus the ``_suppress_dominated`` helper that tests exercise
directly.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ...lcda.source import LcdaSnapshot
    from ...lcda.spell_tracker import SpellTracker

from ._core import Recommendation
from ._rules import (
    ALL_RULES,
    rule_enemy_combat_spell_down,
    rule_enemy_flash_down,
    rule_enemy_tp_down,
    rule_situational_build,
)


_SEVERITY_RANK = {"alert": 0, "warn": 1, "info": 2}


def _suppress_dominated(recs: list[Recommendation]) -> list[Recommendation]:
    """Remove recommendations made redundant by more specific ones.

    Thin shim around the declarative ``SUPPRESSION_TABLE`` in
    ``_suppression``. The table + runner replaced 200 lines of
    imperative ``if "x" in kinds:`` blocks with ~12 entries each
    documenting WHY the suppression exists; see ``_suppression.py``
    for the full table and the two structural special cases (game_end
    whitelist + cross-objective priority) that don't fit the
    (trigger, suppresses) tuple.
    """
    from ._suppression import apply_suppression
    return apply_suppression(recs)


def evaluate(
    snapshot: "LcdaSnapshot | None",
    *,
    rules: tuple = ALL_RULES,
    spell_tracker: "SpellTracker | None" = None,
    situational_build: object = None,
) -> list[Recommendation]:
    """Run every rule against ``snapshot`` and return the non-None
    results sorted by severity (alerts first). Pure function — safe
    to call on the LCDA-snapshot tick without any state.

    None snapshot → empty list (pre-game window). Rules that raise
    are silently skipped — a buggy rule must not break the engine.

    ``spell_tracker``: when provided, enables context-aware rules that
    require user-tracked summoner spell cooldowns (e.g. flash_down).

    ``situational_build``: a ``BuildResult`` from the build engine.
    When provided, fires ``rule_situational_build`` with item recs
    adjusted for the live enemy team composition.
    """
    if snapshot is None:
        return []
    # Per-rule timing: ~30-50 ns per perf_counter call. With 53 rules per
    # tick at 0.5 Hz that's <2 µs of measurement overhead per evaluation —
    # well below the noise floor of the rules themselves. Keeping always-on
    # so live testing has data without a separate mode flag.
    from time import perf_counter
    from ...performance_monitor import rule_timing_recorder
    timer = rule_timing_recorder()
    out: list[Recommendation] = []
    for rule in rules:
        rule_name = getattr(rule, "__name__", "rule_unknown")
        t0 = perf_counter()
        try:
            rec = rule(snapshot)
        except Exception:  # noqa: BLE001 — engine never propagates rule bugs
            timer.record(rule_name, (perf_counter() - t0) * 1000.0)
            continue
        timer.record(
            rule_name, (perf_counter() - t0) * 1000.0, fired=rec is not None,
        )
        if rec is not None:
            out.append(rec)
    if spell_tracker is not None:
        for _spell_rule in (
            rule_enemy_flash_down,
            rule_enemy_tp_down,
            rule_enemy_combat_spell_down,
        ):
            rule_name = getattr(_spell_rule, "__name__", "spell_rule")
            t0 = perf_counter()
            try:
                rec = _spell_rule(snapshot, spell_tracker)
                timer.record(
                    rule_name, (perf_counter() - t0) * 1000.0,
                    fired=rec is not None,
                )
                if rec is not None:
                    out.append(rec)
            except Exception:  # noqa: BLE001
                timer.record(rule_name, (perf_counter() - t0) * 1000.0)
    if situational_build is not None:
        t0 = perf_counter()
        try:
            rec = rule_situational_build(snapshot, situational_build)
            timer.record(
                "rule_situational_build", (perf_counter() - t0) * 1000.0,
                fired=rec is not None,
            )
            if rec is not None:
                out.append(rec)
        except Exception:  # noqa: BLE001
            timer.record("rule_situational_build", (perf_counter() - t0) * 1000.0)
    out.sort(key=lambda r: _SEVERITY_RANK.get(r.severity, 99))
    return _suppress_dominated(out)
