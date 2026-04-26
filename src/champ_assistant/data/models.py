"""Pydantic v2 domain models.

Phase 3 module.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

Role = Literal["TOP", "JUNGLE", "MID", "BOT", "SUPPORT"]


class Champion(BaseModel):
    id: int
    key: str
    name: str
    tags: list[str] = []


class TeamMember(BaseModel):
    champion_id: int | None
    role: Role | None
    summoner_id: int | None = None
    locked: bool = False


class ChampSelectSession(BaseModel):
    my_team: list[TeamMember] = []
    enemy_team: list[TeamMember] = []
    phase: str = "UNKNOWN"
