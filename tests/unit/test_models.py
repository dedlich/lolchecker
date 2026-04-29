"""Tests for Pydantic v2 domain models."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from champ_assistant.data.models import (
    Action,
    Champion,
    ChampSelectSession,
    CounterEntry,
    CounterMatrix,
    TagsData,
    TeamMember,
    TierEntry,
    TierList,
    normalize_role,
)


# ---------------------------------------------------------------------------
# Role normalization
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "lcu_role,expected",
    [
        ("TOP", "TOP"),
        ("JUNGLE", "JUNGLE"),
        ("MIDDLE", "MID"),
        ("BOTTOM", "BOT"),
        ("UTILITY", "SUPPORT"),
        ("middle", "MID"),  # case-insensitive
        (None, None),
        ("", None),
        ("UNKNOWN", None),
    ],
)
def test_normalize_role(lcu_role: str | None, expected: str | None) -> None:
    assert normalize_role(lcu_role) == expected


# ---------------------------------------------------------------------------
# Champion / TeamMember
# ---------------------------------------------------------------------------

def test_champion_frozen_and_defaults() -> None:
    c = Champion(id=86, key="Garen", name="Garen")
    assert c.tags == []
    with pytest.raises(ValidationError):
        c.id = 7  # type: ignore[misc]


def test_team_member_defaults() -> None:
    tm = TeamMember(cell_id=0)
    assert tm.champion_id == 0
    assert tm.assigned_position is None
    assert tm.locked is False


def test_team_member_normalizes_lcu_position() -> None:
    """LCU sends MIDDLE/BOTTOM/UTILITY — model normalizes to MID/BOT/SUPPORT."""
    assert TeamMember(cell_id=0, assigned_position="MIDDLE").assigned_position == "MID"
    assert TeamMember(cell_id=0, assigned_position="BOTTOM").assigned_position == "BOT"
    assert TeamMember(cell_id=0, assigned_position="UTILITY").assigned_position == "SUPPORT"


def test_team_member_rejects_unknown_position() -> None:
    with pytest.raises(ValidationError):
        TeamMember(cell_id=0, assigned_position="NOT_A_ROLE")  # type: ignore[arg-type]


def test_team_member_accepts_lcu_camelcase_keys() -> None:
    raw = {"cellId": 3, "championId": 86, "summonerId": 42, "assignedPosition": "BOTTOM"}
    tm = TeamMember.model_validate(raw)
    assert tm.cell_id == 3
    assert tm.champion_id == 86
    assert tm.summoner_id == 42
    assert tm.assigned_position == "BOT"


def test_session_phase_falls_back_to_timer_phase() -> None:
    """Real LCU sessions often have phase only inside timer."""
    raw = {
        "localPlayerCellId": 0,
        "myTeam": [],
        "theirTeam": [],
        "timer": {"phase": "BAN_PICK", "adjustedTimeLeftInPhase": 12000},
    }
    session = ChampSelectSession.model_validate(raw)
    assert session.phase == "BAN_PICK"


def test_session_top_level_phase_wins_over_timer() -> None:
    raw = {
        "phase": "GAME_STARTING",
        "myTeam": [],
        "theirTeam": [],
        "timer": {"phase": "FINALIZATION"},
    }
    session = ChampSelectSession.model_validate(raw)
    assert session.phase == "GAME_STARTING"


def test_session_ignores_unknown_top_level_fields() -> None:
    """Real LCU payloads carry dozens of extras (actions, bans, chatDetails…)."""
    raw = {
        "phase": "BAN_PICK",
        "myTeam": [],
        "theirTeam": [],
        "actions": [[]],
        "bans": {"myTeamBans": [], "numBans": 6},
        "chatDetails": {"chatRoomName": "x", "chatRoomPassword": "y"},
        "isCustomGame": False,
        "skipChampionSelect": False,
        "trades": [],
    }
    ChampSelectSession.model_validate(raw)  # must not raise


def test_team_member_tolerates_null_ints() -> None:
    """LCU sometimes sends championId=null in transitional phases."""
    tm = TeamMember.model_validate(
        {"cellId": None, "championId": None, "summonerId": None}
    )
    assert tm.cell_id == -1
    assert tm.champion_id == 0
    assert tm.summoner_id is None


def test_team_member_tolerates_empty_position_and_extras() -> None:
    raw = {
        "cellId": 0,
        "championId": 86,
        "assignedPosition": "",
        "championPickIntent": 0,
        "puuid": "abc",
        "spell1Id": 4,
        "team": 1,
        "wardSkinId": -1,
    }
    tm = TeamMember.model_validate(raw)
    assert tm.cell_id == 0
    assert tm.assigned_position is None


def test_session_parses_full_lcu_payload() -> None:
    raw = {
        "phase": "BAN_PICK",
        "localPlayerCellId": 2,
        "myTeam": [
            {"cellId": 0, "championId": 86, "assignedPosition": "TOP"},
            {"cellId": 2, "championId": 0, "assignedPosition": "MIDDLE"},
        ],
        "theirTeam": [
            {"cellId": 5, "championId": 64, "assignedPosition": "JUNGLE"},
        ],
    }
    session = ChampSelectSession.model_validate(raw)
    assert session.local_player_cell_id == 2
    assert session.my_team[1].assigned_position == "MID"
    assert session.their_team[0].assigned_position == "JUNGLE"


# ---------------------------------------------------------------------------
# CounterEntry / CounterMatrix
# ---------------------------------------------------------------------------

def test_counter_entry_score_in_range() -> None:
    CounterEntry(champion="Darius", score=0.0)
    CounterEntry(champion="Darius", score=10.0)
    with pytest.raises(ValidationError):
        CounterEntry(champion="Darius", score=-0.1)
    with pytest.raises(ValidationError):
        CounterEntry(champion="Darius", score=10.1)


def test_counter_entry_tier_validation() -> None:
    CounterEntry(champion="Darius", score=5, tier="S+")
    with pytest.raises(ValidationError):
        CounterEntry(champion="Darius", score=5, tier="SS")  # type: ignore[arg-type]


def test_counter_matrix_lookup_handles_missing_keys() -> None:
    cm = CounterMatrix(
        matrix={
            "Garen": {
                "TOP": [CounterEntry(champion="Darius", score=8.0)],
            }
        }
    )
    assert len(cm.counters_for("Garen", "TOP")) == 1
    assert cm.counters_for("Yasuo", "MID") == []
    assert cm.counters_for("Garen", "JUNGLE") == []


def test_counter_matrix_extra_fields_are_ignored() -> None:
    cm = CounterMatrix.model_validate(
        {"$schema": "v0", "patch": "14.8", "matrix": {}}
    )
    assert cm.patch == "14.8"


# ---------------------------------------------------------------------------
# TierList / TagsData
# ---------------------------------------------------------------------------

def test_tier_list_lookup() -> None:
    tl = TierList(
        tiers={
            "TOP": [
                TierEntry(champion="Darius", tier="S+"),
                TierEntry(champion="Garen", tier="A"),
            ]
        }
    )
    assert tl.tier_for("Darius", "TOP") == "S+"
    assert tl.tier_for("Garen", "TOP") == "A"
    assert tl.tier_for("Yasuo", "TOP") is None
    assert tl.tier_for("Darius", "MID") is None


def test_tags_data_lookup() -> None:
    td = TagsData(tags={"Garen": ["Fighter", "Tank"]})
    assert td.tags_for("Garen") == ["Fighter", "Tank"]
    assert td.tags_for("Unknown") == []


# ---------------------------------------------------------------------------
# ChampSelectSession
# ---------------------------------------------------------------------------

def test_session_me_returns_correct_team_member() -> None:
    session = ChampSelectSession(
        phase="BAN_PICK",
        local_player_cell_id=2,
        my_team=[
            TeamMember(cell_id=0),
            TeamMember(cell_id=1),
            TeamMember(cell_id=2, champion_id=86),
            TeamMember(cell_id=3),
            TeamMember(cell_id=4),
        ],
    )
    me = session.me
    assert me is not None
    assert me.cell_id == 2
    assert me.champion_id == 86


def test_session_me_none_when_cell_id_not_found() -> None:
    session = ChampSelectSession(
        phase="BAN_PICK",
        local_player_cell_id=99,
        my_team=[TeamMember(cell_id=0)],
    )
    assert session.me is None


def test_session_defaults_empty_teams() -> None:
    session = ChampSelectSession(phase="UNKNOWN")
    assert session.local_player_cell_id == -1
    assert session.my_team == []
    assert session.their_team == []
    assert session.me is None


# ---------------------------------------------------------------------------
# Action / my_pending_action
# ---------------------------------------------------------------------------

def _session_with_actions(actions: list[list[dict]], cell_id: int = 0) -> ChampSelectSession:
    return ChampSelectSession.model_validate({
        "phase": "BAN_PICK",
        "localPlayerCellId": cell_id,
        "myTeam": [{"cellId": cell_id}],
        "theirTeam": [],
        "actions": actions,
    })


def test_action_parses_lcu_camelcase() -> None:
    a = Action.model_validate({
        "id": 7, "actorCellId": 0, "championId": 122,
        "type": "ban", "completed": False, "isInProgress": True,
    })
    assert a.id == 7
    assert a.actor_cell_id == 0
    assert a.champion_id == 122
    assert a.type == "ban"
    assert a.is_in_progress is True


def test_my_pending_action_returns_matching_pending_pick() -> None:
    session = _session_with_actions([
        [{"id": 1, "actorCellId": 0, "type": "ban", "completed": True, "championId": 86}],
        [{"id": 2, "actorCellId": 0, "type": "pick", "completed": False, "championId": 0}],
    ])
    pending = session.my_pending_action("pick")
    assert pending is not None
    assert pending.id == 2
    assert pending.type == "pick"


def test_my_pending_action_returns_pending_ban() -> None:
    session = _session_with_actions([
        [{"id": 5, "actorCellId": 0, "type": "ban", "completed": False, "championId": 0}],
    ])
    pending = session.my_pending_action("ban")
    assert pending is not None
    assert pending.id == 5


def test_my_pending_action_skips_completed() -> None:
    session = _session_with_actions([
        [{"id": 1, "actorCellId": 0, "type": "pick", "completed": True, "championId": 86}],
    ])
    assert session.my_pending_action("pick") is None


def test_my_pending_action_skips_other_actors() -> None:
    """An ally's pending pick must NOT be returned for the local player."""
    session = _session_with_actions([
        [{"id": 1, "actorCellId": 1, "type": "pick", "completed": False, "championId": 0}],
    ], cell_id=0)
    assert session.my_pending_action("pick") is None


def test_my_pending_action_returns_none_when_no_local_player() -> None:
    session = ChampSelectSession.model_validate({
        "phase": "BAN_PICK", "localPlayerCellId": -1, "actions": [],
    })
    assert session.my_pending_action("pick") is None


def test_session_drops_unknown_action_fields() -> None:
    """LCU payload sends extra fields per action (pickTurn, type metadata).
    extra='ignore' must swallow them silently."""
    session = ChampSelectSession.model_validate({
        "phase": "BAN_PICK", "localPlayerCellId": 0,
        "actions": [[{
            "id": 1, "actorCellId": 0, "type": "pick", "completed": False,
            "championId": 0, "pickTurn": 1, "isAllyAction": True,
        }]],
    })
    assert len(session.actions) == 1
    assert session.actions[0][0].id == 1


# ---------------------------------------------------------------------------
# display_subphase — UI state machine input
# ---------------------------------------------------------------------------

def _phase_session(phase: str, actions: list[list[dict]] | None = None) -> ChampSelectSession:
    return ChampSelectSession.model_validate({
        "phase": phase, "localPlayerCellId": 0,
        "myTeam": [{"cellId": 0}], "theirTeam": [],
        "actions": actions or [],
    })


def test_display_subphase_idle_when_no_phase() -> None:
    assert _phase_session("").display_subphase() == "idle"


def test_display_subphase_planning() -> None:
    assert _phase_session("PLANNING").display_subphase() == "planning"


def test_display_subphase_finalization() -> None:
    assert _phase_session("FINALIZATION").display_subphase() == "finalization"


def test_display_subphase_loading_screen() -> None:
    """GAME_STARTING is the loading-screen window — UI should pivot
    to player-profile dump for both teams."""
    assert _phase_session("GAME_STARTING").display_subphase() == "loading"


def test_display_subphase_ban_when_ban_step_in_progress() -> None:
    actions = [
        [{"id": 1, "actorCellId": 0, "type": "ban",
          "isInProgress": True, "completed": False, "championId": 0}],
    ]
    assert _phase_session("BAN_PICK", actions).display_subphase() == "ban"


def test_display_subphase_pick_when_pick_step_in_progress() -> None:
    """Bans completed, pick step now active → UI should swap from
    ban-list view to enemy-counters + pick-suggestions view."""
    actions = [
        [{"id": 1, "actorCellId": 0, "type": "ban",
          "isInProgress": False, "completed": True, "championId": 86}],
        [{"id": 6, "actorCellId": 0, "type": "pick",
          "isInProgress": True, "completed": False, "championId": 0}],
    ]
    assert _phase_session("BAN_PICK", actions).display_subphase() == "pick"


def test_display_subphase_picks_when_no_in_progress_falls_back_to_latest() -> None:
    """Between-turn gap (no isInProgress) — fall back to the latest
    action step's type so the UI doesn't flicker mid-phase."""
    actions = [
        [{"id": 1, "actorCellId": 0, "type": "ban",
          "isInProgress": False, "completed": True, "championId": 86}],
        [{"id": 6, "actorCellId": 1, "type": "pick",
          "isInProgress": False, "completed": True, "championId": 64}],
    ]
    assert _phase_session("BAN_PICK", actions).display_subphase() == "pick"


def test_display_subphase_in_game() -> None:
    assert _phase_session("IN_PROGRESS").display_subphase() == "in_game"
