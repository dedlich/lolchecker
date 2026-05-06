"""Declarative suppression table for the decision engine.

The previous imperative implementation in ``_evaluate._suppress_dominated``
was a 200-line wall of ``if "x" in kinds:`` blocks. With 60+ rules in
``ALL_RULES`` every new rule had to hunt through that wall to know which
existing umbrella signals should silence it. This module replaces the
wall with a single ``SUPPRESSION_TABLE`` of ``SuppressionRule`` entries
plus two unavoidable structural special cases.

Why a table beats imperative blocks
------------------------------------
* **Auditable** — one read finds every "who suppresses what". Tests can
  assert table coverage.
* **Symmetric** — adding a new rule means adding an entry, not touching
  a hand-crafted control flow.
* **Documented inline** — each entry carries a ``description`` field
  explaining why the suppression exists.

What stays imperative
---------------------
Two patterns don't fit cleanly into a (trigger, suppresses) tuple:

1. **``game_end`` is a whitelist, not a blacklist** — when present, drop
   everything *except* the game-end card. The table model is "drop these
   kinds"; this is "keep only this kind". Hardcoded as a pre-check.

2. **Cross-objective priority (Rule 9)** — when both
   ``{baron_take, baron_free}`` AND ``{dragon_take, dragon_free}`` fire
   simultaneously, baron wins. This is an AND of two OR groups, not a
   single trigger condition. Hardcoded as a post-pass.

Everything else lives in the table.
"""
from __future__ import annotations

from dataclasses import dataclass

from ._core import Recommendation


@dataclass(frozen=True)
class SuppressionRule:
    """One entry in the suppression table.

    Semantics: when ANY rec in the rec set has its ``kind`` in
    ``triggers`` (and optionally ``severity == requires_severity``),
    every rec whose ``kind`` is in ``suppresses`` is dropped.
    """
    triggers: frozenset[str]
    """Set of rec kinds that fire this suppression. Single-element set
    is the common case ({\"ace\"}, {\"ally_inhib_down\"}). Multi-element
    sets model OR-triggers (any of these kinds present → fire)."""

    suppresses: frozenset[str]
    """Set of rec kinds to drop when the suppression fires."""

    description: str
    """One-line rationale. Surfaced in tooling that prints the table."""

    requires_severity: str | None = None
    """When set, the trigger only fires if at least one matching rec has
    this severity. Used by spiral-tilt suppression: ``tilt`` recs at
    ``info``/``warn`` don't suppress, only ``severity=="alert"`` does."""

    terminal: bool = False
    """When True, applying this rule ends the suppression pass. Used for
    ``numbers_disadv`` — once we're short-handed, no further offensive
    suppression matters; numbers_disadv has already swept them."""


# ---------------------------------------------------------------------------
# THE TABLE
# ---------------------------------------------------------------------------
# Order matches the legacy ``_suppress_dominated`` so any ordering-
# dependent semantics are preserved. Rules 0 (game_end whitelist) and 9
# (cross-objective priority) are NOT in the table — they live as
# imperative special cases in ``apply_suppression`` below.

SUPPRESSION_TABLE: tuple[SuppressionRule, ...] = (
    # Rule 1 — ace absorbs offensive signals.
    SuppressionRule(
        triggers=frozenset({"ace"}),
        suppresses=frozenset({
            "fight", "fight_bad", "numbers_adv", "gold_lead", "kill_lead",
            "jungler_down", "window_closing",
            "gank_risk", "tilt", "recall_resource", "recall_gold",
            "mana_check", "lane_mia", "objective_setup",
            "skill_point_unspent", "active_bounty", "enemy_bounty",
            "ally_bounty", "matchup_mismatch", "plate_window",
            "first_blood", "teamfight_won", "teamfight_won_big",
            "shutdown_taken", "objective_taken_baron",
            "objective_taken_elder", "objective_taken_soul",
            "objective_taken_drake", "objective_taken_herald",
            "objective_bounty_behind", "objective_bounty_ahead",
        }),
        description="ACE moment dominates — absorbs every offensive / "
                    "coaching signal in the rec set",
    ),
    # Rule 2 — short-handed: drop offensive recs and short-circuit.
    SuppressionRule(
        triggers=frozenset({"numbers_disadv"}),
        suppresses=frozenset({
            "fight", "numbers_adv", "gold_lead", "kill_lead",
            "dragon_take", "dragon_free", "baron_take", "baron_free",
            "elder_take", "flash_down", "tp_down", "combat_spell_down",
            "baron_buff_expiring", "elder_buff_expiring", "ally_herald",
            "inhib_expiring", "dragon_soul", "ally_hornguard",
            "void_grub_contest", "jungler_down", "enemy_soul_point",
            "power_spike", "cs_deficit", "lane_level_adv", "lane_mia",
            "objective_setup", "skill_point_unspent", "active_bounty",
            "enemy_bounty", "ally_bounty", "matchup_mismatch",
            "plate_window", "first_blood", "teamfight_won",
            "teamfight_won_big", "shutdown_taken",
            # NOTE: teamfight_lost / teamfight_lost_big SURVIVE — they
            # explain WHY we're short-handed and the "no engage" message
            # is valuable in this state.
        }),
        description="Short-handed (teammate dead) — drop all offensive recs. "
                    "Terminal: short-circuits later suppression rules since "
                    "the offensive side has already been swept.",
        terminal=True,
    ),
    # Rule 3 — free-window objectives embed numbers_adv.
    SuppressionRule(
        triggers=frozenset({"dragon_free", "baron_free", "elder_take"}),
        suppresses=frozenset({"numbers_adv"}),
        description="Free-window objective recs already encode the "
                    "numbers-advantage signal in richer context",
    ),
    # Rule 4 — fight subsumes generic lead signals.
    SuppressionRule(
        triggers=frozenset({"fight"}),
        suppresses=frozenset({"gold_lead", "kill_lead"}),
        description="A fight rec already says 'press the lead' — "
                    "generic lead signals are redundant alongside it",
    ),
    # Rule 5 — objective-take call contradicts 'don't fight'.
    SuppressionRule(
        triggers=frozenset({
            "dragon_take", "dragon_free",
            "baron_take", "baron_free",
            "elder_take",
        }),
        suppresses=frozenset({"fight_bad"}),
        description="Active objective-take recommendation contradicts "
                    "'don't fight' — drop the fight_bad signal",
    ),
    # Rule 6 — base_exposed absorbs lane_open.
    SuppressionRule(
        triggers=frozenset({"base_exposed"}),
        suppresses=frozenset({"lane_open"}),
        description="base_exposed is more specific than generic lane_open "
                    "in the same context",
    ),
    # Rule 7 — inhib_down supersedes earlier-stage building alerts.
    SuppressionRule(
        triggers=frozenset({"inhib_down"}),
        suppresses=frozenset({"base_exposed", "lane_open"}),
        description="State has advanced past base_exposed — the inhib is "
                    "down, no longer just exposed",
    ),
    # Rule 8 — defending base trumps mid-map and coaching calls.
    SuppressionRule(
        triggers=frozenset({"ally_inhib_down"}),
        suppresses=frozenset({
            "dragon_take", "baron_take", "elder_take",
            "lane_open", "inhib_expiring", "ally_turret_lost",
            "dragon_soul", "ally_hornguard", "void_grub_contest",
            "jungler_down", "enemy_soul_point", "cs_deficit",
            "lane_level_adv", "gank_risk", "lane_mia",
            "objective_setup", "skill_point_unspent",
            "active_bounty", "enemy_bounty", "ally_bounty",
            "matchup_mismatch", "plate_window",
            "teamfight_won", "teamfight_won_big",
            "teamfight_lost", "teamfight_lost_big",
            "shutdown_taken", "objective_taken_baron",
            "objective_taken_elder", "objective_taken_soul",
            "objective_taken_drake", "objective_taken_herald",
            "objective_bounty_behind", "objective_bounty_ahead",
            # NOTE: ally_inhib_respawning intentionally coexists with
            # ally_inhib_down — "defend now + back in 30s" are
            # complementary, not conflicting.
        }),
        description="Defending base trumps every mid-map objective + "
                    "coaching call. Survives: ally_inhib_respawning (30s "
                    "countdown is complementary), teamfight_lost (explains "
                    "why we're defending).",
    ),
    # Rule 10 — enemy execute window: fighting is fatal.
    SuppressionRule(
        triggers=frozenset({"enemy_elder_buff"}),
        suppresses=frozenset({
            "fight", "numbers_adv", "gold_lead", "kill_lead",
            "dragon_take", "dragon_free", "baron_take", "baron_free",
            "elder_take", "baron_buff_expiring", "elder_buff_expiring",
            "dragon_soul", "jungler_down", "objective_setup",
            "enemy_bounty", "ally_bounty",
        }),
        description="Enemy Elder execute is active — fighting through it "
                    "is nearly always fatal regardless of other signals",
    ),
    # Rule 10b — deep deficit: scaling beats forcing comeback objectives.
    SuppressionRule(
        triggers=frozenset({"far_behind_safe"}),
        suppresses=frozenset({"objective_bounty_behind"}),
        description="Deep deficit — the bounty math is real but acting on "
                    "it gets you killed and feeds them MORE bounty. Safe-"
                    "play wins.",
    ),
    # Rule 11 — spiral tilt: feeding player should not be told to fight.
    SuppressionRule(
        triggers=frozenset({"tilt"}),
        suppresses=frozenset({
            "fight", "numbers_adv", "gold_lead", "kill_lead",
            "power_spike", "lane_level_adv", "lane_mia",
            "objective_setup", "skill_point_unspent",
            "enemy_bounty", "ally_bounty", "matchup_mismatch",
            "plate_window", "teamfight_won", "teamfight_won_big",
            "shutdown_taken", "objective_taken_baron",
            "objective_taken_elder", "objective_taken_soul",
            "objective_taken_drake", "objective_taken_herald",
            "objective_bounty_behind", "objective_bounty_ahead",
            "flash_down", "tp_down", "combat_spell_down",
            "jungler_down", "dragon_soul",
        }),
        description="Spiral tilt (3+ deaths) — telling a feeding player "
                    "to fight is the worst possible combo. Severity-gated: "
                    "only ``tilt`` recs at severity='alert' qualify.",
        requires_severity="alert",
    ),
)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def apply_suppression(recs: list[Recommendation]) -> list[Recommendation]:
    """Apply the declarative table + the two structural special cases.

    Order:
      1. Pre-check: ``game_end`` whitelist (drop everything else)
      2. Walk SUPPRESSION_TABLE in order, applying each rule that triggers
         (with optional severity filter); ``terminal=True`` short-circuits
      3. Post-pass: cross-objective priority (baron beats dragon when both
         take-kinds present)
    """
    kinds = {r.kind for r in recs}

    # Pre-check — game_end is a whitelist (only show the result card).
    if "game_end" in kinds:
        return [r for r in recs if r.kind == "game_end"]

    # Table-driven rules.
    for rule in SUPPRESSION_TABLE:
        # Trigger check: any rec whose kind is in triggers (and, when
        # requires_severity is set, whose severity matches).
        triggered = False
        for r in recs:
            if r.kind not in rule.triggers:
                continue
            if rule.requires_severity is not None and r.severity != rule.requires_severity:
                continue
            triggered = True
            break
        if not triggered:
            continue
        recs = [r for r in recs if r.kind not in rule.suppresses]
        if rule.terminal:
            return recs
        kinds = {r.kind for r in recs}

    # Post-pass — cross-objective priority. AND of two OR groups doesn't
    # fit the trigger model cleanly; keep it imperative.
    present_baron = kinds & {"baron_take", "baron_free"}
    present_dragon = kinds & {"dragon_take", "dragon_free"}
    if present_baron and present_dragon:
        # Baron beats dragon — drop the dragon objective cards.
        recs = [r for r in recs if r.kind not in present_dragon]

    return recs
