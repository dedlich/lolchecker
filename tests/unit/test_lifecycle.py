"""Tests for the LifecycleManager (ordered startup/shutdown)."""
from __future__ import annotations

import logging

from champ_assistant.lifecycle import LifecycleManager


def test_shutdown_calls_stops_in_reverse_order() -> None:
    calls: list[str] = []
    lc = LifecycleManager()
    lc.register("first",  lambda: calls.append("first"))
    lc.register("second", lambda: calls.append("second"))
    lc.register("third",  lambda: calls.append("third"))
    lc.shutdown()
    assert calls == ["third", "second", "first"]


def test_shutdown_is_idempotent() -> None:
    calls: list[str] = []
    lc = LifecycleManager()
    lc.register("svc", lambda: calls.append("svc"))
    lc.shutdown()
    lc.shutdown()  # second call must be a no-op
    assert calls == ["svc"]


def test_failing_stop_does_not_block_others() -> None:
    calls: list[str] = []

    def boom() -> None:
        raise RuntimeError("boom")

    lc = LifecycleManager()
    lc.register("a", lambda: calls.append("a"))
    lc.register("boom", boom)
    lc.register("b", lambda: calls.append("b"))
    lc.shutdown()  # must not raise
    # 'b' (registered last) runs first, then 'boom' raises, then 'a' still runs.
    assert calls == ["b", "a"]


def test_is_shutting_down_flag_flips_on_shutdown() -> None:
    lc = LifecycleManager()
    assert lc.is_shutting_down is False
    lc.shutdown()
    assert lc.is_shutting_down is True


def test_register_after_shutdown_is_ignored(caplog) -> None:  # type: ignore[no-untyped-def]
    calls: list[str] = []
    lc = LifecycleManager()
    lc.shutdown()
    with caplog.at_level(logging.WARNING, logger="champ_assistant.lifecycle"):
        lc.register("late", lambda: calls.append("late"))
    assert calls == []
    assert any("ignored" in r.getMessage() for r in caplog.records)
