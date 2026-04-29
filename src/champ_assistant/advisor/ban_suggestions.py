"""Ban-pick suggestion engine.

Scoring strategy (lane-aware):

  +5  S+ tier in any role           "S+ MID"
  +3  S  tier in any role           "S TOP"
  +1  A  tier in any role           "A JUNGLE"
  +4  per enemy main hit            "Mained by 2 enemies"
  -inf  champion already drafted   (excluded from candidate set)

  ×1.5  multiplier on the tier-score whenever ``my_role`` matches the
        tier-entry's role. Bans relevant to the player's own lane
        float to the top vs bans that only hurt other lanes.

The previous algorithm was lane-agnostic — an S+ MID and an S+ TOP
scored identically regardless of the player's actual role. The result
felt static across sessions because a top-laner saw the same global
top-3 as a mid-laner.

A configured Riot API key delivers ``enemy_profiles`` via the existing
:mod:`champ_assistant.profiling` plumbing; without it the algorithm
degrades gracefully to a tier-only ranking. ``my_role=None`` falls
back to the original lane-agnostic behavior — back-compat.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from ..data.models import ChampSelectSession
from ..data.models import Champion, Role, TierList
from ..profiling.profile import EnemyProfile

TIER_SCORES = {"S+": 5.0, "S": 3.0, "A": 1.0, "B": 0.0, "C": 0.0, "D": 0.0}
PROFILE_MAIN_BONUS = 4.0
# Multiplier applied to tier-scores when the entry's role matches
# the local player's lane. 1.5 was chosen to be meaningful (an S
# in-lane outranks an S+ off-lane: 3.0×1.5=4.5 > 5.0×1.0... wait,
# actually that's still 5 > 4.5 — so an S+ off-lane still wins
# vs an S in-lane). Concretely: it boosts ties + close ranks toward
# my lane, doesn't completely override raw tier strength.
MY_ROLE_TIER_MULTIPLIER = 1.5


@dataclass(frozen=True)
class BanSuggestion:
    champion_key: str
    score: float
    reasons: list[str]


def suggest_bans(
    *,
    session: ChampSelectSession,
    champions: dict[int, Champion],
    tiers: TierList,
    enemy_profiles: dict[int, EnemyProfile] | None = None,
    my_role: Role | None = None,
    limit: int = 3,
) -> list[BanSuggestion]:
    """Return up to ``limit`` ban suggestions ranked by combined score.

    ``my_role`` (the local player's assigned lane) up-weights tier
    scores for that lane so the suggestions adapt session-to-session
    instead of presenting the same global top-3 to every role.
    """
    profiles = enemy_profiles or {}

    # Build the drafted set so we never recommend banning something that's
    # already locked in (including own-team picks the user shouldn't ban).
    drafted: set[str] = set()
    for member in (*session.my_team, *session.their_team):
        if member.champion_id and member.champion_id in champions:
            drafted.add(champions[member.champion_id].key)

    scores: dict[str, float] = defaultdict(float)
    reasons: dict[str, list[str]] = defaultdict(list)
    # Per-champion: list of (role, score_after_multiplier, label) so we
    # can sort reasons by relevance later.
    role_contributions: dict[str, list[tuple[str, float, str]]] = defaultdict(list)

    # 1. Tier-list contribution per role — with my_role boost.
    for role, entries in tiers.tiers.items():
        is_my_role = (my_role is not None and role == my_role)
        multiplier = MY_ROLE_TIER_MULTIPLIER if is_my_role else 1.0
        for entry in entries:
            if entry.champion in drafted:
                continue
            tier_score = TIER_SCORES.get(entry.tier, 0.0)
            if tier_score <= 0:
                continue
            adjusted = tier_score * multiplier
            scores[entry.champion] += adjusted
            label = (
                f"{entry.tier} in YOUR {role}"
                if is_my_role
                else f"{entry.tier} in {role}"
            )
            role_contributions[entry.champion].append((role, adjusted, label))

    # Reasons: emit my_role contributions FIRST (so the user sees the
    # lane-relevant reason at the top of the row), then everything
    # else by descending contribution.
    for champ, contribs in role_contributions.items():
        my_role_first = sorted(
            contribs,
            key=lambda c: (c[0] != my_role, -c[1]),
        )
        reasons[champ].extend(label for _, _, label in my_role_first)

    # 2. Enemy-mains contribution.
    main_counts: dict[str, int] = defaultdict(int)
    for profile in profiles.values():
        for top in profile.top_champions:
            champ = champions.get(top.champion_id)
            if champ is None:
                continue
            if champ.key in drafted:
                continue
            main_counts[champ.key] += 1

    for champ_key, count in main_counts.items():
        scores[champ_key] += PROFILE_MAIN_BONUS * count
        word = "enemy" if count == 1 else "enemies"
        reasons[champ_key].insert(0, f"Mained by {count} {word}")

    # 3. Rank and slice.
    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    out: list[BanSuggestion] = []
    for champ_key, score in ranked:
        if score <= 0:
            break
        out.append(BanSuggestion(
            champion_key=champ_key,
            score=round(score, 1),
            reasons=reasons[champ_key][:3],
        ))
        if len(out) >= limit:
            break
    return out
