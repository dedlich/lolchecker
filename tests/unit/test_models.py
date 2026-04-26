"""Tests for Pydantic v2 domain models."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from champ_assistant.data.models import (
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
