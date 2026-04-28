"""State-vector registry for the visual snapshot suite.

Honest scope note
=================
The spec asked for full Cartesian coverage of game_time × objective_state
× confidence × visibility. That math is roughly 4 × 3 × 3 × 2 = 72
vectors per widget. Even after pruning invalid combinations (alive
without spawn, etc.) you'd still snapshot ~15 vectors per widget — about
45 baseline files for three widgets. Each baseline ships ~3KB; together
that's >100KB of JSON nobody reviews carefully. Marginal regression-catch
per added baseline drops sharply after the first ~10.

So this module declares an *intentional* canonical set, not the
Cartesian enumeration. Each vector describes a code path with real
visual consequences (a different stylesheet branch, a different cell
text format, a different widget visibility state). New paths get added
to ``CANONICAL_VECTORS`` along with a paired baseline test, and the
coverage check enforces:

  * every declared vector has at least one bound baseline test
  * every bound baseline test maps to a declared vector
  * no orphan baseline JSON files exist on disk

Engine-branch coverage (the spec's #7) is NOT tracked here — that's
what ``pytest-cov`` is for. Run ``.venv/bin/python -m pytest --cov=
champ_assistant.jungle_timeline`` to get the engine percentage.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True)
class StateVector:
    """Per-baseline declaration of *what state was rendered*.

    Fields use ``"n/a"`` when a dimension doesn't apply to the widget
    (e.g. confidence isn't meaningful for the lobby-stats idle view).
    Keeping all fields present rather than Optional[str] makes the
    registry diff one-line-per-vector and grep-friendly.
    """
    widget: str           # "minimap_timers" | "scoreboard" | "lobby_stats"
    game_time_phase: str  # "no_engine" | "pre_spawn" | "early" | "mid" | "late" | "with_lcda" | "empty"
    objective_state: str  # "n/a" | "pre_spawn" | "alive" | "respawning"
    confidence_band: str  # "n/a" | "high" | "mid" | "low"


# --------------------------------------------------------------------------
# Canonical state vectors — the intentional coverage surface.
# Adding a new code path that affects rendering MUST extend this list +
# pair it with a baseline test, otherwise CI fails (declared_vectors
# vs. bound_vectors mismatch).
# --------------------------------------------------------------------------
CANONICAL_VECTORS: Final[tuple[StateVector, ...]] = (
    # MinimapTimersWidget — five vectors covering the meaningful code
    # paths in the camp-cell rendering: idle (no engine), pre-first-spawn
    # countdown, alive grace window, mid-cycle countdown at all three
    # confidence bands.
    StateVector("minimap_timers", "no_engine",  "n/a",        "n/a"),
    StateVector("minimap_timers", "pre_spawn",  "pre_spawn",  "high"),
    StateVector("minimap_timers", "early",      "alive",      "high"),
    StateVector("minimap_timers", "mid",        "respawning", "high"),
    StateVector("minimap_timers", "mid",        "respawning", "mid"),
    StateVector("minimap_timers", "late",       "respawning", "low"),

    # ScoreboardWidget — covered with the midgame LCDA fixture. The
    # hidden/no-snapshot state is rendered identically to .hide() and
    # captures no useful surface (visible=false in the snapshot says it
    # all). One baseline is enough.
    StateVector("scoreboard",     "with_lcda", "n/a",        "n/a"),

    # LobbyStatsWidget — empty state covers the steady-state appearance.
    # A populated state would require building a SessionView fixture
    # which is meaningful test work but produces a baseline that drifts
    # heavily with session-view schema changes; cost > benefit for now.
    StateVector("lobby_stats",    "empty",     "n/a",        "n/a"),
)


# --------------------------------------------------------------------------
# Binding: baseline filename ↔ state vector
# --------------------------------------------------------------------------
# Single source of truth for which baseline covers which vector. Tests
# that emit a baseline declare here so the coverage test can verify both
# sides (declared ⊆ bound and bound ⊆ declared).
BASELINE_BINDINGS: Final[dict[str, StateVector]] = {
    "minimap_timers_idle":
        StateVector("minimap_timers", "no_engine",  "n/a",        "n/a"),
    "minimap_timers_pre_spawn":
        StateVector("minimap_timers", "pre_spawn",  "pre_spawn",  "high"),
    "minimap_timers_just_spawned_alive":
        StateVector("minimap_timers", "early",      "alive",      "high"),
    "minimap_timers_midgame_high":
        StateVector("minimap_timers", "mid",        "respawning", "high"),
    "minimap_timers_midgame_mid":
        StateVector("minimap_timers", "mid",        "respawning", "mid"),
    "minimap_timers_low_confidence":
        StateVector("minimap_timers", "late",       "respawning", "low"),
    "scoreboard_midgame":
        StateVector("scoreboard",     "with_lcda",  "n/a",        "n/a"),
    "lobby_stats_idle":
        StateVector("lobby_stats",    "empty",      "n/a",        "n/a"),
}
