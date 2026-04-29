"""LCU writes that mutate champ-select state (hover-only).

The two flows here PATCH a single action slot to "hover" a champion
in either the player's pick or ban turn. ``completed: false`` keeps
the action in hover state — the user still needs to manually press
"Lock In" / "Ban" in the client. We never auto-lock: a wrong-button
auto-lock can grief a teammate, and tools like Blitz/Porofessor
follow the same rule. Final commit is always user-driven.

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
    """PATCH a champ-select action to hover ``champion_id``.

    Returns the HTTP status code so the caller can distinguish 204
    (success) from anything else for status-bar messaging. Raises
    LcuClientError on transport failure (already retried internally).
    """
    response = await client.patch(
        f"/lol-champ-select/v1/session/actions/{action_id}",
        json={"championId": champion_id, "completed": False},
    )
    return response.status_code
