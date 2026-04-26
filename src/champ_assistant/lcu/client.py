"""LCU HTTP client (httpx, TLS-skip for 127.0.0.1).

Phase 2 module.
"""
from __future__ import annotations


class LcuClient:
    """Authenticated httpx client targeting the local LCU REST API."""

    async def __aenter__(self) -> "LcuClient":
        raise NotImplementedError("Phase 2")

    async def __aexit__(self, *exc: object) -> None:
        raise NotImplementedError("Phase 2")
