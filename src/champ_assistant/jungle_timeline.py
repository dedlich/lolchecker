"""Deterministic jungle camp respawn predictor.

LCDA exposes only a handful of objective events (DragonKill, BaronKill,
HeraldKill, GameStart) and provides no kill events for the small jungle
camps (Red/Blue/Gromp/Krugs/Raptors/Wolves/Scuttle). Memory reading and
OCR are off the table (Vanguard, brittle). What we *do* have is the
official ``gameTime`` from /allgamedata.

That is enough for a Blitz-style camp predictor: assume the worst-case
clear (camp dies the moment it spawns), advance a fixed-interval cycle
forward from there, and surface the next predicted spawn. The result is
not "when did the enemy jungler kill it" — it's "when COULD this camp
have respawned at the earliest". For 80% of in-game uses (timing your
own clear, planning a counter-jungle window) that's the same answer.

Confidence model
================

Predictions decay as the game progresses because real kills increasingly
diverge from the assumed-immediate-kill cycle. Major objective events
(Dragon/Baron/Herald) act as soft anchors — they don't move the camp
timers (those events have nothing to do with small camps), but they
confirm the game is in fact progressing through expected phases, which
gives the deterministic prediction a small confidence boost.

The confidence is exposed to the UI as a 0..1 float; the UI is free to
use it as opacity, color saturation, or to add a "?" indicator. The
*timer values themselves are never modified* by confidence — only their
display weight.

This module is deliberately Qt-free so it can be unit-tested without a
QApplication. Wire-up to ``StateStore`` happens in ``__main__``.
"""
from __future__ import annotations

import logging
import math
from collections.abc import Callable
from dataclasses import dataclass

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Camp specifications
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class CampSpec:
    """Static description of a jungle camp's spawn cycle.

    Values follow the canonical Riot timings used across Season 14+.
    Kept as floats so future patches can introduce sub-second tweaks
    without changing the type surface.
    """
    id: str
    name: str
    first_spawn_s: float   # in-game time (seconds) when the camp first appears
    respawn_s: float       # cooldown after a kill until next spawn


# Canonical jungle timings. Small-camp respawn is the standardized 2:15
# approximation the spec calls for; Buff camps are 5:00; Scuttle first
# spawns at 3:15 and respawns 2:30 after each kill.
JUNGLE_CAMPS: tuple[CampSpec, ...] = (
    CampSpec("red_buff",  "Red Buff",  90.0,  300.0),
    CampSpec("blue_buff", "Blue Buff", 90.0,  300.0),
    CampSpec("gromp",     "Gromp",     90.0,  135.0),
    CampSpec("krugs",     "Krugs",     90.0,  135.0),
    CampSpec("raptors",   "Raptors",   90.0,  135.0),
    CampSpec("wolves",    "Wolves",    90.0,  135.0),
    CampSpec("scuttle",   "Scuttle",   195.0, 150.0),
)


# --------------------------------------------------------------------------
# State model exposed to the UI
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class CampState:
    """Snapshot of one camp's predicted state. UI renders this directly."""
    id: str
    name: str
    state: str                 # "alive" | "respawning"
    next_spawn_at: float       # in-game seconds — absolute, not relative
    time_remaining: float      # seconds until ``next_spawn_at`` (>= 0)
    confidence: float          # 0..1


# --------------------------------------------------------------------------
# Engine
# --------------------------------------------------------------------------
StateListener = Callable[[dict[str, CampState]], None]

# Tunables. Pulled out as module-level constants so tests can monkey-patch
# them and a future confidence retune is one place, not seven.
ALIVE_GRACE_S       = 5.0    # camp shows as "alive" for this long after each spawn
INITIAL_CONFIDENCE  = 1.0
MIN_CONFIDENCE      = 0.30
DECAY_PER_MINUTE    = 0.02   # 0.02 / min → ~0.4 floor at 30:00 before boosts
OBJECTIVE_BOOST     = 0.05   # bumped per Dragon/Baron/Herald kill seen


class JungleTimelineEngine:
    """Pure-Python predictor — no Qt, no I/O, no globals.

    Two inputs only: ``game_time`` (mandatory, from LCDA) and an optional
    list of LCDA events that act as soft anchors for confidence. Output is
    a stable mapping ``{camp_id: CampState}`` that the UI subscribes to.
    """

    def __init__(self, *, specs: tuple[CampSpec, ...] = JUNGLE_CAMPS) -> None:
        self._specs = specs
        self._initialized = False
        self._game_time = 0.0
        # We count anchors (GameStart + observed objective kills) so a long
        # game with frequent Dragon/Baron rotations stays at higher
        # confidence than a quiet 25-min stalemate.
        self._anchor_count = 0
        self._seen_event_ids: set[int] = set()
        self._listeners: list[StateListener] = []
        # Per-camp observed-clear anchors. Set by ``register_clear`` from
        # the vision subsystem. Maps camp_id -> game_time at clear.
        # When set, _camp_state_at uses this anchor instead of the
        # worst-case-clear cycle math.
        self._observed_clears: dict[str, float] = {}

    # -- lifecycle --------------------------------------------------------

    def initialize(self, game_time: float = 0.0) -> None:
        """Bring the engine online at GameStart.

        Idempotent: a second call (e.g. a re-fired GameStart event from a
        late LCDA reconnect) won't reset accumulated anchors.
        """
        if self._initialized:
            return
        self._initialized = True
        self._game_time = max(0.0, _coerce_finite(game_time))
        self._anchor_count = 1   # GameStart counts as the first anchor
        logger.info("jungle_timeline initialized at game_time=%.1f", self._game_time)

    # -- inputs -----------------------------------------------------------

    def tick(
        self,
        game_time: float,
        events: list[dict] | None = None,
    ) -> dict[str, CampState]:
        """Advance the simulation. Returns the fresh state mapping.

        Graceful degradation: a NaN/negative game_time is rejected and the
        last known state is returned unchanged so an LCDA hiccup never
        produces nonsense timers.
        """
        gt = _coerce_finite(game_time)
        if gt < 0:
            return self.states()
        if not self._initialized:
            # Auto-init the first time we see a sane game_time. Prevents
            # the engine from sitting silent if GameStart event was missed
            # (e.g. user attached overlay mid-match).
            self.initialize(gt)
        self._game_time = gt
        if events:
            self._absorb_events(events)
        states = self._compute_states()
        self._notify(states)
        return states

    def register_clear(self, camp_id: str, game_time: float | None = None) -> None:
        """Anchor a camp's respawn cycle to an observed clear time.

        Called from the vision subsystem when it detects camp icon
        disappearing. Stores the anchor; subsequent ``states()`` calls
        compute next_spawn from anchor + respawn_s instead of the
        worst-case-clear cycle math.

        Confidence model is intentionally NOT touched — vision is a
        soft anchor, the deterministic confidence trajectory stays
        the source of truth for UI weighting. Spec compliance: "Do
        not modify confidence model. Do not override deterministic
        math."
        """
        gt = game_time if game_time is not None else self._game_time
        if not isinstance(gt, (int, float)) or not math.isfinite(gt) or gt < 0:
            return
        # Reject unknown camp ids — silent rather than raise so a
        # bad event from the vision pipeline can't crash the engine.
        if not any(spec.id == camp_id for spec in self._specs):
            return
        self._observed_clears[camp_id] = float(gt)
        # Notify listeners so the UI re-renders with the new anchor.
        self._notify(self._compute_states())

    def _absorb_events(self, events: list[dict]) -> None:
        """Bump confidence on every newly-seen objective kill.

        EventID is monotonically increasing per match — we dedupe so a
        replayed event list (LCDA returns the cumulative log on every
        poll) doesn't double-count.
        """
        for event in events:
            name = event.get("EventName") or ""
            if name not in ("DragonKill", "BaronKill", "HeraldKill"):
                continue
            event_id = event.get("EventID")
            if not isinstance(event_id, int):
                continue
            if event_id in self._seen_event_ids:
                continue
            self._seen_event_ids.add(event_id)
            self._anchor_count += 1

    # -- outputs ----------------------------------------------------------

    def states(self) -> dict[str, CampState]:
        """Read-only snapshot of the current camp states."""
        return self._compute_states()

    def subscribe(self, listener: StateListener) -> Callable[[], None]:
        """Register a listener; returns the unsubscribe callable."""
        self._listeners.append(listener)

        def _unsub() -> None:
            try:
                self._listeners.remove(listener)
            except ValueError:
                pass

        return _unsub

    @property
    def confidence(self) -> float:
        """Current global confidence floor exposed for diagnostics."""
        return self._current_confidence()

    @property
    def is_initialized(self) -> bool:
        return self._initialized

    # -- internals --------------------------------------------------------

    def _compute_states(self) -> dict[str, CampState]:
        """Camps without an observed clear render nothing — the user
        explicitly rejected predictive 'pseudo' timers because they
        don't reflect reality. We synthesize an ``alive`` sentinel
        for un-anchored camps so the UI's existing 'skip if alive'
        paint path naturally hides them.

        Camps with a registered clear go through the real countdown
        math (anchor + respawn cycle) — that's the trustworthy half
        of the engine and is preserved unchanged.
        """
        confidence = self._current_confidence()
        out: dict[str, CampState] = {}
        for spec in self._specs:
            anchor = self._observed_clears.get(spec.id)
            if anchor is None:
                out[spec.id] = CampState(
                    id=spec.id, name=spec.name, state="alive",
                    next_spawn_at=0.0, time_remaining=0.0,
                    confidence=0.0,
                )
                continue
            out[spec.id] = _camp_state_at(
                spec, self._game_time, confidence,
                clear_anchor=anchor,
            )
        return out

    def _current_confidence(self) -> float:
        if not self._initialized:
            return 0.0
        decay = (self._game_time / 60.0) * DECAY_PER_MINUTE
        boost = max(0, self._anchor_count - 1) * OBJECTIVE_BOOST
        raw = INITIAL_CONFIDENCE - decay + boost
        return max(MIN_CONFIDENCE, min(INITIAL_CONFIDENCE, raw))

    def _notify(self, states: dict[str, CampState]) -> None:
        for cb in list(self._listeners):
            try:
                cb(states)
            except Exception:  # noqa: BLE001 — never let one listener kill others
                logger.exception("jungle_timeline listener failed")


# --------------------------------------------------------------------------
# Pure helpers (testable without an engine instance)
# --------------------------------------------------------------------------
def _coerce_finite(value: float) -> float:
    """Best-effort to a finite float, otherwise -1.0 (sentinel for invalid)."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return -1.0
    if not math.isfinite(value):
        return -1.0
    return float(value)


def _camp_state_at(
    spec: CampSpec,
    game_time: float,
    confidence: float,
    *,
    clear_anchor: float | None = None,
) -> CampState:
    """Compute a single camp's state at ``game_time`` from its spec.

    Two cycle sources:
      * Worst-case (default): assume immediate clear at every spawn,
        next_spawn = first_spawn + (cycles+1) × respawn_s.
      * Observed-anchor: when ``clear_anchor`` is provided (vision
        subsystem set it via ``JungleTimelineEngine.register_clear``),
        next_spawn = clear_anchor + respawn_s. The anchor naturally
        re-cycles after one respawn — once game_time exceeds the
        anchor's first re-spawn, we fall back to the worst-case
        cycle from there.

    A short ``ALIVE_GRACE_S`` window after each spawn is reported as
    "alive" so UI can briefly highlight the camp as available.
    """
    if game_time < spec.first_spawn_s:
        next_spawn = spec.first_spawn_s
        return CampState(
            id=spec.id,
            name=spec.name,
            state="respawning",
            next_spawn_at=next_spawn,
            time_remaining=max(0.0, next_spawn - game_time),
            confidence=confidence,
        )

    # Anchor path: if we have an observed clear AND it's still "fresh"
    # (within one full cycle of game_time), use it.
    if clear_anchor is not None and clear_anchor <= game_time < clear_anchor + spec.respawn_s + ALIVE_GRACE_S:
        next_spawn = clear_anchor + spec.respawn_s
        since_spawn = game_time - next_spawn
        if 0 <= since_spawn < ALIVE_GRACE_S:
            return CampState(
                id=spec.id, name=spec.name, state="alive",
                next_spawn_at=next_spawn + spec.respawn_s,
                time_remaining=max(0.0, next_spawn + spec.respawn_s - game_time),
                confidence=confidence,
            )
        return CampState(
            id=spec.id, name=spec.name, state="respawning",
            next_spawn_at=next_spawn,
            time_remaining=max(0.0, next_spawn - game_time),
            confidence=confidence,
        )

    # Worst-case cycle path.
    elapsed = game_time - spec.first_spawn_s
    cycle_index = int(elapsed // spec.respawn_s)
    last_spawn = spec.first_spawn_s + cycle_index * spec.respawn_s
    next_spawn = last_spawn + spec.respawn_s
    since_last = game_time - last_spawn

    if since_last < ALIVE_GRACE_S:
        return CampState(
            id=spec.id,
            name=spec.name,
            state="alive",
            next_spawn_at=next_spawn,
            time_remaining=max(0.0, next_spawn - game_time),
            confidence=confidence,
        )
    return CampState(
        id=spec.id,
        name=spec.name,
        state="respawning",
        next_spawn_at=next_spawn,
        time_remaining=max(0.0, next_spawn - game_time),
        confidence=confidence,
    )
