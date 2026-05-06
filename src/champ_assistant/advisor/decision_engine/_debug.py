"""Engine debug introspection — full state dump for one snapshot.

When live testing produces an unexpected rec set ("why didn't
rule_X fire?" / "why did this kind get suppressed?"), this is the
diagnostic surface. ``dump_engine_state(snapshot)`` runs every rule,
captures intermediate state, and returns a structured dict that can
be pretty-printed, attached to a bug report, or asserted on in tests.

Distinct from ``evaluate(snapshot)``:
  evaluate           returns the final post-suppression rec list only
  dump_engine_state  returns rec list + per-rule fire flags + per-rule
                     timing + pre-vs-post-suppression diff + hysteresis
                     state at this moment

The dump is *non-mutating* — it doesn't advance hysteresis state, doesn't
write to the timing recorder. Calling it during normal operation is
side-effect-free, so it can run inside a UI debug overlay.
"""
from __future__ import annotations

from time import perf_counter
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ...lcda.source import LcdaSnapshot
    from ...lcda.spell_tracker import SpellTracker

from ._core import Recommendation
from ._evaluate import _SEVERITY_RANK, _suppress_dominated
from ._rules import (
    ALL_RULES,
    rule_enemy_combat_spell_down,
    rule_enemy_flash_down,
    rule_enemy_tp_down,
    rule_situational_build,
)
from . import _state as _state_module


# Snapshot fields surfaced in the dump. Captured by name so a future
# LcdaSnapshot field addition doesn't crash the dump — getattr handles
# missing attrs by returning None.
_SNAPSHOT_FIELDS: tuple[str, ...] = (
    "game_time",
    "game_mode",
    "active_team",
    "active_summoner",
    "active_level",
    "active_items",
    "game_result",
)


def _snapshot_summary(snapshot: object) -> dict[str, Any]:
    """Capture key snapshot fields for debug context. Compact dict —
    only the high-signal fields. Full snapshot is available in caller
    scope if richer inspection is needed."""
    summary: dict[str, Any] = {}
    for field in _SNAPSHOT_FIELDS:
        summary[field] = getattr(snapshot, field, None)
    # Counts of the list-shaped fields (more useful than the lists
    # themselves for a debug snapshot).
    for list_field in ("enemies", "allies", "objectives", "raw_events",
                       "new_spikes", "enemy_spikes"):
        items = getattr(snapshot, list_field, None)
        summary[f"{list_field}_count"] = len(items) if items else 0
    # Boolean presence of optional alerts.
    for opt_field in ("gank_alert", "tilt_state", "lane_opponent_alert",
                      "active_combat", "ally_aggregate", "enemy_aggregate"):
        summary[f"{opt_field}_present"] = getattr(snapshot, opt_field, None) is not None
    return summary


def _capture_hysteresis_state() -> dict[str, Any]:
    """Snapshot every hysteresis singleton's current state.

    Each ``_FooHysteresis`` class uses ``__slots__``; we read each slot
    by name and emit a plain dict. Defensive — unknown attributes are
    skipped so a future class addition doesn't crash this helper.
    """
    state: dict[str, Any] = {}
    # Identify singletons by attribute-name convention: module-level
    # constants matching ``_*_HYSTERESIS``.
    for name in dir(_state_module):
        if not name.startswith("_") or not name.endswith("_HYSTERESIS"):
            continue
        singleton = getattr(_state_module, name, None)
        if singleton is None:
            continue
        # __slots__ may not exist if the class was changed; fall back to __dict__.
        slots = getattr(type(singleton), "__slots__", None)
        if slots:
            state[name] = {
                slot: _safe_repr(getattr(singleton, slot, None))
                for slot in slots
            }
        else:
            state[name] = {
                k: _safe_repr(v)
                for k, v in vars(singleton).items()
            }
    return state


def _safe_repr(value: Any) -> Any:
    """Coerce hysteresis values to JSON-friendly primitives.

    Most hysteresis state is bool / int / float / str / dict / set —
    all dump fine. Sets get converted to sorted lists for stable output.
    """
    if isinstance(value, set):
        return sorted(value, key=str)
    if isinstance(value, dict):
        return {k: _safe_repr(v) for k, v in value.items()}
    if isinstance(value, (bool, int, float, str)) or value is None:
        return value
    # Catch-all — repr is always safe for a dict value.
    return repr(value)


def _rec_to_dict(rec: Recommendation | None) -> dict[str, Any] | None:
    """Convert a Recommendation to a JSON-friendly dict, or None."""
    if rec is None:
        return None
    return {
        "kind": rec.kind,
        "severity": rec.severity,
        "category": rec.category,
        "text": rec.text,
        "confidence": rec.confidence,
        "risk": rec.risk,
        "ttl_s": rec.ttl_s,
        "reasons": list(rec.reasons),
    }


def dump_engine_state(
    snapshot: "LcdaSnapshot | None",
    *,
    spell_tracker: "SpellTracker | None" = None,
    situational_build: object = None,
) -> dict[str, Any]:
    """Run every rule against ``snapshot`` and return a structured
    diagnostic dict. Side-effect free: hysteresis isn't advanced, the
    rule-timing recorder isn't appended to.

    Returned shape::

        {
            "snapshot_summary": {game_time, enemies_count, ..., gank_alert_present, ...},
            "rule_results": [
                {"rule": "rule_X", "fired": True, "duration_ms": 0.012,
                 "rec": {"kind": "X", "severity": "warn", ...}},
                {"rule": "rule_Y", "fired": False, "duration_ms": 0.008, "rec": None},
                {"rule": "rule_Z", "fired": False, "duration_ms": 0.005,
                 "exception": "ValueError: ..."},
                ...
            ],
            "pre_suppression_kinds": ["X", "B", "C"],
            "post_suppression_kinds": ["X", "C"],
            "suppressed_kinds": ["B"],
            "post_suppression_recs": [{"kind": "X", ...}, ...],
            "hysteresis_state": {
                "_RECALL_HYSTERESIS": {"critical": True, "resource": False, ...},
                ...
            },
        }

    For ``snapshot=None`` the rule results / kinds are empty but
    ``hysteresis_state`` is still captured.
    """
    hysteresis_state = _capture_hysteresis_state()
    if snapshot is None:
        return {
            "snapshot_summary": None,
            "rule_results": [],
            "pre_suppression_kinds": [],
            "post_suppression_kinds": [],
            "suppressed_kinds": [],
            "post_suppression_recs": [],
            "hysteresis_state": hysteresis_state,
        }

    rule_results: list[dict[str, Any]] = []
    out: list[Recommendation] = []

    def _eval_one(rule_name: str, fn) -> None:  # type: ignore[no-untyped-def]
        t0 = perf_counter()
        try:
            rec = fn()
        except Exception as exc:  # noqa: BLE001 — engine never propagates rule bugs
            duration_ms = (perf_counter() - t0) * 1000.0
            rule_results.append({
                "rule": rule_name,
                "fired": False,
                "duration_ms": duration_ms,
                "rec": None,
                "exception": f"{type(exc).__name__}: {exc}",
            })
            return
        duration_ms = (perf_counter() - t0) * 1000.0
        rule_results.append({
            "rule": rule_name,
            "fired": rec is not None,
            "duration_ms": duration_ms,
            "rec": _rec_to_dict(rec),
        })
        if rec is not None:
            out.append(rec)

    for rule in ALL_RULES:
        _eval_one(rule.__name__, lambda r=rule: r(snapshot))

    if spell_tracker is not None:
        for spell_rule in (rule_enemy_flash_down, rule_enemy_tp_down,
                           rule_enemy_combat_spell_down):
            _eval_one(
                spell_rule.__name__,
                lambda r=spell_rule: r(snapshot, spell_tracker),
            )

    if situational_build is not None:
        _eval_one(
            "rule_situational_build",
            lambda: rule_situational_build(snapshot, situational_build),
        )

    pre_suppression_kinds = [r.kind for r in out]
    out.sort(key=lambda r: _SEVERITY_RANK.get(r.severity, 99))
    final = _suppress_dominated(out)
    post_suppression_kinds = [r.kind for r in final]

    pre_set = set(pre_suppression_kinds)
    post_set = set(post_suppression_kinds)
    suppressed_kinds = sorted(pre_set - post_set)

    return {
        "snapshot_summary": _snapshot_summary(snapshot),
        "rule_results": rule_results,
        "pre_suppression_kinds": pre_suppression_kinds,
        "post_suppression_kinds": post_suppression_kinds,
        "suppressed_kinds": suppressed_kinds,
        "post_suppression_recs": [_rec_to_dict(r) for r in final],
        "hysteresis_state": hysteresis_state,
    }
