"""Pick suggestion scoring.

Combines tier strength, counter performance vs. enemy team, and composition
gap-fill into a single score per candidate. Scores are clamped to [0, 100]
and the result is sorted high → low.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from ..data.models import CounterMatrix, Role, TagsData, TierList
from .composition import (
    CompositionGap,
    _AD_TAGS,
    _AP_TAGS,
    _ENGAGE_TAGS,
    _FRONTLINE_TAGS,
    _PEEL_TAGS,
    _WAVE_CLEAR_TAGS,
)

# Score components (calibrated so a top-tier comp-fill counter caps near 100).
_TIER_SCORE: dict[str, float] = {"S+": 25.0, "S": 20.0, "A": 15.0, "B": 10.0, "C": 5.0, "D": 0.0}
_COUNTER_WEIGHT = 2.0          # × CounterEntry.score (range 0-10) → up to 20 per enemy
_COUNTER_CAP_PER_PICK = 30.0   # cap counter contribution overall
_GAP_FILL_BONUS: dict[str, float] = {"critical": 25.0, "important": 12.0, "nice_to_have": 5.0}

_GAP_TAGS: dict[str, set[str]] = {
    "frontline": _FRONTLINE_TAGS,
    "engage": _ENGAGE_TAGS,
    "ap_damage": _AP_TAGS,
    "ad_damage": _AD_TAGS,
    "peel": _PEEL_TAGS,
    "wave_clear": _WAVE_CLEAR_TAGS,
}


class PickSuggestion(BaseModel):
    model_config = ConfigDict(frozen=True)

    champion_key: str
    score: float = Field(ge=0.0, le=100.0)
    tier: str | None = None
    reasons: list[str] = Field(default_factory=list)


def suggest_picks(
    role: Role,
    my_team_keys: list[str],
    enemy_team_keys: list[str],
    gaps: list[CompositionGap],
    tiers: TierList,
    counters: CounterMatrix,
    tags: TagsData,
    *,
    limit: int = 5,
) -> list[PickSuggestion]:
    """Score and rank pick candidates for ``role``.

    Candidates come from the tier list for ``role``. Champions already drafted
    on either team are excluded so we never recommend an unavailable pick.
    """
    if limit < 0:
        raise ValueError(f"limit must be non-negative, got {limit}")

    drafted = {c for c in my_team_keys if c} | {c for c in enemy_team_keys if c}
    role_tiers = tiers.tiers.get(role, [])
    candidates = [t for t in role_tiers if t.champion not in drafted]

    suggestions: list[PickSuggestion] = []
    for entry in candidates:
        champ = entry.champion
        reasons: list[str] = []

        tier_score = _TIER_SCORE.get(entry.tier, 10.0)
        reasons.append(f"{entry.tier} tier in {role}")

        # Counter bonus — how strong is this champ vs the enemy team?
        counter_score = 0.0
        for enemy_key in enemy_team_keys:
            if not enemy_key:
                continue
            for ce in counters.counters_for(enemy_key, role):
                if ce.champion == champ:
                    counter_score += ce.score * _COUNTER_WEIGHT
                    reasons.append(f"counters {enemy_key} ({ce.score:.1f})")
        counter_score = min(counter_score, _COUNTER_CAP_PER_PICK)

        # Gap-fill bonus — does this champ's tag set cover any open gap?
        champ_tags = set(tags.tags_for(champ))
        gap_score = 0.0
        for gap in gaps:
            if champ_tags & _GAP_TAGS[gap.category]:
                gap_score += _GAP_FILL_BONUS[gap.severity]
                reasons.append(f"fills {gap.category} gap ({gap.severity})")

        total = max(0.0, min(100.0, tier_score + counter_score + gap_score))
        suggestions.append(
            PickSuggestion(
                champion_key=champ,
                score=total,
                tier=entry.tier,
                reasons=reasons,
            )
        )

    suggestions.sort(key=lambda s: (-s.score, s.champion_key))
    return suggestions[:limit]
