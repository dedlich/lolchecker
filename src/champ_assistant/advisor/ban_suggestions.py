"""Ban-pick suggestion engine.

Scoring strategy (intentionally simple, easy to reason about):

  +5  S+ tier in any role           "S+ MID"
  +3  S  tier in any role           "S TOP"
  +1  A  tier in any role           "A JUNGLE"
  +4  per enemy main hit            "Mained by 2 enemies"
  -inf  champion already drafted   (excluded from candidate set)

The result is the top-N champions ranked by total score, each with up to
three short reason strings the UI shows underneath the champion name.

A configured Riot API key delivers ``enemy_profiles`` via the existing
:mod:`champ_assistant.profiling` plumbing; without it the algorithm
degrades gracefully to a tier-only ranking.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from ..data.models import ChampSelectSession
from ..data.models import Champion, TierList
from ..profiling.profile import EnemyProfile

TIER_SCORES = {"S+": 5.0, "S": 3.0, "A": 1.0, "B": 0.0, "C": 0.0, "D": 0.0}
PROFILE_MAIN_BONUS = 4.0


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
    limit: int = 3,
) -> list[BanSuggestion]:
    """Return up to ``limit`` ban suggestions ranked by combined score."""
    profiles = enemy_profiles or {}

    # Build the drafted set so we never recommend banning something that's
    # already locked in (including own-team picks the user shouldn't ban).
    drafted: set[str] = set()
    for member in (*session.my_team, *session.their_team):
        if member.champion_id and member.champion_id in champions:
            drafted.add(champions[member.champion_id].key)

    scores: dict[str, float] = defaultdict(float)
    reasons: dict[str, list[str]] = defaultdict(list)

    # 1. Tier-list contribution per role.
    for role, entries in tiers.tiers.items():
        for entry in entries:
            if entry.champion in drafted:
                continue
            tier_score = TIER_SCORES.get(entry.tier, 0.0)
            if tier_score <= 0:
                continue
            scores[entry.champion] += tier_score
            reasons[entry.champion].append(f"{entry.tier} in {role}")

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
