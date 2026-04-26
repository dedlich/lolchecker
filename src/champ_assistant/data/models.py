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
    """Recommended runes / items / summoner spells for a champion in a role."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    runes: list[str] = Field(default_factory=list)
    items: list[str] = Field(default_factory=list)
    summoners: list[str] = Field(default_factory=list)


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


class ChampSelectSession(BaseModel):
    """Normalized champ-select state derived from the LCU session payload.

    Real LCU sessions (recent client versions) often only carry the
    phase inside ``timer.phase`` rather than at the top level, plus
    dozens of fields we don't care about (actions, bans, chatDetails,
    entitledFeatureState, …). The model_validator below pulls phase
    out of the timer when needed; ``extra="ignore"`` swallows the rest.
    """

    model_config = ConfigDict(frozen=True, populate_by_name=True, extra="ignore")

    phase: str = ""
    local_player_cell_id: int = Field(default=-1, alias="localPlayerCellId")
    my_team: list[TeamMember] = Field(default_factory=list, alias="myTeam")
    their_team: list[TeamMember] = Field(default_factory=list, alias="theirTeam")

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
