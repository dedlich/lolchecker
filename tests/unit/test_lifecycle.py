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


# ----------------------------------------------------------------------
# Finalizers — failure-recovery layer (session_summary, clean_shutdown)
# ----------------------------------------------------------------------
def test_finalizers_run_after_all_services_in_registration_order() -> None:
    calls: list[str] = []
    lc = LifecycleManager()
    lc.register("svc-a", lambda: calls.append("stop:a"))
    lc.register("svc-b", lambda: calls.append("stop:b"))
    lc.register_finalizer("summary", lambda: calls.append("final:summary"))
    lc.register_finalizer("marker",  lambda: calls.append("final:marker"))
    lc.shutdown()
    # Services in REVERSE registration order, then finalizers in
    # FORWARD order. summary BEFORE marker so a dying summary doesn't
    # mask the absence of a clean exit.
    assert calls == [
        "stop:b", "stop:a",
        "final:summary", "final:marker",
    ]


def test_failing_finalizer_does_not_block_others() -> None:
    calls: list[str] = []

    def boom() -> None:
        raise RuntimeError("boom")

    lc = LifecycleManager()
    lc.register_finalizer("a", lambda: calls.append("a"))
    lc.register_finalizer("boom", boom)
    lc.register_finalizer("c", lambda: calls.append("c"))
    lc.shutdown()  # must not raise
    # 'c' still runs even though 'boom' raised before it.
    assert calls == ["a", "c"]


def test_finalizers_only_fire_once() -> None:
    """Idempotent shutdown protection covers finalizers too."""
    calls: list[str] = []
    lc = LifecycleManager()
    lc.register_finalizer("once", lambda: calls.append("hit"))
    lc.shutdown()
    lc.shutdown()
    assert calls == ["hit"]
