"""Data Dragon client (champion metadata, patch detection).

Phase 3 module.
"""
from __future__ import annotations


async def fetch_latest_patch() -> str:
    raise NotImplementedError("Phase 3")


async def fetch_champions(patch: str) -> dict[str, object]:
    raise NotImplementedError("Phase 3")
