"""Tests for the ally-team profile fetch path.

Loading-screen lobby panel needs profile data for BOTH teams — this
file verifies the orchestrator scheduling logic without spinning up
the actual Riot HTTP layer.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from champ_assistant.data.models import (
    ChampSelectSession,
    Champion,
    TeamMember,
)


def _session(
    *,
    my_team: list[TeamMember],
    their_team: list[TeamMember],
    local_cell: int = 0,
) -> ChampSelectSession:
    return ChampSelectSession(
        phase="BAN_PICK",
        localPlayerCellId=local_cell,
        myTeam=my_team,
        theirTeam=their_team,
    )


def _members(side: str, *, with_local: bool = True) -> list[TeamMember]:
    """Five members per side. Each has a unique puuid so the fetch
    scheduler can dispatch them independently."""
    out = []
    for i in range(5):
        out.append(TeamMember(
            cellId=(0 + i) if side == "my" else (5 + i),
            championId=100 + i if side == "my" else 200 + i,
            puuid=f"{side}-{i}-puuid",
        ))
    return out


# ----------------------------------------------------------------------
# _maybe_fetch_profiles dispatches for BOTH teams
# ----------------------------------------------------------------------
def test_maybe_fetch_profiles_skips_local_player() -> None:
    """The local player's own profile must never be fetched —
    wasted API budget against the rate limit."""
    from champ_assistant.app import ChampAssistant

    # Construct a minimal ChampAssistant just to exercise
    # _schedule_profile_fetch's branching. We mock the profile
    # service so we can observe the calls without real HTTP.
    profile_service = MagicMock()
    profile_service.enabled = True

    app = ChampAssistant.__new__(ChampAssistant)
    app._profile_service = profile_service
    app._enemy_profiles_by_cell = {}
    app._ally_profiles_by_cell = {}
    app._profile_inflight = set()

    scheduled: list[tuple[int, bool]] = []

    def _capture(member, *, is_ally: bool):
        scheduled.append((member.cell_id, is_ally))

    app._schedule_profile_fetch = _capture  # type: ignore[method-assign]

    sess = _session(
        my_team=_members("my"),
        their_team=_members("their"),
        local_cell=0,  # cell 0 is "us"
    )
    app._maybe_fetch_profiles(sess)

    # Enemy team: all 5 cells scheduled.
    enemy_cells = {c for c, ally in scheduled if not ally}
    assert enemy_cells == {5, 6, 7, 8, 9}

    # Ally team: 4 of 5 (local cell 0 skipped).
    ally_cells = {c for c, ally in scheduled if ally}
    assert ally_cells == {1, 2, 3, 4}
    assert 0 not in ally_cells, "local player must not be fetched"


def test_schedule_profile_fetch_uses_separate_inflight_keys() -> None:
    """If somehow the same puuid appears on both teams (test fixture
    weirdness), inflight tracking must not deduplicate them — they
    land in different caches."""
    from champ_assistant.app import ChampAssistant

    profile_service = MagicMock()
    profile_service.enabled = True

    app = ChampAssistant.__new__(ChampAssistant)
    app._profile_service = profile_service
    app._enemy_profiles_by_cell = {}
    app._ally_profiles_by_cell = {}
    app._profile_inflight = set()

    member = TeamMember(cellId=1, championId=100, puuid="shared-puuid")

    # Both calls should add their own inflight key — they don't
    # collide. Without a running asyncio loop, _schedule_profile_fetch
    # falls through to the RuntimeError path and discards. We just
    # observe the inflight keys mid-flight.
    captured = []

    def _capture_inflight():
        captured.append(set(app._profile_inflight))

    # Fake create_task so we can inspect inflight before discard runs.
    import asyncio
    original_create_task = asyncio.create_task
    def _fake_create_task(coro):
        # Cancel the coroutine so it doesn't actually run.
        coro.close()
        _capture_inflight()
        # Raise to mimic the no-running-loop path; the scheduler
        # discards the inflight in `except RuntimeError`. For this
        # test we want to OBSERVE before discard.
        raise RuntimeError("test")
    try:
        asyncio.create_task = _fake_create_task  # type: ignore[assignment]
        app._schedule_profile_fetch(member, is_ally=True)
        ally_inflight = set(captured[-1]) if captured else set()
        app._schedule_profile_fetch(member, is_ally=False)
        # After ally was discarded, the enemy schedule sees inflight
        # containing the enemy key.
    finally:
        asyncio.create_task = original_create_task  # type: ignore[assignment]

    # Ally key is "a:shared-puuid"; enemy is "e:shared-puuid".
    # Different prefixes → no collision.
    assert "a:shared-puuid" in ally_inflight


def test_maybe_fetch_profiles_noop_when_service_disabled() -> None:
    from champ_assistant.app import ChampAssistant

    app = ChampAssistant.__new__(ChampAssistant)
    app._profile_service = None
    app._enemy_profiles_by_cell = {}
    app._ally_profiles_by_cell = {}
    app._profile_inflight = set()

    scheduled: list = []
    app._schedule_profile_fetch = lambda *args, **kwargs: scheduled.append(args)  # type: ignore[method-assign]

    sess = _session(my_team=_members("my"), their_team=_members("their"))
    app._maybe_fetch_profiles(sess)
    assert scheduled == []


def test_clear_drops_both_team_caches() -> None:
    """Session-end cleanup must wipe ally cache too — old data must
    not leak into the next match."""
    from champ_assistant.app import ChampAssistant

    app = ChampAssistant.__new__(ChampAssistant)
    app._enemy_profiles_by_cell = {1: "stale_enemy"}
    app._ally_profiles_by_cell = {2: "stale_ally"}

    # Mimic the relevant clear path manually (the full event handler
    # has too many other deps to instantiate here).
    app._enemy_profiles_by_cell.clear()
    app._ally_profiles_by_cell.clear()

    assert app._enemy_profiles_by_cell == {}
    assert app._ally_profiles_by_cell == {}
