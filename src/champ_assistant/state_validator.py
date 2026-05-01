"""State invariant validator (Strategy C4 — Most Reliable).

Pure functions that check state consistency, plus a thin observer that
subscribes to the state store and logs violations. Charter requirement:
"timers never go negative, game state never becomes invalid, services
never desynchronize".

Scope (V1)
==========
* Objective timers: ``next_spawn_seconds`` ≥ 0, ``last_killed_seconds``
  ≥ 0 and not in the future, ``remaining(game_time)`` ≥ 0.
* LCDA snapshot: ``game_time`` ≥ 0; ``items_value`` ≥ 0; ``level`` ≥ 0
  per live player.
* Detection only — never mutates state. The validator's job is to
  surface drift, not auto-correct it (that belongs to the recovery
  manager, charter step C5).

Out of scope for V1
-------------------
* Cross-service desync (no second source of truth to compare against).
* Champ-select session validation — pydantic model_validators already
  cover the on-the-wire shape.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING
from collections.abc import Callable

if TYPE_CHECKING:
    from .lcda.objectives import ObjectiveTimer
    from .lcda.source import LcdaSnapshot
    from .state_store import GameState, StateStore

logger = logging.getLogger(__name__)

# How far in the future a "killed at" timestamp can be relative to
# current game_time before we flag it. LCDA event ingest can race the
# game-time poll by a few hundred ms; 1.0s is generous enough to absorb
# the jitter without missing real desync.
KILL_FUTURE_TOLERANCE_S = 1.0


@dataclass(frozen=True)
class Issue:
    """One invariant violation. Pure data — no Qt, no I/O."""
    severity: str  # "warning" | "error"
    category: str  # "timer" | "snapshot" | "player"
    message: str


def validate_objective(
    obj: "ObjectiveTimer", game_time: float,
) -> list[Issue]:
    """Check one objective timer's internal consistency."""
    issues: list[Issue] = []
    name = getattr(obj, "name", "?")

    nxt = getattr(obj, "next_spawn_seconds", None)
    if nxt is not None and nxt < 0:
        issues.append(Issue(
            "error", "timer",
            f"{name}.next_spawn_seconds={nxt:.2f} (< 0)",
        ))

    last = getattr(obj, "last_killed_seconds", None)
    if last is not None:
        if last < 0:
            issues.append(Issue(
                "error", "timer",
                f"{name}.last_killed_seconds={last:.2f} (< 0)",
            ))
        elif last > game_time + KILL_FUTURE_TOLERANCE_S:
            issues.append(Issue(
                "error", "timer",
                f"{name} killed in the future "
                f"(last_killed={last:.2f}, game_time={game_time:.2f})",
            ))

    try:
        remaining = obj.remaining(game_time)
    except Exception as exc:  # noqa: BLE001 — validator must never crash
        issues.append(Issue(
            "error", "timer",
            f"{name}.remaining() raised: {exc!r}",
        ))
        return issues
    if remaining is not None and remaining < 0:
        issues.append(Issue(
            "error", "timer",
            f"{name}.remaining()={remaining:.2f} (< 0)",
        ))

    return issues


def validate_snapshot(snapshot: "LcdaSnapshot | None") -> list[Issue]:
    """Run every invariant against an LCDA snapshot."""
    if snapshot is None:
        return []
    issues: list[Issue] = []

    gt = getattr(snapshot, "game_time", 0.0)
    if not isinstance(gt, (int, float)) or gt < 0:
        issues.append(Issue(
            "error", "snapshot", f"game_time={gt!r} (must be ≥ 0)",
        ))
        # When game_time is bogus, downstream timer checks would
        # produce a flood of false positives. Bail out early.
        return issues

    for obj in getattr(snapshot, "objectives", []) or []:
        issues.extend(validate_objective(obj, float(gt)))

    for player in (
        list(getattr(snapshot, "allies", []) or [])
        + list(getattr(snapshot, "enemies", []) or [])
    ):
        issues.extend(_validate_player(player))

    return issues


def _validate_player(player: object) -> list[Issue]:
    issues: list[Issue] = []
    name = getattr(player, "summoner_name", "?") or "?"
    level = getattr(player, "level", 0)
    items_value = getattr(player, "items_value", 0)

    if isinstance(level, int) and level < 0:
        issues.append(Issue(
            "warning", "player",
            f"{name}.level={level} (< 0)",
        ))
    if isinstance(items_value, (int, float)) and items_value < 0:
        issues.append(Issue(
            "warning", "player",
            f"{name}.items_value={items_value} (< 0)",
        ))
    return issues


# --------------------------------------------------------------------------
# Live observer — wires into StateStore and logs violations
# --------------------------------------------------------------------------

IssueHandler = Callable[[list[Issue]], None]


class StateValidator:
    """Subscribes to a StateStore. On every snapshot change, runs the
    full validation pass; non-empty issue lists are forwarded to the
    handler (default: logger.warning per issue).

    Idempotent ``stop`` — safe to call from a teardown hook even when
    the subscription was never live.
    """

    def __init__(
        self,
        store: "StateStore",
        *,
        on_issues: IssueHandler | None = None,
    ) -> None:
        self._store = store
        self._on_issues = on_issues or self._default_log
        self._last_snapshot_id: int | None = None
        self._unsub: Callable[[], None] | None = store.subscribe(self._on_change)

    def _on_change(self, old: "GameState", new: "GameState") -> None:
        snap = getattr(new, "lcda_snapshot", None)
        if snap is None:
            self._last_snapshot_id = None
            return
        # Only re-validate when the snapshot reference actually changed
        # (StateStore notifies on every update, even no-op ones).
        snap_id = id(snap)
        if snap_id == self._last_snapshot_id:
            return
        self._last_snapshot_id = snap_id
        try:
            issues = validate_snapshot(snap)
        except Exception:  # noqa: BLE001
            logger.exception("state_validator_crashed")
            return
        if issues:
            try:
                self._on_issues(issues)
            except Exception:  # noqa: BLE001
                logger.exception("state_validator_handler_failed")

    @staticmethod
    def _default_log(issues: list[Issue]) -> None:
        for issue in issues:
            logger.warning(
                "state_invariant_violated category=%s severity=%s msg=%s",
                issue.category, issue.severity, issue.message,
            )

    def stop(self) -> None:
        if self._unsub is not None:
            try:
                self._unsub()
            except Exception:  # noqa: BLE001
                logger.exception("state_validator_unsubscribe_failed")
            finally:
                self._unsub = None
