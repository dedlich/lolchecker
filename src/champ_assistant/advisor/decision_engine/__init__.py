"""Decision engine — turns raw LCDA state into actionable recommendations.

Strategy B1 — first foundation of the smartest pillar. Pure functions
over an LCDA snapshot; no Qt, no I/O, no asyncio. Each rule encodes
one heuristic the assistant would tell a teammate at that game state.

Layout
======
The engine grew past 5000 lines as a single module; this package
splits the implementation across four private files while preserving
the public API. External imports
``from champ_assistant.advisor.decision_engine import X`` keep working
because every public name and the private helpers / hysteresis-reset
functions tests exercise are re-exported here.

* ``_core``     — Recommendation dataclass, threshold constants,
                  champion data tables, all shared helper functions.
* ``_state``    — Process-wide hysteresis singletons + reset_*
                  helpers (one per stateful rule).
* ``_rules``    — All ``rule_*`` functions + the ``ALL_RULES`` tuple +
                  ``rule_situational_build`` (called separately).
* ``_evaluate`` — ``_SEVERITY_RANK``, ``_suppress_dominated``,
                  ``evaluate``.

Adding a new rule
-----------------
Define the function in ``_rules.py`` (or split it out by category if a
new module makes sense). Register it in ``ALL_RULES``. If it carries
state across ticks, add a ``_FooHysteresis`` class to ``_state.py``
plus a matching ``reset_foo_hysteresis`` helper for tests. Update
``_suppress_dominated`` if the new rec ``kind`` should drop or be
dropped under existing umbrella signals.

What this is NOT
----------------
* No enemy-position detection (Vanguard-incompatible).
* No matchup-specific advice (would need curated dataset).
* No teamfight-readiness model — that requires ult availability data
  we don't have.
* Not an oracle — heuristics are approximations, the user remains in
  control. Every recommendation is a hint, never a command.
"""
from __future__ import annotations

# Public surface — the wildcard imports pull in every non-underscore
# name from each submodule. The order matters only because _evaluate
# imports from _rules which imports from _core / _state.
from ._core import *  # noqa: F401,F403
from ._state import *  # noqa: F401,F403
from ._rules import *  # noqa: F401,F403
from ._evaluate import *  # noqa: F401,F403

# Underscore-prefixed names that tests + a few external consumers use
# directly. Wildcard imports skip names starting with `_`, so we
# re-export them explicitly here. Keep this list tight — adding a name
# here is a deliberate choice to expose internal API.
from ._core import (
    _active_ally_inhibitors_down,
    _active_enemy_inhibitors_down,
    _ally_baron_buff_remaining,
    _ally_elder_buff_remaining,
    _alive_count,
    _avg_level_diff,
    _drake_stack_count,
    _earliest_ally_inhib_respawn_remaining,
    _earliest_enemy_inhib_respawn_remaining,
    _enemy_baron_buff_remaining,
    _enemy_drake_stack_count,
    _enemy_elder_buff_remaining,
    _enemy_grub_count,
    _enemy_herald_pickup,
    _enemy_turrets_down,
    _herald_pickup,
    _fed_score,
    _focus_target,
    _is_jungler,
    _kill_streak,
    _objective_remaining,
    _parse_turret_name,
    _player_ids,
    _recent_ally_turret_losses,
    _team_gold_diff,
    _team_kill_diff,
)
from ._rules import (
    _active_player,
    _matchup_deficit,
    _team_id_set,
)
from ._evaluate import (
    _SEVERITY_RANK,
    _suppress_dominated,
)
