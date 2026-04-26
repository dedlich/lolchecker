"""View-model the overlay consumes.

Bundles everything the UI needs to render a single frame. Phase 6
(Integration) builds these from raw session payloads + advisor outputs;
Phase 5 (UI) only consumes them.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ..advisor.composition import CompositionGap
from ..advisor.picks import PickSuggestion
from ..data.models import ChampSelectSession, CounterEntry

ConnectionState = Literal["disconnected", "waiting", "connected", "reconnecting"]


class SessionView(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    connection_state: ConnectionState = "disconnected"
    session: ChampSelectSession | None = None
    # Indexed by enemy cell_id → counters in *that enemy's* role
    enemy_counters: dict[int, list[CounterEntry]] = Field(default_factory=dict)
    suggestions: list[PickSuggestion] = Field(default_factory=list)
    gaps: list[CompositionGap] = Field(default_factory=list)
    enemy_names: dict[int, str] = Field(default_factory=dict)
    """Map champion id → display name (filled from Data Dragon by integration)."""
