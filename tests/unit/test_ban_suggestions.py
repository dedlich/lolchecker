"""Tests for the ban-suggestion engine."""
from __future__ import annotations

from champ_assistant.advisor.ban_suggestions import suggest_bans
from champ_assistant.data.models import (
    ChampSelectSession,
    Champion,
    TeamMember,
    TierEntry,
    TierList,
)
from champ_assistant.profiling.profile import EnemyProfile, TopChampion


def _champ(id_: int, key: str) -> Champion:
    return Champion(id=id_, key=key, name=key, tags=[])


CHAMPIONS = {
    122: _champ(122, "Darius"),
    103: _champ(103, "Ahri"),
    64:  _champ(64,  "LeeSin"),
    51:  _champ(51,  "Caitlyn"),
    412: _champ(412, "Thresh"),
    7:   _champ(7,   "Leblanc"),
    266: _champ(266, "Aatrox"),
    157: _champ(157, "Yasuo"),
}


def _tiers(per_role: dict[str, list[tuple[str, str]]]) -> TierList:
    return TierList(
        tiers={
            role: [TierEntry(champion=k, tier=t) for k, t in entries]
            for role, entries in per_role.items()
        }
    )


def test_returns_empty_when_no_data() -> None:
    session = ChampSelectSession(phase="BAN_PICK", myTeam=[], theirTeam=[])
    tiers = TierList(tiers={})
    bans = suggest_bans(
        session=session, champions=CHAMPIONS, tiers=tiers,
    )
    assert bans == []


def test_tier_alone_picks_strongest_per_role() -> None:
    session = ChampSelectSession(phase="BAN_PICK", myTeam=[], theirTeam=[])
    tiers = _tiers({
        "TOP": [("Darius", "S+"), ("Aatrox", "A")],
        "MID": [("Ahri", "S")],
    })
    bans = suggest_bans(
        session=session, champions=CHAMPIONS, tiers=tiers, limit=3,
    )
    assert [b.champion_key for b in bans] == ["Darius", "Ahri", "Aatrox"]
    assert bans[0].score == 5.0
    assert bans[1].score == 3.0


def test_drafted_champions_are_excluded() -> None:
    session = ChampSelectSession(
        phase="BAN_PICK", localPlayerCellId=0,
        myTeam=[TeamMember(cellId=0, championId=122)],   # Darius locked on us
        theirTeam=[TeamMember(cellId=5, championId=51)],  # Cait on enemy
    )
    tiers = _tiers({
        "TOP": [("Darius", "S+"), ("Aatrox", "S")],
        "BOT": [("Caitlyn", "S+"), ("Thresh", "A")],
    })
    bans = suggest_bans(
        session=session, champions=CHAMPIONS, tiers=tiers, limit=5,
    )
    keys = {b.champion_key for b in bans}
    assert "Darius" not in keys
    assert "Caitlyn" not in keys
    assert "Aatrox" in keys


def test_enemy_mains_boost_score() -> None:
    session = ChampSelectSession(phase="BAN_PICK", myTeam=[], theirTeam=[])
    tiers = _tiers({
        "MID": [("Yasuo", "A")],
        "TOP": [("Darius", "S")],
    })
    profiles = {
        5: EnemyProfile(
            summoner_name="X",
            top_champions=[TopChampion(157, 500_000, 7)],  # Yasuo
        ),
        6: EnemyProfile(
            summoner_name="Y",
            top_champions=[TopChampion(157, 400_000, 6)],  # Yasuo too
        ),
    }
    bans = suggest_bans(
        session=session, champions=CHAMPIONS, tiers=tiers,
        enemy_profiles=profiles, limit=2,
    )
    # Yasuo: 1 (A tier) + 4*2 (mained by 2) = 9 → outranks Darius (3)
    assert bans[0].champion_key == "Yasuo"
    assert "Mained by 2 enemies" in bans[0].reasons
    assert bans[0].score == 9.0


def test_limit_caps_results() -> None:
    session = ChampSelectSession(phase="BAN_PICK", myTeam=[], theirTeam=[])
    tiers = _tiers({
        "TOP": [(c.key, "S") for c in CHAMPIONS.values()],
    })
    bans = suggest_bans(
        session=session, champions=CHAMPIONS, tiers=tiers, limit=3,
    )
    assert len(bans) == 3


# ----------------------------------------------------------------------
# Lane-aware scoring (my_role boost)
# ----------------------------------------------------------------------
def test_my_role_boosts_in_lane_score() -> None:
    """Same tier in different lanes → my_role lane outscores."""
    session = ChampSelectSession(phase="BAN_PICK", myTeam=[], theirTeam=[])
    tiers = _tiers({
        "TOP": [("Darius", "S")],     # 3.0 base
        "MID": [("Ahri", "S")],       # 3.0 base
    })
    # Without my_role: order is undefined between two equal scores.
    # WITH my_role=TOP, Darius gets 3.0×1.5=4.5 → outranks Ahri at 3.0.
    bans = suggest_bans(
        session=session, champions=CHAMPIONS, tiers=tiers,
        my_role="TOP", limit=2,
    )
    assert bans[0].champion_key == "Darius"
    assert bans[0].score == 4.5
    assert bans[1].champion_key == "Ahri"
    assert bans[1].score == 3.0


def test_my_role_boost_does_not_override_strict_tier_dominance() -> None:
    """An S+ off-lane (5.0×1.0=5.0) still beats an S in-lane (3.0×1.5=4.5).
    The boost shifts close ranks, doesn't completely override raw tier."""
    session = ChampSelectSession(phase="BAN_PICK", myTeam=[], theirTeam=[])
    tiers = _tiers({
        "TOP": [("Darius", "S")],     # in-lane: 3.0×1.5=4.5
        "MID": [("Ahri", "S+")],      # off-lane: 5.0×1.0=5.0
    })
    bans = suggest_bans(
        session=session, champions=CHAMPIONS, tiers=tiers,
        my_role="TOP", limit=2,
    )
    assert bans[0].champion_key == "Ahri"  # S+ off-lane still wins
    assert bans[1].champion_key == "Darius"


def test_my_role_reasons_appear_first() -> None:
    """The lane-relevant reason should be the first the user reads,
    not buried after off-lane reasons."""
    session = ChampSelectSession(phase="BAN_PICK", myTeam=[], theirTeam=[])
    tiers = _tiers({
        "TOP": [("Darius", "S")],     # in-lane
        "MID": [("Darius", "A")],     # off-lane (same champ, multi-role)
    })
    bans = suggest_bans(
        session=session, champions=CHAMPIONS, tiers=tiers,
        my_role="TOP", limit=1,
    )
    assert bans[0].champion_key == "Darius"
    # First reason is the YOUR-role one, not "A in MID"
    assert "YOUR TOP" in bans[0].reasons[0]
    # Both reasons present
    assert any("MID" in r for r in bans[0].reasons)


def test_my_role_none_preserves_back_compat() -> None:
    """Calling without my_role must produce the same scoring as
    before this commit — no implicit boost when role is unknown."""
    session = ChampSelectSession(phase="BAN_PICK", myTeam=[], theirTeam=[])
    tiers = _tiers({
        "TOP": [("Darius", "S+")],
        "MID": [("Ahri", "S+")],
    })
    bans = suggest_bans(
        session=session, champions=CHAMPIONS, tiers=tiers,
        my_role=None, limit=2,
    )
    # Both at exactly 5.0 — no boost applied to either.
    assert all(b.score == 5.0 for b in bans)


def test_changing_my_role_between_calls_changes_ranking() -> None:
    """Same tier list, different player role → different top ban.
    This is the core 'dynamic for the lane' behavior — top-laner
    sees Darius, mid-laner sees Ahri."""
    session = ChampSelectSession(phase="BAN_PICK", myTeam=[], theirTeam=[])
    tiers = _tiers({
        "TOP": [("Darius", "S")],
        "MID": [("Ahri", "S")],
    })
    top_bans = suggest_bans(
        session=session, champions=CHAMPIONS, tiers=tiers,
        my_role="TOP", limit=1,
    )
    mid_bans = suggest_bans(
        session=session, champions=CHAMPIONS, tiers=tiers,
        my_role="MID", limit=1,
    )
    assert top_bans[0].champion_key == "Darius"
    assert mid_bans[0].champion_key == "Ahri"


def test_enemy_mains_still_dominate_with_my_role() -> None:
    """Mained-by-multiple-enemies must still take precedence over
    in-lane tier — the heuristics compose, my_role doesn't override
    the profile signal."""
    session = ChampSelectSession(phase="BAN_PICK", myTeam=[], theirTeam=[])
    tiers = _tiers({
        "TOP": [("Darius", "S")],     # 3.0×1.5=4.5 in-lane
        "MID": [("Yasuo", "A")],      # 1.0×1.0=1.0 off-lane
    })
    profiles = {
        5: EnemyProfile(
            summoner_name="X",
            top_champions=[TopChampion(157, 500_000, 7)],
        ),
        6: EnemyProfile(
            summoner_name="Y",
            top_champions=[TopChampion(157, 400_000, 6)],
        ),
    }
    bans = suggest_bans(
        session=session, champions=CHAMPIONS, tiers=tiers,
        enemy_profiles=profiles, my_role="TOP", limit=2,
    )
    # Yasuo: 1.0 + 4×2 = 9.0 → still ahead of Darius's 4.5
    assert bans[0].champion_key == "Yasuo"
    assert bans[1].champion_key == "Darius"
