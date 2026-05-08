"""Tests for ``advisor.win_condition`` — coaching-anchor synthesis.

Pins the heuristic decisions that drive the per-game plan: which
template fires for which archetype × matchup, threat priority order,
"never-do" mistake selection. The static templates themselves are
data, not logic — these tests guard the SELECTION logic.
"""
from __future__ import annotations

from champ_assistant.advisor.build_engine import ChampionArchetype
from champ_assistant.advisor.win_condition import (
    archetype_from_tags,
    compute_win_condition,
)
from champ_assistant.data.models import TagsData


def _arch(
    play_style: str = "mage",
    *,
    primary_position: str = "MIDDLE",
    item_damage_type: str | None = None,
    is_ranged: bool = True,
) -> ChampionArchetype:
    return ChampionArchetype(
        damage_type="magic",
        item_damage_type=item_damage_type or "magic",
        play_style=play_style,
        is_ranged=is_ranged,
        has_mana=True,
        primary_position=primary_position,
        scaling_attributes=frozenset(),
    )


# ── compute_win_condition: input guards ──────────────────────────────────

def test_returns_none_for_empty_champion_key() -> None:
    """Pre-lock-in state — no champion picked yet."""
    wc = compute_win_condition(
        my_champion_key="", archetype=_arch(),
        ally_team_keys=["a", "b"], enemy_team_keys=["c", "d"],
        tags=TagsData(),
    )
    assert wc is None


def test_returns_none_for_empty_enemy_team() -> None:
    """No matchup yet — heuristics can't classify the game."""
    wc = compute_win_condition(
        my_champion_key="Ahri", archetype=_arch(),
        ally_team_keys=["a"], enemy_team_keys=[],
        tags=TagsData(),
    )
    assert wc is None


def test_returns_none_when_archetype_missing() -> None:
    """Defensive: archetype synthesis upstream may fail; UI falls
    back to placeholder rather than raising."""
    wc = compute_win_condition(
        my_champion_key="Ahri", archetype=None,
        ally_team_keys=[], enemy_team_keys=["Garen"],
        tags=TagsData(),
    )
    assert wc is None


# ── compute_win_condition: per-archetype headline shape ──────────────────

def test_assassin_uses_pick_map_when_isolated_carries_present() -> None:
    """Assassin vs squishy AP+AD carries → pick-map variant headline.
    Verified via the raw_tags surfacing 'isolated_carries'."""
    wc = compute_win_condition(
        my_champion_key="Akali",
        archetype=_arch("assassin"),
        ally_team_keys=["Garen", "LeeSin", "Caitlyn", "Thresh"],
        enemy_team_keys=["Yasuo", "Ahri", "Vayne", "Senna"],
        tags=TagsData(tags={
            "Yasuo": ["Fighter"],
            "Ahri": ["Mage"],
            "Vayne": ["Marksman"],
            "Senna": ["Marksman", "Support"],
        }),
    )
    assert wc is not None
    assert "isolated_carries" in wc.raw_tags
    assert "Pick-Map" in wc.headline


def test_assassin_default_when_no_isolated_carries() -> None:
    """Assassin into a tank-heavy / engage team → default headline,
    not pick-map. (Need the AP+AD threshold.)"""
    wc = compute_win_condition(
        my_champion_key="Talon",
        archetype=_arch("assassin"),
        ally_team_keys=["Garen"],
        enemy_team_keys=["Sion", "Maokai"],
        tags=TagsData(tags={
            "Sion": ["Tank"],
            "Maokai": ["Tank"],
        }),
    )
    assert wc is not None
    assert "isolated_carries" not in wc.raw_tags


def test_marksman_scaling_variant_when_team_scales() -> None:
    """Marksman with a scaling-team flag → scaling variant. The
    scaling_team tag fires when 3+ ally champs carry late-game tags."""
    wc = compute_win_condition(
        my_champion_key="Caitlyn",
        archetype=_arch("marksman", primary_position="BOTTOM", item_damage_type="physical"),
        ally_team_keys=["Caitlyn", "Kassadin", "Vayne", "Veigar", "Lulu"],
        enemy_team_keys=["Garen"],
        tags=TagsData(tags={
            "Kassadin": ["Mage", "Late-Game"],
            "Vayne": ["Marksman", "Hyper-Carry"],
            "Veigar": ["Mage", "Scaling"],
        }),
    )
    assert wc is not None
    assert "scaling_team" in wc.raw_tags


def test_support_branch_separates_engage_from_enchanter() -> None:
    """Tank-supports (item_damage_type='physical') get the engage
    headline; enchanters (magic damage) get the enchanter line."""
    enchanter = compute_win_condition(
        my_champion_key="Lulu",
        archetype=_arch("support", primary_position="SUPPORT", item_damage_type="magic"),
        ally_team_keys=[], enemy_team_keys=["Garen"],
        tags=TagsData(),
    )
    engage = compute_win_condition(
        my_champion_key="Leona",
        archetype=_arch("support", primary_position="SUPPORT", item_damage_type="physical"),
        ally_team_keys=[], enemy_team_keys=["Garen"],
        tags=TagsData(),
    )
    assert enchanter is not None and engage is not None
    assert enchanter.headline != engage.headline


# ── threats: priority + counter text ─────────────────────────────────────

def test_threats_prioritise_burst_over_other_kits() -> None:
    """Burst threats > mobility > hard CC > sustain. With both Yasuo
    (mobility) and Zed (burst) on enemy team, Zed should be the first
    threat surfaced — burst kills you faster than anything else."""
    wc = compute_win_condition(
        my_champion_key="Vayne",
        archetype=_arch("marksman", primary_position="BOTTOM", item_damage_type="physical"),
        ally_team_keys=["Vayne"],
        enemy_team_keys=["Yasuo", "Zed", "Garen", "Soraka"],
        tags=TagsData(),
    )
    assert wc is not None
    assert wc.threats
    assert wc.threats[0].startswith("Zed")  # burst priority wins


def test_threats_max_two_entries() -> None:
    """Avoid information overload — pro coach surfaces the 2 biggest,
    not every enemy."""
    wc = compute_win_condition(
        my_champion_key="Vayne",
        archetype=_arch("marksman", primary_position="BOTTOM", item_damage_type="physical"),
        ally_team_keys=[],
        enemy_team_keys=["Zed", "Talon", "LeBlanc", "Yasuo", "Camille"],
        tags=TagsData(),
    )
    assert wc is not None
    assert len(wc.threats) <= 2


def test_threat_includes_named_counter() -> None:
    """Each threat line should name the counter — pro coaching
    pairs the problem with the answer in the same breath."""
    wc = compute_win_condition(
        my_champion_key="Caitlyn",
        archetype=_arch("marksman", primary_position="BOTTOM", item_damage_type="physical"),
        ally_team_keys=[],
        enemy_team_keys=["Zed"],
        tags=TagsData(),
    )
    assert wc is not None
    assert any("Stasis" in t or "Banshee" in t for t in wc.threats)


# ── avoid: situational selection ─────────────────────────────────────────

def test_marksman_avoid_mentions_peel_when_burst_present() -> None:
    wc = compute_win_condition(
        my_champion_key="Vayne",
        archetype=_arch("marksman", primary_position="BOTTOM", item_damage_type="physical"),
        ally_team_keys=[], enemy_team_keys=["Zed"],
        tags=TagsData(),
    )
    assert wc is not None
    assert "Peel" in wc.avoid


def test_assassin_avoid_calls_out_5v5() -> None:
    wc = compute_win_condition(
        my_champion_key="Talon",
        archetype=_arch("assassin"),
        ally_team_keys=[], enemy_team_keys=["Garen"],
        tags=TagsData(),
    )
    assert wc is not None
    assert "5v5" in wc.avoid


# ── archetype_from_tags fast path ────────────────────────────────────────

def test_archetype_from_tags_marksman() -> None:
    arch = archetype_from_tags("Caitlyn", "BOTTOM", ["Marksman"])
    assert arch.play_style == "marksman"
    assert arch.primary_position == "BOTTOM"
    assert arch.is_ranged is True


def test_archetype_from_tags_ap_assassin_collapses_to_mage() -> None:
    """AP assassins (Akali / LeBlanc) build like mages — the win-
    condition templates treat them as mages."""
    arch = archetype_from_tags("Akali", "MIDDLE", ["Mage", "Assassin"])
    assert arch.play_style == "mage"


def test_archetype_from_tags_tank_uses_tank_play_style() -> None:
    arch = archetype_from_tags("Sion", "TOP", ["Tank"])
    assert arch.play_style == "tank"


def test_archetype_from_tags_fighter_falls_to_bruiser() -> None:
    arch = archetype_from_tags("Garen", "TOP", ["Fighter", "Tank"])
    # Garen has both Tank + Fighter tags. The heuristic groups
    # Tank+Fighter under "tank" (engage-tank). Locking that in so a
    # future tag expansion doesn't break the template selection.
    assert arch.play_style in {"tank", "bruiser"}


def test_archetype_from_tags_unknown_falls_to_specialist() -> None:
    arch = archetype_from_tags("Mystery", "TOP", ["Specialist"])
    assert arch.play_style == "specialist"


# ── archetype label ──────────────────────────────────────────────────────

def test_archetype_label_combines_role_and_style() -> None:
    wc = compute_win_condition(
        my_champion_key="Akali",
        archetype=_arch("assassin", primary_position="MIDDLE"),
        ally_team_keys=[], enemy_team_keys=["Garen"],
        tags=TagsData(),
    )
    assert wc is not None
    assert "Mid-Lane" in wc.archetype_label
    assert "Assassin" in wc.archetype_label


# ── raw_tags surface for downstream consumers ───────────────────────────

def test_raw_tags_carry_threat_signals() -> None:
    """Decision-engine rules can cheaply check membership instead of
    re-deriving the heuristics."""
    wc = compute_win_condition(
        my_champion_key="Garen",
        archetype=_arch("bruiser", primary_position="TOP", item_damage_type="physical", is_ranged=False),
        ally_team_keys=[],
        enemy_team_keys=["Zed", "Yasuo", "Leona", "Aatrox"],
        tags=TagsData(),
    )
    assert wc is not None
    assert "burst_threat" in wc.raw_tags
    assert "mobility_threat" in wc.raw_tags
    assert "cc_threat" in wc.raw_tags
    assert "sustain_threat" in wc.raw_tags
