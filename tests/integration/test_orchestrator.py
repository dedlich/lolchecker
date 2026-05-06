"""Integration tests: wire LcuSource → ChampAssistant → MainOverlay end-to-end."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from champ_assistant.app import ChampAssistant  # noqa: E402
from champ_assistant.data.loader import (  # noqa: E402
    load_counters,
    load_tags,
    load_tiers,
)
from champ_assistant.data.models import Champion  # noqa: E402
from champ_assistant.lcu.sources import FixtureLcuSource  # noqa: E402
from champ_assistant.ui.overlay import MainOverlay  # noqa: E402
from champ_assistant.ui.view_model import SessionView  # noqa: E402


REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "static"
FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures" / "sessions"


@pytest.fixture
def champions() -> dict[int, Champion]:
    """Subset of champions referenced by the session fixtures + seed data."""
    return {
        86: Champion(id=86, key="Garen", name="Garen", tags=["Fighter", "Tank"]),
        64: Champion(id=64, key="Lee Sin", name="Lee Sin", tags=["Fighter"]),
        103: Champion(id=103, key="Ahri", name="Ahri", tags=["Mage"]),
        22: Champion(id=22, key="Ashe", name="Ashe", tags=["Marksman"]),
        412: Champion(id=412, key="Thresh", name="Thresh", tags=["Tank", "Engage"]),
        60: Champion(id=60, key="Elise", name="Elise", tags=["Mage", "Assassin"]),
        7: Champion(id=7, key="LeBlanc", name="LeBlanc", tags=["Assassin", "Mage"]),
        145: Champion(id=145, key="Kaisa", name="Kai'Sa", tags=["Marksman"]),
        53: Champion(id=53, key="Blitzcrank", name="Blitzcrank", tags=["Tank", "Engage"]),
        21: Champion(id=21, key="MissFortune", name="Miss Fortune", tags=["Marksman"]),
    }


@pytest.fixture
def assistant(qtbot, champions):  # type: ignore[no-untyped-def]
    overlay = MainOverlay()
    qtbot.addWidget(overlay)
    seen_views: list[SessionView] = []
    a = ChampAssistant(
        source=FixtureLcuSource(FIXTURES_DIR, interval=0.0),
        overlay=overlay,
        counters=load_counters(DATA_DIR / "counters.json"),
        tiers=load_tiers(DATA_DIR / "tiers.json"),
        tags=load_tags(DATA_DIR / "tags.json"),
        champions=champions,
        view_callback=seen_views.append,
    )
    a._seen_views = seen_views  # type: ignore[attr-defined]
    return a


# ---------------------------------------------------------------------------
# Lifecycle event handling
# ---------------------------------------------------------------------------

def test_waiting_event_sets_overlay_state(assistant) -> None:  # type: ignore[no-untyped-def]
    view = assistant.handle_event({"type": "waiting_for_client"})
    assert view.connection_state == "waiting"
    assert assistant.overlay.status_bar.state == "waiting"


def test_connected_event_without_session_yields_empty_view(assistant) -> None:  # type: ignore[no-untyped-def]
    view = assistant.handle_event({"type": "connected"})
    assert view.connection_state == "connected"
    assert view.session is None
    assert view.suggestions == []


def test_disconnected_clears_session(assistant) -> None:  # type: ignore[no-untyped-def]
    # First put a session in.
    raw = json.loads((FIXTURES_DIR / "04_my_turn_top.json").read_text())
    assistant.handle_event({"type": "session", "data": raw})
    assert assistant._latest_session is not None
    # Now disconnect.
    view = assistant.handle_event({"type": "disconnected"})
    assert view.connection_state == "disconnected"
    assert assistant._latest_session is None


def test_session_ended_clears_session_keeps_connection(assistant) -> None:  # type: ignore[no-untyped-def]
    """LCU's WS Delete event now fires session_ended, which should clear
    the cached session without dropping the connection state - prevents
    UI flicker between consecutive custom-game champ selects."""
    raw = json.loads((FIXTURES_DIR / "04_my_turn_top.json").read_text())
    assistant.handle_event({"type": "session", "data": raw})
    assert assistant._latest_session is not None
    # Connection was set to connected by the session event; preserve it.
    assert assistant._connection_state == "connected"

    view = assistant.handle_event({"type": "session_ended"})
    assert assistant._latest_session is None
    assert view.connection_state == "connected"  # NOT disconnected
    assert assistant._connection_state == "connected"


def test_unknown_event_does_not_crash(assistant) -> None:  # type: ignore[no-untyped-def]
    view = assistant.handle_event({"type": "definitely_not_a_real_event"})
    assert view.connection_state == "disconnected"


def test_malformed_session_event_is_logged_and_ignored(assistant) -> None:  # type: ignore[no-untyped-def]
    view = assistant.handle_event({"type": "session", "data": "not a dict"})
    # Stays in current state (disconnected initially), no crash.
    assert view.connection_state == "disconnected"


def test_session_parse_failure_does_not_crash(assistant) -> None:  # type: ignore[no-untyped-def]
    view = assistant.handle_event(
        {"type": "session", "data": {"phase": "BAN_PICK", "myTeam": "garbage"}}
    )
    assert view.session is None  # parse failed, stayed empty


# ---------------------------------------------------------------------------
# Full session → view pipeline
# ---------------------------------------------------------------------------

def test_session_event_builds_complete_view(assistant) -> None:  # type: ignore[no-untyped-def]
    raw = json.loads((FIXTURES_DIR / "04_my_turn_top.json").read_text())
    view = assistant.handle_event({"type": "session", "data": raw})

    assert view.connection_state == "connected"
    assert view.session is not None
    assert view.session.local_player_cell_id == 0

    # Enemies have names resolved from the champions dict.
    assert view.enemy_names[86] == "Garen"
    assert view.enemy_names[60] == "Elise"

    # Garen TOP is in the seed counter matrix → expect counters for cell 5.
    assert 5 in view.enemy_counters
    assert any(c.champion == "Darius" for c in view.enemy_counters[5])

    # Local player is TOP → suggestions exist (Darius + others from tier list).
    assert len(view.suggestions) > 0
    keys = [s.champion_key for s in view.suggestions]
    assert "Darius" in keys

    # Drafted champions are excluded from suggestions.
    drafted_my_team_keys = {"Lee Sin", "Ahri", "Ashe", "Thresh"}
    assert not (set(keys) & drafted_my_team_keys)


def test_overlay_status_bar_reflects_handle_event(assistant) -> None:  # type: ignore[no-untyped-def]
    assistant.handle_event({"type": "waiting_for_client"})
    assert assistant.overlay.status_bar.state == "waiting"
    assistant.handle_event({"type": "connected"})
    assert assistant.overlay.status_bar.state == "connected"


def test_tag_inference_overrides_cell_order_for_enemy_role(qtbot, champions) -> None:  # type: ignore[no-untyped-def]
    """A pure Marksman in the TOP cell (5) should still be classified as BOT
    via tag inference (cell-order would have said TOP)."""
    overlay = MainOverlay()
    qtbot.addWidget(overlay)
    a = ChampAssistant(
        source=FixtureLcuSource(FIXTURES_DIR, interval=0.0),
        overlay=overlay,
        counters=load_counters(DATA_DIR / "counters.json"),
        tiers=load_tiers(DATA_DIR / "tiers.json"),
        tags=load_tags(DATA_DIR / "tags.json"),
        champions=champions,
    )
    raw = {
        "phase": "BAN_PICK",
        "localPlayerCellId": 0,
        "myTeam": [{"cellId": 0, "championId": 86, "assignedPosition": "TOP"}],
        "theirTeam": [
            # cell 5 = TOP by cell-order, but championId 21 (Miss Fortune)
            # is tagged Marksman → should resolve to BOT.
            {"cellId": 5, "championId": 21},
        ],
    }
    view = a.handle_event({"type": "session", "data": raw})
    assert view.enemy_roles[5] == "BOT"
    assert 5 not in view.enemy_role_overridden  # auto-inference, not manual


def test_manual_override_takes_priority_and_is_marked(qtbot, champions) -> None:  # type: ignore[no-untyped-def]
    overlay = MainOverlay()
    qtbot.addWidget(overlay)
    a = ChampAssistant(
        source=FixtureLcuSource(FIXTURES_DIR, interval=0.0),
        overlay=overlay,
        counters=load_counters(DATA_DIR / "counters.json"),
        tiers=load_tiers(DATA_DIR / "tiers.json"),
        tags=load_tags(DATA_DIR / "tags.json"),
        champions=champions,
    )
    raw = {
        "phase": "BAN_PICK", "localPlayerCellId": 0,
        "myTeam": [{"cellId": 0, "championId": 86, "assignedPosition": "TOP"}],
        "theirTeam": [{"cellId": 5, "championId": 21}],  # Miss Fortune (Marksman) → auto-resolves to BOT
    }
    a.handle_event({"type": "session", "data": raw})

    a.set_enemy_role_override(5, "TOP")  # contradicts auto-inference
    # set_enemy_role_override re-renders; check the overlay's last view.
    last = overlay._last_view  # type: ignore[attr-defined]
    assert last is not None
    assert last.enemy_roles[5] == "TOP"
    assert 5 in last.enemy_role_overridden


def test_cycle_enemy_role_advances_through_all_roles(qtbot, champions) -> None:  # type: ignore[no-untyped-def]
    overlay = MainOverlay()
    qtbot.addWidget(overlay)
    a = ChampAssistant(
        source=FixtureLcuSource(FIXTURES_DIR, interval=0.0),
        overlay=overlay,
        counters=load_counters(DATA_DIR / "counters.json"),
        tiers=load_tiers(DATA_DIR / "tiers.json"),
        tags=load_tags(DATA_DIR / "tags.json"),
        champions=champions,
    )
    raw = {
        "phase": "BAN_PICK", "localPlayerCellId": 0,
        "myTeam": [{"cellId": 0, "championId": 86, "assignedPosition": "TOP"}],
        "theirTeam": [{"cellId": 5, "championId": 21}],
    }
    a.handle_event({"type": "session", "data": raw})

    cycle_observed = []
    for _ in range(7):  # full cycle is 6 (None + 5 roles); 7 wraps around
        cycle_observed.append(a.cycle_enemy_role_override(5))
    assert cycle_observed == ["TOP", "JUNGLE", "MID", "BOT", "SUPPORT", None, "TOP"]


def test_pick_suggestions_prioritize_lane_opponent_counters(qtbot, champions) -> None:  # type: ignore[no-untyped-def]
    """When the enemy in MY role is locked in, suggestions should be the
    counters against THEM (filtered + scored), not generic tier-based picks."""
    overlay = MainOverlay()
    qtbot.addWidget(overlay)
    a = ChampAssistant(
        source=FixtureLcuSource(FIXTURES_DIR, interval=0.0),
        overlay=overlay,
        counters=load_counters(DATA_DIR / "counters.json"),
        tiers=load_tiers(DATA_DIR / "tiers.json"),
        tags=load_tags(DATA_DIR / "tags.json"),
        champions=champions,
    )
    raw = {
        "phase": "BAN_PICK",
        "localPlayerCellId": 0,
        "myTeam": [
            {"cellId": 0, "championId": 0, "assignedPosition": "TOP"},
        ],
        "theirTeam": [
            # Garen TOP is locked in cell 5 — our seed has counters for Garen TOP.
            {"cellId": 5, "championId": 86},
        ],
    }
    view = a.handle_event({"type": "session", "data": raw})

    # The seed counter matrix has Darius S+ and Vayne A as top counters
    # for Garen TOP. Suggestions should match.
    keys = [s.champion_key for s in view.suggestions]
    assert "Darius" in keys
    # Reasons should call out the lane opponent explicitly.
    darius = next(s for s in view.suggestions if s.champion_key == "Darius")
    assert any("Garen" in r for r in darius.reasons)


def test_pick_suggestions_fallback_when_lane_opponent_not_locked(qtbot, champions) -> None:  # type: ignore[no-untyped-def]
    """Without a locked lane opponent, fall back to tier-based suggestions."""
    overlay = MainOverlay()
    qtbot.addWidget(overlay)
    a = ChampAssistant(
        source=FixtureLcuSource(FIXTURES_DIR, interval=0.0),
        overlay=overlay,
        counters=load_counters(DATA_DIR / "counters.json"),
        tiers=load_tiers(DATA_DIR / "tiers.json"),
        tags=load_tags(DATA_DIR / "tags.json"),
        champions=champions,
    )
    raw = {
        "phase": "BAN_PICK",
        "localPlayerCellId": 0,
        "myTeam": [{"cellId": 0, "championId": 0, "assignedPosition": "TOP"}],
        "theirTeam": [{"cellId": 5, "championId": 0}],  # nothing picked yet
    }
    view = a.handle_event({"type": "session", "data": raw})
    # Should still produce suggestions (tier-based for TOP).
    assert len(view.suggestions) > 0
    # No suggestion's reasons should mention a specific enemy counter.
    for s in view.suggestions:
        assert not any("Counters" in r for r in s.reasons)


def test_update_champions_resolves_previously_unknown_enemy(qtbot, champions) -> None:  # type: ignore[no-untyped-def]
    """A session that references champion ids missing from the bootstrap dict
    should fall back to numeric placeholders, then resolve once Data Dragon
    delivers the full roster."""
    overlay = MainOverlay()
    qtbot.addWidget(overlay)
    seen: list[SessionView] = []
    a = ChampAssistant(
        source=FixtureLcuSource(FIXTURES_DIR, interval=0.0),
        overlay=overlay,
        counters=load_counters(DATA_DIR / "counters.json"),
        tiers=load_tiers(DATA_DIR / "tiers.json"),
        tags=load_tags(DATA_DIR / "tags.json"),
        champions={},  # start empty — every enemy is "unknown"
        view_callback=seen.append,
    )

    raw = json.loads((FIXTURES_DIR / "04_my_turn_top.json").read_text())
    a.handle_event({"type": "session", "data": raw})
    pre = seen[-1]
    assert pre.enemy_names == {}  # nothing resolved yet

    # Now hydrate the champion table the way the prefetch task would.
    a.update_champions(champions)
    post = seen[-1]
    assert "Garen" in post.enemy_names.values()


def test_update_champions_with_empty_dict_is_noop(assistant, champions) -> None:  # type: ignore[no-untyped-def]
    """Empty fetch (network failure → empty dict) must not wipe the existing table."""
    raw = json.loads((FIXTURES_DIR / "04_my_turn_top.json").read_text())
    assistant.handle_event({"type": "session", "data": raw})
    snapshot = dict(assistant.champions)
    assistant.update_champions({})
    assert assistant.champions == snapshot


def test_refresh_shortcut_rebuilds_view(assistant) -> None:  # type: ignore[no-untyped-def]
    raw = json.loads((FIXTURES_DIR / "04_my_turn_top.json").read_text())
    assistant.handle_event({"type": "session", "data": raw})
    snapshot_count = len(assistant._seen_views)  # type: ignore[attr-defined]
    assistant._on_refresh_requested()
    assert len(assistant._seen_views) == snapshot_count + 1  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# End-to-end with FixtureLcuSource
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_consumes_fixture_source_to_completion(
    qtbot, champions  # type: ignore[no-untyped-def]
) -> None:
    overlay = MainOverlay()
    qtbot.addWidget(overlay)
    seen: list[SessionView] = []
    assistant = ChampAssistant(
        source=FixtureLcuSource(FIXTURES_DIR, interval=0.0),
        overlay=overlay,
        counters=load_counters(DATA_DIR / "counters.json"),
        tiers=load_tiers(DATA_DIR / "tiers.json"),
        tags=load_tags(DATA_DIR / "tags.json"),
        champions=champions,
        view_callback=seen.append,
    )
    await asyncio.wait_for(assistant.run(), timeout=2.0)

    types = [v.connection_state for v in seen]
    assert "connected" in types
    # 3 valid fixtures + the implicit "connected" lifecycle event = at least 3 views
    sessions_seen = [v for v in seen if v.session is not None]
    assert len(sessions_seen) >= 3
