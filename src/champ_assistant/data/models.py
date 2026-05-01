"""Pydantic v2 domain models.

Conventions:
  - All models are frozen (immutable) — they flow through async tasks; mutating
    them across boundaries is a bug, not a feature.
  - Champion identity uses *both* the numeric id (LCU sends int) and the
    string key (Riot data files use strings like "Garen", "MissFortune").
  - Roles in our domain are 5 short tokens: TOP / JUNGLE / MID / BOT / SUPPORT.
    LCU uses TOP / JUNGLE / MIDDLE / BOTTOM / UTILITY — we normalize on the way in.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# --- Roles -----------------------------------------------------------------

Role = Literal["TOP", "JUNGLE", "MID", "BOT", "SUPPORT"]
LcuRole = Literal["TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY"]

_LCU_TO_DOMAIN: dict[str, Role] = {
    "TOP": "TOP",
    "JUNGLE": "JUNGLE",
    "MIDDLE": "MID",
    "BOTTOM": "BOT",
    "UTILITY": "SUPPORT",
}


def normalize_role(lcu_role: str | None) -> Role | None:
    """Map LCU role tokens (MIDDLE/BOTTOM/UTILITY) to our domain (MID/BOT/SUPPORT)."""
    if lcu_role is None:
        return None
    return _LCU_TO_DOMAIN.get(lcu_role.upper())


Tier = Literal["S+", "S", "A", "B", "C", "D"]


# --- Static data: champions / counters / tiers / tags ----------------------


class Champion(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: int  # numeric champion id (LCU sends this as int)
    key: str  # string key, e.g. "Garen", "MissFortune"
    name: str  # display name, e.g. "Garen", "Miss Fortune"
    tags: list[str] = Field(default_factory=list)


class CounterNotes(BaseModel):
    """Structured guidance for *how* to play a counter (UI renders these fields)."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    lane_phase: str | None = None
    spike: str | None = None
    items: list[str] = Field(default_factory=list)
    trade_pattern: str | None = None


class CounterEntry(BaseModel):
    """A single counter pick recommendation against an enemy in a given role."""

    model_config = ConfigDict(frozen=True)

    champion: str  # champion key (matches Champion.key)
    score: float = Field(ge=0.0, le=10.0)
    tier: Tier | None = None
    notes: CounterNotes = Field(default_factory=CounterNotes)


class CounterMatrix(BaseModel):
    """Indexed by enemy_champ_key → role → ordered list of counters (best first)."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    patch: str | None = None
    matrix: dict[str, dict[Role, list[CounterEntry]]] = Field(default_factory=dict)

    def counters_for(self, enemy_key: str, role: Role) -> list[CounterEntry]:
        """Look up counters; empty list when no entry — never KeyError."""
        return self.matrix.get(enemy_key, {}).get(role, [])


class TierEntry(BaseModel):
    model_config = ConfigDict(frozen=True)

    champion: str
    tier: Tier


class TierList(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    patch: str | None = None
    tiers: dict[Role, list[TierEntry]] = Field(default_factory=dict)

    def tier_for(self, champion_key: str, role: Role) -> Tier | None:
        for entry in self.tiers.get(role, []):
            if entry.champion == champion_key:
                return entry.tier
        return None


class TagsData(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    tags: dict[str, list[str]] = Field(default_factory=dict)

    def tags_for(self, champion_key: str) -> list[str]:
        return self.tags.get(champion_key, [])


class ChampionBuild(BaseModel):
    """Recommended runes / items / summoner spells for a champion in a role.

    A build may carry alternative variants the user can cycle through
    (different rune trees, item rushes, etc.) post-pick. The "main"
    build is whatever the legacy fields hold; ``variants`` lists
    additional named alternatives. Variants of variants are ignored —
    the recursion is one level deep by convention."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    runes: list[str] = Field(default_factory=list)
    items: list[str] = Field(default_factory=list)
    summoners: list[str] = Field(default_factory=list)
    skill_order: list[str] = Field(default_factory=list)
    name: str = "Default"
    variants: list["ChampionBuild"] = Field(default_factory=list)


class BuildLibrary(BaseModel):
    """Indexed by champion key → role → build."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    patch: str | None = None
    builds: dict[str, dict[Role, ChampionBuild]] = Field(default_factory=dict)

    def build_for(self, champion_key: str, role: Role) -> ChampionBuild | None:
        return self.builds.get(champion_key, {}).get(role)


# --- Live champ-select session -------------------------------------------


class TeamMember(BaseModel):
    """Normalized champ-select team slot.

    Accepts both our snake_case names and the LCU's camelCase keys
    (cellId / championId / summonerId / assignedPosition) so payloads
    parse via ``model_validate(raw_lcu_dict)`` without manual mapping.
    LCU position tokens (MIDDLE/BOTTOM/UTILITY) are normalized to the
    domain values (MID/BOT/SUPPORT).

    All fields are forgiving: real LCU payloads sometimes include
    ``null`` values for ints during transitional phases, and slots
    without an assigned position carry the empty string. Defaults
    cover those cases without forcing a parse failure.
    """

    model_config = ConfigDict(frozen=True, populate_by_name=True, extra="ignore")

    cell_id: int = Field(default=-1, alias="cellId")
    champion_id: int = Field(default=0, alias="championId")  # 0 = not picked yet
    summoner_id: int | None = Field(default=None, alias="summonerId")
    puuid: str | None = Field(default=None, alias="puuid")
    assigned_position: Role | None = Field(default=None, alias="assignedPosition")
    locked: bool = False

    @field_validator("cell_id", mode="before")
    @classmethod
    def _coerce_cell_id(cls, value: Any) -> Any:
        return -1 if value is None else value

    @field_validator("champion_id", mode="before")
    @classmethod
    def _coerce_champion_id(cls, value: Any) -> Any:
        return 0 if value is None else value

    @field_validator("assigned_position", mode="before")
    @classmethod
    def _normalize_position(cls, value: Any) -> Any:
        if value is None or value == "":
            return None
        if isinstance(value, str):
            normalized = normalize_role(value)
            if normalized is not None:
                return normalized
        return value


class Action(BaseModel):
    """A single ban or pick slot in the champ-select sequence.

    LCU payload structure: ``session.actions`` is a list-of-lists —
    each inner list is a "step" of parallel actions resolving at the
    same time (e.g. all 6 first-phase bans together). Within a step,
    each action is one player's intent for one slot.

    For UI-driven hover/select, we look up the local player's
    not-yet-completed action of a given type (``pick`` or ``ban``).
    """

    model_config = ConfigDict(frozen=True, populate_by_name=True, extra="ignore")

    id: int = 0
    actor_cell_id: int = Field(default=-1, alias="actorCellId")
    champion_id: int = Field(default=0, alias="championId")
    type: str = ""  # "pick" | "ban" | "ten_bans_reveal" etc.
    completed: bool = False
    is_in_progress: bool = Field(default=False, alias="isInProgress")


class ChampSelectSession(BaseModel):
    """Normalized champ-select state derived from the LCU session payload.

    Real LCU sessions (recent client versions) often only carry the
    phase inside ``timer.phase`` rather than at the top level, plus
    dozens of fields we don't care about (bans, chatDetails,
    entitledFeatureState, …). The model_validator below pulls phase
    out of the timer when needed; ``extra="ignore"`` swallows the rest.
    """

    model_config = ConfigDict(frozen=True, populate_by_name=True, extra="ignore")

    phase: str = ""
    local_player_cell_id: int = Field(default=-1, alias="localPlayerCellId")
    my_team: list[TeamMember] = Field(default_factory=list, alias="myTeam")
    their_team: list[TeamMember] = Field(default_factory=list, alias="theirTeam")
    actions: list[list[Action]] = Field(default_factory=list)
    """Stepped action sequence — preserved so UI click-to-hover can
    look up which slot to PATCH for the local player."""

    @model_validator(mode="before")
    @classmethod
    def _hydrate_phase_from_timer(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        # Top-level "phase" wins; otherwise fall back to timer.phase.
        if not data.get("phase"):
            timer = data.get("timer")
            if isinstance(timer, dict):
                timer_phase = timer.get("phase")
                if isinstance(timer_phase, str) and timer_phase:
                    data = {**data, "phase": timer_phase}
        return data

    @property
    def me(self) -> TeamMember | None:
        for p in self.my_team:
            if p.cell_id == self.local_player_cell_id:
                return p
        return None

    def my_pending_action(self, action_type: str) -> Action | None:
        """Local player's not-yet-completed action of the given type
        (``"pick"`` or ``"ban"``). ``None`` if the player has no
        such pending slot — wrong phase, blind pick, action already
        completed. Caller treats None as "can't PATCH right now"."""
        if self.local_player_cell_id < 0:
            return None
        for step in self.actions:
            for action in step:
                if (
                    action.actor_cell_id == self.local_player_cell_id
                    and action.type == action_type
                    and not action.completed
                ):
                    return action
        return None

    def display_subphase(self) -> str:
        """Coarse-grained phase label the UI uses to choose what to
        render. Maps the LCU phase + the in-progress action type into
        a single string so the overlay state-machine has one input.

        Returns one of:
          ``"idle"``           — no champ-select session
          ``"planning"``       — role assignment, no actions yet
          ``"ban"``            — currently in the ban step
          ``"pick"``           — currently in the pick step
          ``"finalization"``   — lock-in countdown after picks
          ``"loading"``        — game starting, players known, no live game
          ``"in_game"``        — game running

        ``BAN_PICK`` is the umbrella LCU phase covering both bans and
        picks; we look at which step is currently in progress to
        differentiate. When the in-progress step is ambiguous (every
        action either completed or empty), we fall back to the most
        recent step's type."""
        phase = (self.phase or "").upper()
        if phase == "" or phase == "NONE":
            return "idle"
        if phase == "GAME_STARTING":
            return "loading"
        if phase == "FINALIZATION":
            return "finalization"
        if phase == "PLANNING":
            return "planning"
        if phase == "BAN_PICK":
            current = self._current_action_type()
            return current or "pick"
        if phase in ("IN_PROGRESS", "GAME"):
            return "in_game"
        return phase.lower()

    def _current_action_type(self) -> str | None:
        """Type of the action step currently in progress. Returns
        None when no step is active (between turns)."""
        # Iterate in order — first step with isInProgress wins.
        for step in self.actions:
            if any(a.is_in_progress for a in step):
                # All actions in a step share the same type (Riot's
                # invariant). Return the first non-empty one.
                for action in step:
                    if action.type:
                        return action.type
        # No in-progress step (e.g. between turns) — fall back to
        # the latest step that has any action, useful for "we just
        # finished bans, picks haven't started" gap.
        for step in reversed(self.actions):
            for action in step:
                if action.type:
                    return action.type
        return None
