"""Tests for the centralized StateStore."""
from __future__ import annotations

from champ_assistant.state_store import GameState, StateStore


def test_initial_state_has_safe_defaults() -> None:
    store = StateStore()
    state = store.get()
    assert state.phase == "idle"
    assert state.connection_state == "disconnected"
    assert state.game_time == 0.0
    assert state.session_view is None
    assert state.lcda_snapshot is None
    assert state.revision == 0


def test_update_replaces_specified_fields_and_bumps_revision() -> None:
    store = StateStore()
    new = store.update(phase="champ_select", game_time=42.0)
    assert new.phase == "champ_select"
    assert new.game_time == 42.0
    assert new.revision == 1
    # untouched fields stay default
    assert new.connection_state == "disconnected"


def test_no_op_update_does_not_bump_revision_or_notify() -> None:
    store = StateStore()
    store.update(game_time=100.0)
    received: list[tuple[GameState, GameState]] = []
    store.subscribe(lambda old, new: received.append((old, new)))

    # Same value — should be skipped.
    store.update(game_time=100.0)
    assert received == []
    assert store.get().revision == 1  # unchanged


def test_subscribe_and_unsubscribe() -> None:
    store = StateStore()
    received: list[GameState] = []
    unsub = store.subscribe(lambda _o, n: received.append(n))

    store.update(game_time=10.0)
    assert len(received) == 1

    unsub()
    store.update(game_time=20.0)
    assert len(received) == 1  # unchanged


def test_listener_failures_do_not_block_other_listeners() -> None:
    store = StateStore()
    fired = []

    def bad(_o, _n):
        raise RuntimeError("boom")

    def good(_o, _n):
        fired.append(True)

    store.subscribe(bad)
    store.subscribe(good)
    store.update(phase="in_game")
    assert fired == [True]


def test_state_is_immutable_per_dataclass_frozen() -> None:
    state = GameState()
    try:
        state.phase = "in_game"  # type: ignore[misc]
    except Exception:  # noqa: BLE001
        return  # frozen as expected
    raise AssertionError("GameState should be frozen")


def test_metric_hook_fires_on_real_update_only() -> None:
    store = StateStore()
    metrics: list[float] = []
    store._on_update_metric = metrics.append

    store.update(game_time=1.0)
    store.update(game_time=1.0)  # no-op
    store.update(game_time=2.0)
    # No-op didn't record a metric; two real updates did.
    assert len(metrics) == 2
    assert all(m >= 0 for m in metrics)
