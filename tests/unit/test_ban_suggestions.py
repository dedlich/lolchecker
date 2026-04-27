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
