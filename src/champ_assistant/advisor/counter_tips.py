"""Per-enemy counter-play tips, derived from static champion tags.

One short hint shown as a tooltip on the enemy portraits in
``LiveCompanionView``'s SummaryRow. The intent is fast at-a-glance
coaching — "what kind of opponent is this and what's the broad
counter-play" — not a full matchup essay.

The function is pure: tags in, advice string out. Empty string when
no tags match (let the UI fall back to "no tip" rather than rendering
a generic placeholder).

Tag priority order is hand-tuned: more specific / actionable tips
trump generic role tags. ``Assassin`` beats ``Fighter``; ``Hyper-Carry``
beats ``Marksman``. The first matching rule wins so adding a more
specific category at the top is a deliberate override.
"""
from __future__ import annotations

from collections.abc import Iterable

# Tag → tip mapping, ordered by specificity. First match wins.
# Keep tips under ~80 chars — they render in a Qt tooltip with no
# wrapping by default. The user reads at a glance, not a paragraph.
_TIP_RULES: tuple[tuple[str, str], ...] = (
    ("Assassin",
     "High burst — keep distance, ward flanks, don't get caught isolated."),
    ("Hyper-Carry",
     "Outscales hard — pressure objectives early, end before late-game."),
    ("Lane-Bully",
     "Snowballs lane — play safe pre-6, scale to mid-game."),
    ("Early-Game",
     "Strong early, weak late — survive the first 2 items, then trade up."),
    ("Late-Game",
     "Outscales — deny CS / objectives early, win before her core."),
    ("Scaling",
     "Outscales — pressure early game, deny resources, force objectives."),
    ("Mage",
     "AP burst — Mercury's Treads / Hexdrinker, watch flash range."),
    ("Marksman",
     "Ranged DPS — engage from cover or flank, focus in fights."),
    ("Tank",
     "Engages first — focus carries, peel for backline, build %HP damage."),
    ("Fighter",
     "Sustained damage — kite with peel, force fights at range."),
    ("Support",
     "Sets up engages — track flash / CC cooldowns, dodge skillshots."),
    ("Diver",
     "Targets backline — buy Stopwatch / Zhonya's, position with peel."),
    ("Bruiser",
     "Mid-range trader — kite or all-in, avoid drawn-out 1v1s."),
    ("Skirmisher",
     "1v1 monster — group up, never duel solo without summs."),
)


def counter_tip_for_tags(tags: Iterable[str]) -> str:
    """First matching tip from the rule table, or empty when nothing
    matches (e.g. champion has no curated tags yet).

    Doesn't depend on champion key — purely tag-driven so newly added
    champions whose tags inherit from DataDragon still get a tip even
    before ``static/tags.json`` is updated."""
    tag_set = set(tags)
    for tag, tip in _TIP_RULES:
        if tag in tag_set:
            return tip
    return ""
