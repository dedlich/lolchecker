"""Live Client Data API integration.

Riot exposes ``https://127.0.0.1:2999/liveclientdata/...`` only while a game
is running. It returns the public scoreboard, game time, and an event log
(DragonKill, BaronKill, ChampionKill, ...). All read-only, no auth needed.
We use it for objective timers, summoner spell helpers, and power-spike
hints — never for inputs into the game.
"""
from .client import LcdaClient, LcdaUnavailable
from .objectives import ObjectiveTimer, compute_objectives
from .source import LcdaSnapshot, LcdaSource

__all__ = [
    "LcdaClient",
    "LcdaSnapshot",
    "LcdaSource",
    "LcdaUnavailable",
    "ObjectiveTimer",
    "compute_objectives",
]
