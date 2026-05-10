"""Tests for the loading-screen RosterPanel (v1.10.103).

Closes the b53fa9e feature ask — surface mains/WR/streak for all
10 players during the FINALIZATION / loading window. The panel is
hidden during BAN_PICK so the active-draft layout stays clean.
"""
from __future__ import annotations

import pytest
from PyQt6.QtWidgets import QApplication

from champ_assistant.data.models import (
    ChampSelectSession,
    TeamMember,
)
from champ_assistant.profiling.profile import (
    EnemyProfile,
    RankBadge,
    TopChampion,
)
from champ_assistant.ui.live_companion_sections.roster_panel import (
    RosterPanel,
    _RosterRow,
)
from champ_assistant.ui.view_model import SessionView


@pytest.fixture
def qt_app():
    return QApplication.instance() or QApplication([])


def _profile(
    *,
    name: str = "Smurf",
    wins: int = 73,
    losses: int = 27,
    streak: int = 3,
    top_champ_ids: tuple[int, ...] = (86, 122, 64),
) -> EnemyProfile:
    return EnemyProfile(
        summoner_name=name,
        wins=wins,
        losses=losses,
        streak=streak,
        top_champions=[
            TopChampion(champion_id=cid, points=1000, mastery_level=7)
            for cid in top_champ_ids
        ],
        rank=RankBadge(tier="DIAMOND", division="II", league_points=42),
    )


def _session(*, phase: str = "FINALIZATION") -> ChampSelectSession:
    return ChampSelectSession(
        phase=phase,
        localPlayerCellId=0,
        myTeam=[
            TeamMember(cellId=0, championId=86),
            TeamMember(cellId=1, championId=64),
            TeamMember(cellId=2, championId=122),
            TeamMember(cellId=3, championId=412),
            TeamMember(cellId=4, championId=222),
        ],
        theirTeam=[
            TeamMember(cellId=5, championId=157),
            TeamMember(cellId=6, championId=103),
            TeamMember(cellId=7, championId=145),
            TeamMember(cellId=8, championId=89),
            TeamMember(cellId=9, championId=53),
        ],
    )


# ---------- _RosterRow individual rendering ---------------------------

def test_row_populate_shows_summoner_name_and_wr(qt_app) -> None:
    row = _RosterRow()
    member = TeamMember(cellId=1, championId=86)
    profile = _profile(name="ProfileName", wins=73, losses=27, streak=3)

    row.populate(
        member=member,
        champion_key="Garen",
        profile=profile,
        icon_lookup=lambda key: None,
        champion_keys={86: "Garen", 122: "Darius", 64: "Lee Sin"},
    )

    # Profile name takes precedence over LCU summoner_name.
    assert row._name.text() == "ProfileName"
    assert "73% WR" in row._stats.text()
    assert "W3" in row._stats.text()


def test_row_loss_streak_renders_with_l_prefix(qt_app) -> None:
    row = _RosterRow()
    profile = _profile(streak=-4)
    row.populate(
        member=TeamMember(cellId=1, championId=86),
        champion_key="Garen",
        profile=profile,
        icon_lookup=lambda key: None,
        champion_keys={86: "Garen"},
    )
    assert "L4" in row._stats.text()


def test_row_no_profile_renders_empty_stats(qt_app) -> None:
    """Pre-fetch state — row exists but stats line is blank. Name
    shows ``Loading…`` as a neutral placeholder; we deliberately do
    NOT fall back to the champion key here because that visually
    duplicates the locked champion's name on the row (v1.10.143 fix
    for the "champ name and player name overlap" report)."""
    row = _RosterRow()
    row.populate(
        member=TeamMember(cellId=1, championId=86),
        champion_key="Garen",
        profile=None,
        icon_lookup=lambda key: None,
        champion_keys={86: "Garen"},
    )
    assert row._stats.text() == ""
    assert row._name.text() == "Loading…"


def test_row_mains_text_fallback_when_icons_missing(qt_app) -> None:
    """Icon prefetch hasn't caught a champion → text fallback so the
    user still sees the mains data."""
    row = _RosterRow()
    profile = _profile(top_champ_ids=(86, 122, 64))
    row.populate(
        member=TeamMember(cellId=1, championId=86),
        champion_key="Garen",
        profile=profile,
        icon_lookup=lambda key: None,  # no icons available
        champion_keys={86: "Garen", 122: "Darius", 64: "Lee Sin"},
    )
    text = row._mains_text.text()
    assert "Garen" in text
    assert "Darius" in text
    assert "Lee Sin" in text


def test_row_clear_resets_state(qt_app) -> None:
    row = _RosterRow()
    profile = _profile()
    row.populate(
        member=TeamMember(cellId=1, championId=86),
        champion_key="Garen",
        profile=profile,
        icon_lookup=lambda key: None,
        champion_keys={86: "Garen"},
    )
    row.clear()
    assert row._name.text() == "—"
    assert row._stats.text() == ""


# ---------- RosterPanel team-level rendering --------------------------

def test_panel_constructs_5_ally_5_enemy_rows(qt_app) -> None:
    panel = RosterPanel()
    assert len(panel._ally_rows) == 5
    assert len(panel._enemy_rows) == 5


def test_panel_update_with_session_populates_both_teams(qt_app) -> None:
    """End-to-end: SessionView with 10 picks + ally/enemy profile dicts
    → both teams' rows rendered."""
    panel = RosterPanel()
    session = _session()
    view = SessionView(
        session=session,
        all_champion_keys={
            86: "Garen", 64: "Lee Sin", 122: "Darius",
            412: "Thresh", 222: "Jinx",
            157: "Yasuo", 103: "Ahri", 145: "Kaisa",
            89: "Leona", 53: "Blitzcrank",
        },
        all_champion_names={
            86: "Garen", 64: "Lee Sin", 122: "Darius",
            412: "Thresh", 222: "Jinx",
            157: "Yasuo", 103: "Ahri", 145: "Kaisa",
            89: "Leona", 53: "Blitzcrank",
        },
        ally_profiles={
            1: _profile(name="AllyAProfile"),
            2: _profile(name="AllyBProfile"),
        },
        enemy_profiles={
            5: _profile(name="EnemyAProfile"),
            6: _profile(name="EnemyBProfile"),
        },
    )
    panel.update_panel(view, icon_lookup=lambda key: None)

    # Ally row index 1 picks up the cell_id=1 profile name.
    assert panel._ally_rows[1]._name.text() == "AllyAProfile"
    assert panel._ally_rows[2]._name.text() == "AllyBProfile"
    # Enemy row index 0 maps to cell_id=5.
    assert panel._enemy_rows[0]._name.text() == "EnemyAProfile"
    assert panel._enemy_rows[1]._name.text() == "EnemyBProfile"


def test_panel_update_no_session_clears_all_rows(qt_app) -> None:
    panel = RosterPanel()
    panel.update_panel(SessionView(), icon_lookup=lambda key: None)
    for row in panel._ally_rows + panel._enemy_rows:
        assert row._name.text() == "—"
