"""LCU writes that mutate champ-select state.

PATCHes a single action slot to either hover or lock a champion in
the player's pick or ban turn. Two callable surfaces:

  * ``hover_action`` — ``completed: false``, tentative hover only.
    The user still has to manually press "Lock In" / "Ban".
  * ``commit_action`` — ``completed: true``, locks immediately.
    Single-click flow per user preference; misclicks become real
    picks/bans, that's the trade-off.

Errors:
  * 4xx from LCU (e.g. action already completed, not your turn) →
    LcuClientError raised by the client.request retry layer; caller
    catches and surfaces in the status bar.
  * Action not found (caller resolved a stale action_id) → 404,
    same path.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .client import LcuClient


async def hover_action(
    client: "LcuClient", *, action_id: int, champion_id: int,
) -> int:
    """PATCH a champ-select action to hover ``champion_id`` without
    completing the slot. Returns the HTTP status code."""
    response = await client.patch(
        f"/lol-champ-select/v1/session/actions/{action_id}",
        json={"championId": champion_id, "completed": False},
    )
    return response.status_code


async def commit_action(
    client: "LcuClient", *, action_id: int, champion_id: int,
) -> int:
    """PATCH a champ-select action and lock it in. The pick/ban becomes
    final once Riot accepts the request — no further client-side
    confirmation needed. Returns the HTTP status code."""
    response = await client.patch(
        f"/lol-champ-select/v1/session/actions/{action_id}",
        json={"championId": champion_id, "completed": True},
    )
    return response.status_code
