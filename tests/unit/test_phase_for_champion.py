"""Unit tests for ``view_builder._phase_for_champion_key``.

Drives the LiveCompanion right-column "Champion Power Spikes" line
(v1.10.100). Same heuristic as the team-level distribution but for
the locked single champion: classify by static tag.
"""
from __future__ import annotations

from champ_assistant.data.models import (
    BuildLibrary,
    Champion,
    CounterMatrix,
    TagsData,
    TierList,
)
from champ_assistant.view_builder import (
    ViewBuilderDeps,
    _phase_for_champion_key,
)


def _deps(
    *,
    tags: dict[str, list[str]] | None = None,
    champions: dict[int, Champion] | None = None,
) -> ViewBuilderDeps:
    return ViewBuilderDeps(
        connection_state="disconnected",
        counters=CounterMatrix(matrix={}),
        tiers=TierList(),
        tags=TagsData(tags=tags or {}),
        champions=champions or {},
        builds=BuildLibrary(),
        runtime_counters=None,
        enemy_role_overrides={},
        enemy_profiles_by_cell={},
        ally_profiles_by_cell={},
        schedule_runtime_fetch=lambda *_: None,
    )


def test_empty_key_returns_empty() -> None:
    """Pre-lock-in state — no champion, no phase."""
    assert _phase_for_champion_key("", _deps()) == ""


def test_early_game_tag_classifies_as_early() -> None:
    deps = _deps(tags={"Darius": ["Fighter", "Early-Game"]})
    assert _phase_for_champion_key("Darius", deps) == "early"


def test_lane_bully_tag_classifies_as_early() -> None:
    """``Lane-Bully`` is the second early-phase signal — must also map
    to "early" since it's the same intent (snowball window)."""
    deps = _deps(tags={"Pantheon": ["Fighter", "Lane-Bully"]})
    assert _phase_for_champion_key("Pantheon", deps) == "early"


def test_late_game_tag_classifies_as_late() -> None:
    deps = _deps(tags={"Kassadin": ["Mage", "Late-Game"]})
    assert _phase_for_champion_key("Kassadin", deps) == "late"


def test_hyper_carry_tag_classifies_as_late() -> None:
    deps = _deps(tags={"Vayne": ["Marksman", "Hyper-Carry"]})
    assert _phase_for_champion_key("Vayne", deps) == "late"


def test_scaling_tag_classifies_as_late() -> None:
    deps = _deps(tags={"Nasus": ["Fighter", "Scaling"]})
    assert _phase_for_champion_key("Nasus", deps) == "late"


def test_no_phase_tag_classifies_as_mid() -> None:
    """Champion with only role tags falls into the mid-game default."""
    deps = _deps(tags={"Ahri": ["Mage", "Assassin"]})
    assert _phase_for_champion_key("Ahri", deps) == "mid"


def test_falls_back_to_champion_record_when_static_tags_missing() -> None:
    """When the static curated tags map doesn't have an entry, the
    champion's DataDragon-derived ``tags`` field is used. Keeps the
    panel useful for champions added in patches newer than the
    bundled tags.json."""
    deps = _deps(
        tags={},  # static map empty
        champions={86: Champion(
            id=86, key="Garen", name="Garen",
            tags=["Fighter", "Tank", "Early-Game"],
        )},
    )
    assert _phase_for_champion_key("Garen", deps) == "early"


def test_unknown_champion_returns_empty() -> None:
    """Champion not in either lookup → empty string, not a guess."""
    deps = _deps()
    assert _phase_for_champion_key("Mystery", deps) == ""
