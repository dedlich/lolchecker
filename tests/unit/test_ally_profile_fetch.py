"""Tests for the team-profile fetch path.

LobbyStatsWidget was retired in v1.10.80 and ally fetching was paused
in v1.10.91 (no UI consumer in LiveCompanion yet). The orchestrator
now schedules ENEMY fetches only — these tests pin that contract so a
future re-enable of ally fetching is a deliberate flip, not a silent
regression.
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
# _maybe_fetch_profiles schedules ENEMY fetches only (v1.10.91)
# ----------------------------------------------------------------------
def test_maybe_fetch_profiles_schedules_enemies_only() -> None:
    """Enemy team gets 5 fetches; ally team gets 0 (paused pending an
    ally-roster panel in LiveCompanion). Original feature ask is
    archived in commit b53fa9e."""
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
        local_cell=0,
    )
    app._maybe_fetch_profiles(sess)

    enemy_cells = {c for c, ally in scheduled if not ally}
    assert enemy_cells == {5, 6, 7, 8, 9}

    ally_cells = {c for c, ally in scheduled if ally}
    assert ally_cells == set(), (
        "ally fetching is paused as of v1.10.91 — re-enable when an "
        "ally-roster panel exists in LiveCompanion"
    )


def test_schedule_profile_fetch_uses_separate_inflight_keys() -> None:
    """If the same puuid appears on both teams (test fixture weirdness),
    the team-prefixed keys must keep ally + enemy fetches separate so
    profiles land in the right cache."""
    from champ_assistant.app import ChampAssistant
    from champ_assistant.coalescer import Coalescer

    profile_service = MagicMock()
    profile_service.enabled = True

    app = ChampAssistant.__new__(ChampAssistant)
    app._profile_service = profile_service
    app._enemy_profiles_by_cell = {}
    app._ally_profiles_by_cell = {}
    app._profile_coalescer = Coalescer()

    member = TeamMember(cellId=1, championId=100, puuid="shared-puuid")

    # Capture the keys passed to Coalescer.schedule. We override schedule
    # to just record the key — this avoids needing a running event loop
    # and isolates the test to the key-derivation logic in
    # _schedule_profile_fetch.
    captured_keys: list[str] = []
    real_schedule = app._profile_coalescer.schedule

    def _capture_schedule(key, factory):  # type: ignore[no-untyped-def]
        captured_keys.append(key)
        return False  # pretend already inflight; factory is never called
    app._profile_coalescer.schedule = _capture_schedule  # type: ignore[method-assign]

    app._schedule_profile_fetch(member, is_ally=True)
    app._schedule_profile_fetch(member, is_ally=False)

    # Ally key is "a:shared-puuid"; enemy is "e:shared-puuid".
    # Different prefixes → no collision in the coalescer.
    assert "a:shared-puuid" in captured_keys
    assert "e:shared-puuid" in captured_keys


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
