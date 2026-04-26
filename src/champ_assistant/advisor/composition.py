"""Team composition analysis: detect missing pieces in a draft.

Heuristic checks based on champion tags. The output is consumed by
``suggest_picks`` to bias scores toward picks that fill gaps.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ..data.models import TagsData

Severity = Literal["critical", "important", "nice_to_have"]
GapCategory = Literal["frontline", "engage", "ap_damage", "ad_damage", "peel", "wave_clear"]

_SEVERITY_RANK: dict[Severity, int] = {"critical": 0, "important": 1, "nice_to_have": 2}

# Tag groupings used by the rules below. Tag taxonomy is informal — these sets
# capture the typical labels we attach in tags.json.
_FRONTLINE_TAGS = {"Tank", "Bruiser", "Engage"}
_ENGAGE_TAGS = {"Engage", "Diver"}
_AP_TAGS = {"Mage", "Burst"}
_AD_TAGS = {"Marksman", "Fighter", "Assassin"}
_PEEL_TAGS = {"Enchanter", "Peel", "Crowd-Control"}
_WAVE_CLEAR_TAGS = {"Mage", "Wave-Clear", "Marksman"}


class CompositionGap(BaseModel):
    """A missing element in the current team."""

    model_config = ConfigDict(frozen=True)

    category: GapCategory
    severity: Severity
    description: str = Field(..., min_length=1)


def analyze_composition(
    team_champions: list[str],
    tags: TagsData,
) -> list[CompositionGap]:
    """Detect composition gaps based on champion tag coverage.

    Sorted by severity (critical first). Empty list = no gaps detected.
    """
    covered: set[str] = set()
    for champ in team_champions:
        covered.update(tags.tags_for(champ))

    gaps: list[CompositionGap] = []

    if not covered & _FRONTLINE_TAGS:
        gaps.append(
            CompositionGap(
                category="frontline",
                severity="critical",
                description="No tank, bruiser, or engage — your carries will get blown up.",
            )
        )

    if not covered & _ENGAGE_TAGS:
        gaps.append(
            CompositionGap(
                category="engage",
                severity="important",
                description="No engage — hard to force fights or contest objectives.",
            )
        )

    has_ap = bool(covered & _AP_TAGS)
    has_ad = bool(covered & _AD_TAGS)
    if not has_ap and has_ad:
        gaps.append(
            CompositionGap(
                category="ap_damage",
                severity="important",
                description="No magic damage — enemy will stack armor and tank you.",
            )
        )
    elif not has_ad and has_ap:
        gaps.append(
            CompositionGap(
                category="ad_damage",
                severity="important",
                description="No physical damage — enemy will stack MR and tank you.",
            )
        )

    if not covered & _PEEL_TAGS:
        gaps.append(
            CompositionGap(
                category="peel",
                severity="nice_to_have",
                description="No dedicated peel for the carries — assassins will eat them.",
            )
        )

    if not covered & _WAVE_CLEAR_TAGS:
        gaps.append(
            CompositionGap(
                category="wave_clear",
                severity="nice_to_have",
                description="No reliable wave clear — you'll get sieged off objectives.",
            )
        )

    gaps.sort(key=lambda g: _SEVERITY_RANK[g.severity])
    return gaps
