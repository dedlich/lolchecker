"""Tests for spell-click click hardening — focus guard, dedup, telemetry.

Driven via SummonerTrackerPanel's _on_spell_clicked /
_on_spell_right_clicked handlers (the public surface that integrates
with the spec's "Click handler integration into scoreboard UI").
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from champ_assistant.lcda.players import LivePlayer, LiveSummonerSpell
from champ_assistant.lcda.spell_tracker import SpellTracker
from champ_assistant.ui.summoner_tracker import SummonerTrackerPanel


@pytest.fixture
def qt_app():
    return QApplication.instance() or QApplication([])


def _enemy_with_spells(name: str = "EnemyMid") -> LivePlayer:
    """Build a synthetic LivePlayer with two recognizable spells."""
    return LivePlayer(
        summoner_name=name,
        champion_name="Ahri",
        team="ORDER",  # arbitrary
        spell_one=LiveSummonerSpell(name="Flash", cooldown=300.0),
        spell_two=LiveSummonerSpell(name="Ignite", cooldown=180.0),
    )


def _make_panel(qt_app, app_active: bool = True):  # type: ignore[no-untyped-def]
    """Construct a panel with a fresh SpellTracker and a stub for
    application focus state."""
    tracker = SpellTracker()
    panel = SummonerTrackerPanel(tracker=tracker)
    panel._latest_enemies = [_enemy_with_spells()]
    panel._latest_game_time = 600.0  # 10 min in
    # Patch the focus-guard helper for deterministic tests.
    if app_active:
        panel._app_is_active = lambda: True   # type: ignore[method-assign]
    else:
        panel._app_is_active = lambda: False  # type: ignore[method-assign]
    return panel, tracker


# ----------------------------------------------------------------------
# Click → tracker.mark_used path
# ----------------------------------------------------------------------
def test_click_starts_timer(qt_app) -> None:  # type: ignore[no-untyped-def]
    panel, tracker = _make_panel(qt_app)
    panel._on_spell_clicked("EnemyMid", "Flash")
    cd = tracker.get("EnemyMid", "Flash")
    assert cd is not None
    assert cd.cast_at == 600.0
    assert cd.cooldown == 300.0


def test_right_click_resets_timer(qt_app) -> None:  # type: ignore[no-untyped-def]
    panel, tracker = _make_panel(qt_app)
    panel._on_spell_clicked("EnemyMid", "Flash")
    assert tracker.get("EnemyMid", "Flash") is not None
    panel._on_spell_right_clicked("EnemyMid", "Flash")
    assert tracker.get("EnemyMid", "Flash") is None


def test_click_with_unknown_spell_is_noop(qt_app) -> None:  # type: ignore[no-untyped-def]
    panel, tracker = _make_panel(qt_app)
    panel._on_spell_clicked("EnemyMid", "?")
    assert tracker.get("EnemyMid", "?") is None


def test_empty_summoner_or_spell_is_noop(qt_app) -> None:  # type: ignore[no-untyped-def]
    panel, tracker = _make_panel(qt_app)
    panel._on_spell_clicked("", "Flash")
    panel._on_spell_clicked("EnemyMid", "")
    assert len(tracker._entries) == 0


# ----------------------------------------------------------------------
# Focus guard
# ----------------------------------------------------------------------
def test_focus_guard_blocks_click_when_inactive(qt_app) -> None:  # type: ignore[no-untyped-def]
    panel, tracker = _make_panel(qt_app, app_active=False)
    panel._on_spell_clicked("EnemyMid", "Flash")
    assert tracker.get("EnemyMid", "Flash") is None


def test_focus_guard_blocks_right_click_when_inactive(qt_app) -> None:  # type: ignore[no-untyped-def]
    panel, tracker = _make_panel(qt_app)
    panel._on_spell_clicked("EnemyMid", "Flash")
    # Now go inactive.
    panel._app_is_active = lambda: False  # type: ignore[method-assign]
    panel._on_spell_right_clicked("EnemyMid", "Flash")
    # Reset blocked — timer still there.
    assert tracker.get("EnemyMid", "Flash") is not None


# ----------------------------------------------------------------------
# Duplicate-click protection
# ----------------------------------------------------------------------
def test_dedup_blocks_rapid_repeat_click(qt_app) -> None:  # type: ignore[no-untyped-def]
    panel, tracker = _make_panel(qt_app)
    panel._on_spell_clicked("EnemyMid", "Flash")
    first_cast = tracker.get("EnemyMid", "Flash").cast_at
    # Game time advances slightly to prove the SECOND click had real
    # input but was filtered.
    panel._latest_game_time = 620.0
    panel._on_spell_clicked("EnemyMid", "Flash")
    after_cast = tracker.get("EnemyMid", "Flash").cast_at
    # Second click was within MIN_TIMER_INTERVAL_S, so cast_at unchanged.
    assert after_cast == first_cast


def test_dedup_only_per_summoner_spell_pair(qt_app) -> None:  # type: ignore[no-untyped-def]
    """Different spell on the same summoner should not be dedup'd."""
    panel, tracker = _make_panel(qt_app)
    panel._on_spell_clicked("EnemyMid", "Flash")
    panel._on_spell_clicked("EnemyMid", "Ignite")
    assert tracker.get("EnemyMid", "Flash") is not None
    assert tracker.get("EnemyMid", "Ignite") is not None


def test_dedup_respects_min_interval(qt_app) -> None:  # type: ignore[no-untyped-def]
    """After waiting > MIN_TIMER_INTERVAL_S, a second click is accepted."""
    import time as _t
    panel, tracker = _make_panel(qt_app)
    panel._on_spell_clicked("EnemyMid", "Flash")
    first_cast = tracker.get("EnemyMid", "Flash").cast_at

    # Advance the dedup clock by tampering with the internal map —
    # avoids actually sleeping in the test.
    key = ("EnemyMid", "Flash")
    panel._last_click_at[key] = panel._last_click_at[key] - 5.0

    panel._latest_game_time = 700.0
    panel._on_spell_clicked("EnemyMid", "Flash")
    new_cast = tracker.get("EnemyMid", "Flash").cast_at
    assert new_cast == 700.0
    assert new_cast != first_cast


# ----------------------------------------------------------------------
# Telemetry — events fire on accepted clicks, not on blocked ones
# ----------------------------------------------------------------------
def test_telemetry_emitted_on_click(qt_app) -> None:  # type: ignore[no-untyped-def]
    from champ_assistant import telemetry
    panel, _ = _make_panel(qt_app)

    received: list[tuple] = []

    def _capture(event: str, payload: dict | None = None) -> None:
        received.append((event, payload))

    rec = telemetry.recorder()
    original = rec.record
    rec.record = _capture  # type: ignore[method-assign]
    try:
        panel._on_spell_clicked("EnemyMid", "Flash")
    finally:
        rec.record = original  # type: ignore[method-assign]

    started = [r for r in received if r[0] == telemetry.EV_SPELL_TIMER_STARTED]
    assert len(started) == 1
    payload = started[0][1]
    assert payload["player_id"] == "EnemyMid"
    assert payload["spell"] == "Flash"
    assert payload["source"] == "scoreboard_click"


def test_telemetry_not_emitted_on_blocked_click(qt_app) -> None:  # type: ignore[no-untyped-def]
    """A focus-guard-blocked click must NOT produce a telemetry
    event (otherwise the "click was accepted" semantic of the event
    is broken)."""
    from champ_assistant import telemetry
    panel, _ = _make_panel(qt_app, app_active=False)

    received: list[tuple] = []
    rec = telemetry.recorder()
    original = rec.record
    rec.record = lambda e, p=None: received.append((e, p))  # type: ignore[method-assign]
    try:
        panel._on_spell_clicked("EnemyMid", "Flash")
    finally:
        rec.record = original  # type: ignore[method-assign]

    started = [r for r in received if r[0] == telemetry.EV_SPELL_TIMER_STARTED]
    assert started == []


def test_telemetry_emitted_on_right_click_reset(qt_app) -> None:  # type: ignore[no-untyped-def]
    from champ_assistant import telemetry
    panel, _ = _make_panel(qt_app)
    panel._on_spell_clicked("EnemyMid", "Flash")

    received: list[tuple] = []
    rec = telemetry.recorder()
    original = rec.record
    rec.record = lambda e, p=None: received.append((e, p))  # type: ignore[method-assign]
    try:
        panel._on_spell_right_clicked("EnemyMid", "Flash")
    finally:
        rec.record = original  # type: ignore[method-assign]

    resets = [r for r in received if r[0] == telemetry.EV_SPELL_TIMER_RESET]
    assert len(resets) == 1
