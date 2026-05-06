"""Process-wide hysteresis singletons for the decision engine.

Each class tracks per-rule "fired-this-tier" state so rules don't
re-emit the same recommendation every 2 s LCDA tick. The matching
``reset_*_hysteresis`` helpers exist for test isolation — autouse
fixtures call them before/after every test that exercises a rule
with hysteresis.
"""
from __future__ import annotations


class _RecallHysteresis:
    """Per-process armed/disarmed flags for the four recall tiers.

    Single mutable singleton (``_RECALL_HYSTERESIS``) — kept as a class
    rather than four module globals so tests can call ``reset()`` to
    isolate per-test runs. The ``rule_recall_check`` function disarms
    a tier when it fires the rec, and re-arms it once the player's
    state recovers above the rearm threshold.
    """
    __slots__ = ("critical", "resource", "gold", "mana")

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.critical = True
        self.resource = True
        self.gold = True
        self.mana = True


_RECALL_HYSTERESIS = _RecallHysteresis()


def reset_recall_hysteresis() -> None:
    """Test-only: drop all four tier flags back to armed.

    Without this, two unit tests sharing the same player state will
    mutually disarm each other (the first call disarms a tier, the
    second sees it disarmed and returns None unexpectedly).
    """
    _RECALL_HYSTERESIS.reset()


# ─── Bounty hysteresis ────────────────────────────────────────────────────────
# Active-player bounty awareness fires once per "episode" (each escalation
# tier and each fresh-life entry). Without state, the rule would re-fire
# every 2 s tick while the streak holds, drowning out everything else.

class _BountyHysteresis:
    """Track the highest bounty tier we've already announced this life,
    plus the death count we last saw, so we can detect "respawned →
    re-arm" transitions.
    """
    __slots__ = ("last_fired_tier", "last_seen_deaths")

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.last_fired_tier = 0
        self.last_seen_deaths = 0


_BOUNTY_HYSTERESIS = _BountyHysteresis()


def reset_bounty_hysteresis() -> None:
    """Test-only: drop the bounty-fired tier back to 0."""
    _BOUNTY_HYSTERESIS.reset()


# ─── Enemy bounty hysteresis ──────────────────────────────────────────────────
# Per-enemy version of the same pattern: each enemy carrier (Jinx, Yasuo,
# whoever has the streak) gets their own "highest tier already announced".
# Fired tier resets on their death (deaths counter increments).

class _EnemyBountyHysteresis:
    """Per-enemy "highest bounty tier announced this life" + death tracker."""
    __slots__ = ("last_fired_tier", "last_seen_deaths")

    def __init__(self) -> None:
        self.last_fired_tier: dict[str, int] = {}
        self.last_seen_deaths: dict[str, int] = {}

    def reset(self) -> None:
        self.last_fired_tier.clear()
        self.last_seen_deaths.clear()


_ENEMY_BOUNTY_HYSTERESIS = _EnemyBountyHysteresis()


def reset_enemy_bounty_hysteresis() -> None:
    """Test-only: drop all per-enemy fired-tier state."""
    _ENEMY_BOUNTY_HYSTERESIS.reset()


# ─── Ally bounty hysteresis ──────────────────────────────────────────────────
# Same shape as the enemy version, but applied to allies (excluding the
# active player — they get rule_active_bounty instead).

class _AllyBountyHysteresis:
    """Per-ally fired-tier + death-count tracker."""
    __slots__ = ("last_fired_tier", "last_seen_deaths")

    def __init__(self) -> None:
        self.last_fired_tier: dict[str, int] = {}
        self.last_seen_deaths: dict[str, int] = {}

    def reset(self) -> None:
        self.last_fired_tier.clear()
        self.last_seen_deaths.clear()


_ALLY_BOUNTY_HYSTERESIS = _AllyBountyHysteresis()


def reset_ally_bounty_hysteresis() -> None:
    """Test-only: drop per-ally fired-tier state."""
    _ALLY_BOUNTY_HYSTERESIS.reset()


# ─── Matchup-mismatch hysteresis ─────────────────────────────────────────────
# Per-enemy fired-tier tracker for "you're losing the lane to this specific
# opponent" detection. The deficit is measured as (deaths_to_them) minus
# (kills_on_them) so a 3-3 trade isn't flagged — only a real one-sided
# pattern triggers.

class _MatchupMismatchHysteresis:
    """Per-enemy "highest deficit tier announced this game" tracker.

    Unlike bounty hysteresis there's no "death resets" semantic — once you
    are 3-deep on a matchup it doesn't un-mismatch by you respawning. Only
    a kill on that enemy can reduce the deficit, which the rule recomputes
    fresh every tick from raw_events anyway.
    """
    __slots__ = ("last_fired_tier",)

    def __init__(self) -> None:
        self.last_fired_tier: dict[str, int] = {}

    def reset(self) -> None:
        self.last_fired_tier.clear()


_MATCHUP_MISMATCH_HYSTERESIS = _MatchupMismatchHysteresis()


def reset_matchup_mismatch_hysteresis() -> None:
    """Test-only: drop per-enemy mismatch-tier state."""
    _MATCHUP_MISMATCH_HYSTERESIS.reset()


# ─── Plate window hysteresis ─────────────────────────────────────────────────
# Single-fire reminder for the 13:00-14:00 window. After 14:00 the outer
# turret plates despawn — any plate not yet popped is gold left on the table
# for the rest of the game.

class _PlateWindowHysteresis:
    """Tracks whether the plate-despawn reminder has fired this game."""
    __slots__ = ("fired",)

    def __init__(self) -> None:
        self.fired = False

    def reset(self) -> None:
        self.fired = False


_PLATE_WINDOW_HYSTERESIS = _PlateWindowHysteresis()


def reset_plate_window_hysteresis() -> None:
    """Test-only: re-arm the plate-despawn reminder."""
    _PLATE_WINDOW_HYSTERESIS.reset()


# ─── First-blood hysteresis ──────────────────────────────────────────────────
# A game has exactly one First Blood. The rule fires once when it's detected,
# then stays silent for the rest of the game.

class _FirstBloodHysteresis:
    """Tracks whether the FB announcement has fired this game."""
    __slots__ = ("fired",)

    def __init__(self) -> None:
        self.fired = False

    def reset(self) -> None:
        self.fired = False


_FIRST_BLOOD_HYSTERESIS = _FirstBloodHysteresis()


def reset_first_blood_hysteresis() -> None:
    """Test-only: re-arm the FB announcement."""
    _FIRST_BLOOD_HYSTERESIS.reset()


# ─── Teamfight-outcome hysteresis ────────────────────────────────────────────
# Don't re-fire the same fight every tick. After firing, hold for 30s (the
# "press the win / recover the loss" window) before allowing the next
# teamfight rec.

class _TeamfightOutcomeHysteresis:
    """Tracks the latest fight already announced, so the rule fires once
    per teamfight rather than every tick the recent-window stays populated.
    """
    __slots__ = ("last_fired_event_time",)

    def __init__(self) -> None:
        self.last_fired_event_time: float = -1.0

    def reset(self) -> None:
        self.last_fired_event_time = -1.0


_TEAMFIGHT_OUTCOME_HYSTERESIS = _TeamfightOutcomeHysteresis()


def reset_teamfight_outcome_hysteresis() -> None:
    """Test-only: re-arm teamfight-outcome detection."""
    _TEAMFIGHT_OUTCOME_HYSTERESIS.reset()


# ─── Shutdown-taken hysteresis ───────────────────────────────────────────────
# Tracks the highest streak each enemy reached *while alive* + which death
# instance we already announced shutdown for. The pre-death tier matters
# because _kill_streak immediately drops to 0 once the death event lands in
# raw_events — by then we'd see "no streak" and miss the conversion call.

class _ShutdownTakenHysteresis:
    """Per-enemy "highest tier seen while alive" + "deaths count we've
    already announced shutdown for" tracker.
    """
    __slots__ = ("last_alive_tier", "fired_for_death")

    def __init__(self) -> None:
        self.last_alive_tier: dict[str, int] = {}
        self.fired_for_death: dict[str, int] = {}

    def reset(self) -> None:
        self.last_alive_tier.clear()
        self.fired_for_death.clear()


_SHUTDOWN_TAKEN_HYSTERESIS = _ShutdownTakenHysteresis()


def reset_shutdown_taken_hysteresis() -> None:
    """Test-only: drop the shutdown-taken state."""
    _SHUTDOWN_TAKEN_HYSTERESIS.reset()


# ─── Objective-taken hysteresis ──────────────────────────────────────────────
# Keyed on the EventTime of each objective kill so we fire once per instance
# (not every tick the kill is in the cumulative event log).

class _ObjectiveTakenHysteresis:
    """Tracks the kill EventTimes we've already announced conversion for."""
    __slots__ = ("fired_event_times",)

    def __init__(self) -> None:
        # Use a set of (event_name, event_time) tuples — DragonKill/BaronKill/
        # HeraldKill all have unique EventTimes, but key on both for safety.
        self.fired_event_times: set[tuple[str, float]] = set()

    def reset(self) -> None:
        self.fired_event_times.clear()


_OBJECTIVE_TAKEN_HYSTERESIS = _ObjectiveTakenHysteresis()


def reset_objective_taken_hysteresis() -> None:
    """Test-only: drop the objective-taken-fired set."""
    _OBJECTIVE_TAKEN_HYSTERESIS.reset()


# ─── Objective-bounty hysteresis ─────────────────────────────────────────────
# Two flags — one for "we're behind, comeback bounty active" and one for
# "we're ahead, our deaths will give them comeback bounty". Each flips
# armed/disarmed across threshold crossings so the rule fires once per
# state-change rather than every tick the condition holds.

class _ObjectiveBountyHysteresis:
    """Per-direction (behind / ahead) fired flag."""
    __slots__ = ("fired_behind", "fired_ahead")

    def __init__(self) -> None:
        self.fired_behind = False
        self.fired_ahead = False

    def reset(self) -> None:
        self.fired_behind = False
        self.fired_ahead = False


_OBJECTIVE_BOUNTY_HYSTERESIS = _ObjectiveBountyHysteresis()


def reset_objective_bounty_hysteresis() -> None:
    """Test-only: drop both bounty-fired flags."""
    _OBJECTIVE_BOUNTY_HYSTERESIS.reset()
