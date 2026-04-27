"""HTTP client for the in-game Live Client Data API (port 2999).

Differences from the LCU client:
  - No auth (Riot relies on the loopback boundary).
  - Self-signed cert → ``verify=False`` (same loopback rationale as LCU).
  - Only reachable while a match is loaded; treat ConnectError as "no game".
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

LCDA_BASE_URL = "https://127.0.0.1:2999"
DEFAULT_TIMEOUT = 2.0


class LcdaUnavailable(RuntimeError):
    """Raised when LCDA can't be reached — the user is not in a game."""


class LcdaClient:
    """Thin wrapper around the four LCDA endpoints we actually use."""

    def __init__(
        self,
        *,
        base_url: str = LCDA_BASE_URL,
        timeout: float = DEFAULT_TIMEOUT,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        kwargs: dict[str, Any] = dict(
            base_url=base_url,
            verify=False,
            timeout=timeout,
        )
        if transport is not None:
            kwargs["transport"] = transport
        self._client = httpx.AsyncClient(**kwargs)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _get(self, path: str) -> Any:
        try:
            response = await self._client.get(path)
        except httpx.HTTPError as exc:
            raise LcdaUnavailable(f"LCDA unreachable: {exc}") from exc
        if response.status_code == 404:
            # 404 happens between game-start and full data initialization
            raise LcdaUnavailable("LCDA endpoint returned 404")
        response.raise_for_status()
        return response.json()

    async def all_game_data(self) -> dict[str, Any]:
        return await self._get("/liveclientdata/allgamedata")

    async def event_data(self) -> list[dict[str, Any]]:
        data = await self._get("/liveclientdata/eventdata")
        events = data.get("Events", []) if isinstance(data, dict) else []
        return list(events)

    async def game_stats(self) -> dict[str, Any]:
        return await self._get("/liveclientdata/gamestats")

    async def is_available(self) -> bool:
        try:
            await self.game_stats()
            return True
        except LcdaUnavailable:
            return False
