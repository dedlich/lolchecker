"""Ban-pick suggestion engine.

Scoring strategy (lane-targeted):

  +5  S+ tier in player's lane     "S+ in YOUR TOP"
  +3  S  tier in player's lane     "S in YOUR TOP"
  +1  A  tier in player's lane     "A in YOUR TOP"
  +4  per enemy main hit            "Mained by 2 enemies"
  -inf  champion already drafted   (excluded from candidate set)

When ``my_role`` is set, tier scoring is restricted to that role —
off-lane S+ champs don't surface as bans because they don't threaten
the player's matchup. Enemy-mains bonus stays role-independent (a
champion two enemies main is worth banning regardless of lane).

Why a hard filter and not a multiplier: an earlier 1.5× boost still
let off-lane S+ (5.0) beat in-lane S (3.0×1.5=4.5). With only a
handful of S+ champions in the tier dataset, the same 3-4 names
surfaced for every role — exactly the "static bans" symptom this
engine is meant to fix.

When ``my_role`` is None (early champ-select before assignment is
visible, blind-pick queues), the engine falls back to global
multi-role aggregation as the only reasonable signal.

A configured Riot API key delivers ``enemy_profiles`` via the existing
:mod:`champ_assistant.profiling` plumbing; without it the algorithm
degrades gracefully to a tier-only ranking.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from ..data.models import ChampSelectSession
from ..data.models import Champion, CounterMatrix, Role, TierList
from ..profiling.profile import EnemyProfile

TIER_SCORES = {"S+": 5.0, "S": 3.0, "A": 1.0, "B": 0.0, "C": 0.0, "D": 0.0}
PROFILE_MAIN_BONUS = 4.0
# Bonus when a ban candidate directly counters one of our likely picks.
# Each ally pick they counter adds this bonus independently.
COUNTER_ALLY_BONUS = 3.0
# Minimum counter score (0-10 scale) to qualify — filters weak partial counters.
COUNTER_MIN_SCORE = 6.0


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
    counters: CounterMatrix | None = None,
    ally_candidate_keys: list[str] | None = None,
    limit: int = 3,
) -> list[BanSuggestion]:
    """Return up to ``limit`` ban suggestions ranked by combined score.

    ``my_role`` (the local player's assigned lane) up-weights tier
    scores for that lane so the suggestions adapt session-to-session
    instead of presenting the same global top-3 to every role.

    ``counters`` + ``ally_candidate_keys``: when both are provided, ban
    candidates that hard-counter our likely picks receive COUNTER_ALLY_BONUS
    per ally pick they counter (capped to min score COUNTER_MIN_SCORE).
    This surfaces "Malphite counters your Yasuo" bans that tier alone misses.
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

    # 1. Tier-list contribution.
    # When the player's lane is known, ban suggestions should target
    # threats *to that lane* — off-lane S+ doesn't matter to a top-
    # laner choosing a ban. Filter strictly. When my_role is None,
    # fall back to all-roles aggregation (best-effort signal).
    for role, entries in tiers.tiers.items():
        if my_role is not None and role != my_role:
            continue
        is_my_role = (my_role is not None)
        for entry in entries:
            if entry.champion in drafted:
                continue
            tier_score = TIER_SCORES.get(entry.tier, 0.0)
            if tier_score <= 0:
                continue
            scores[entry.champion] += tier_score
            label = (
                f"{entry.tier} in YOUR {role}"
                if is_my_role
                else f"{entry.tier} in {role}"
            )
            role_contributions[entry.champion].append((role, tier_score, label))

    for champ, contribs in role_contributions.items():
        ordered = sorted(contribs, key=lambda c: -c[1])
        reasons[champ].extend(label for _, _, label in ordered)

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

    # 3. Counter-to-ally contribution.
    # For each champion the player is likely to pick, find who counters them
    # and up-weight banning those counters. "Malphite counters Yasuo" → ban Malphite.
    if counters is not None and ally_candidate_keys:
        role_for_lookup = my_role or "MID"
        for ally_key in ally_candidate_keys:
            for ce in counters.counters_for(ally_key, role_for_lookup):
                if ce.score < COUNTER_MIN_SCORE:
                    continue
                if ce.champion in drafted:
                    continue
                scores[ce.champion] += COUNTER_ALLY_BONUS
                reasons[ce.champion].insert(
                    0, f"Countered dein {ally_key} (Score {ce.score:.1f})"
                )

    # 4. Rank and slice.
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
