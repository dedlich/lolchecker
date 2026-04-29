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
def test_my_role_filters_to_lane() -> None:
    """With my_role set, only that lane's tier entries score. Off-lane
    champs drop out of the tier-only ranking entirely."""
    session = ChampSelectSession(phase="BAN_PICK", myTeam=[], theirTeam=[])
    tiers = _tiers({
        "TOP": [("Darius", "S")],
        "MID": [("Ahri", "S")],
    })
    bans = suggest_bans(
        session=session, champions=CHAMPIONS, tiers=tiers,
        my_role="TOP", limit=5,
    )
    assert [b.champion_key for b in bans] == ["Darius"]
    assert bans[0].score == 3.0


def test_in_lane_tier_beats_off_lane_splus() -> None:
    """Lane-target rule: an S in YOUR lane beats an S+ in someone
    else's lane — off-lane is filtered out, not just discounted."""
    session = ChampSelectSession(phase="BAN_PICK", myTeam=[], theirTeam=[])
    tiers = _tiers({
        "TOP": [("Darius", "S")],     # in-lane
        "MID": [("Ahri", "S+")],      # off-lane — should not appear
    })
    bans = suggest_bans(
        session=session, champions=CHAMPIONS, tiers=tiers,
        my_role="TOP", limit=5,
    )
    assert [b.champion_key for b in bans] == ["Darius"]


def test_my_role_reasons_label_is_in_lane() -> None:
    """When the same champ appears in multiple roles, only the
    in-lane entry contributes — the off-lane row is suppressed."""
    session = ChampSelectSession(phase="BAN_PICK", myTeam=[], theirTeam=[])
    tiers = _tiers({
        "TOP": [("Darius", "S")],
        "MID": [("Darius", "A")],
    })
    bans = suggest_bans(
        session=session, champions=CHAMPIONS, tiers=tiers,
        my_role="TOP", limit=1,
    )
    assert bans[0].champion_key == "Darius"
    assert "YOUR TOP" in bans[0].reasons[0]
    assert not any("MID" in r for r in bans[0].reasons)
    assert bans[0].score == 3.0  # only in-lane S, no off-lane A


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
    """Mained-by-multiple-enemies must still surface even when the
    main is from another lane — profile signal is role-independent."""
    session = ChampSelectSession(phase="BAN_PICK", myTeam=[], theirTeam=[])
    tiers = _tiers({
        "TOP": [("Darius", "S")],     # 3.0 in-lane
        "MID": [("Yasuo", "A")],      # filtered out by my_role=TOP
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
    # Yasuo: 0 (off-lane filtered) + 4×2 = 8.0 → still ahead of Darius (3.0).
    # Profile signal works without any tier contribution.
    assert bans[0].champion_key == "Yasuo"
    assert bans[0].score == 8.0
    assert bans[1].champion_key == "Darius"


def test_role_with_no_splus_still_returns_lane_targets() -> None:
    """Reproducer for the 'static bans' bug: when my_role has no
    S+ (e.g. MID at patch X), off-lane S+ champs MUST NOT leak into
    the suggestion list. The MID player's bans should be MID's
    strongest lane threats — even if those are 'only' S/A — not
    the global top-3 from other lanes."""
    session = ChampSelectSession(phase="BAN_PICK", myTeam=[], theirTeam=[])
    tiers = _tiers({
        "TOP": [("Darius", "S+")],
        "JUNGLE": [("LeeSin", "S+")],
        "BOT": [("Caitlyn", "S+")],
        "MID": [("Ahri", "S"), ("Yasuo", "A")],
    })
    bans = suggest_bans(
        session=session, champions=CHAMPIONS, tiers=tiers,
        my_role="MID", limit=3,
    )
    keys = [b.champion_key for b in bans]
    assert keys == ["Ahri", "Yasuo"]
    assert "Darius" not in keys
    assert "LeeSin" not in keys
    assert "Caitlyn" not in keys
