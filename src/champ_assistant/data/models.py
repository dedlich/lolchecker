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

from pydantic import BaseModel, ConfigDict, Field, field_validator

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


# --- Live champ-select session -------------------------------------------


class TeamMember(BaseModel):
    """Normalized champ-select team slot.

    Accepts both our snake_case names and the LCU's camelCase keys
    (cellId / championId / summonerId / assignedPosition) so payloads
    parse via ``model_validate(raw_lcu_dict)`` without manual mapping.
    LCU position tokens (MIDDLE/BOTTOM/UTILITY) are normalized to the
    domain values (MID/BOT/SUPPORT).
    """

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    cell_id: int = Field(alias="cellId")
    champion_id: int = Field(default=0, alias="championId")  # 0 = not picked yet
    summoner_id: int | None = Field(default=None, alias="summonerId")
    assigned_position: Role | None = Field(default=None, alias="assignedPosition")
    locked: bool = False

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
    """Normalized champ-select state derived from the LCU session payload."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    phase: str
    local_player_cell_id: int = Field(default=-1, alias="localPlayerCellId")
    my_team: list[TeamMember] = Field(default_factory=list, alias="myTeam")
    their_team: list[TeamMember] = Field(default_factory=list, alias="theirTeam")

    @property
    def me(self) -> TeamMember | None:
        for p in self.my_team:
            if p.cell_id == self.local_player_cell_id:
                return p
        return None
