"""GitHub Releases update checker.

Polls ``/repos/<owner>/<repo>/releases/latest`` once at startup, compares the
returned tag to the running app's ``__version__``, and notifies the UI when a
newer release is published. Failures (network down, GitHub API rate-limited,
malformed JSON) degrade silently — never blocks the app from starting.
"""
from __future__ import annotations

import logging
import re

import httpx

logger = logging.getLogger(__name__)

DEFAULT_REPO = "dedlich/lolchecker"
DEFAULT_TIMEOUT = 5.0


def _parse_version(tag: str) -> tuple[int, ...]:
    """Convert a tag like 'v0.2.0' or '0.2.0-beta.1' to a comparable tuple."""
    # Strip leading 'v' and anything after a hyphen (pre-release suffix).
    cleaned = tag.lstrip("v").split("-", 1)[0]
    parts = re.findall(r"\d+", cleaned)
    if not parts:
        return (0,)
    return tuple(int(p) for p in parts)


def is_newer(latest_tag: str, current_version: str) -> bool:
    """Strict-greater-than version comparison."""
    return _parse_version(latest_tag) > _parse_version(current_version)


async def fetch_latest_release(
    repo: str = DEFAULT_REPO,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, str] | None:
    """Return ``{"tag": ..., "url": ...}`` or None on any failure."""
    url = f"https://api.github.com/repos/{repo}/releases/latest"
    try:
        kwargs: dict[str, object] = {"timeout": timeout}
        if transport is not None:
            kwargs["transport"] = transport
        async with httpx.AsyncClient(**kwargs) as client:
            response = await client.get(
                url, headers={"Accept": "application/vnd.github+json"}
            )
            response.raise_for_status()
            data = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.info("update_check_failed", extra={"error": str(exc)})
        return None
    tag = data.get("tag_name")
    html_url = data.get("html_url")
    if not isinstance(tag, str) or not isinstance(html_url, str):
        return None
    return {"tag": tag, "url": html_url}


async def check_for_update(
    current_version: str,
    *,
    repo: str = DEFAULT_REPO,
    timeout: float = DEFAULT_TIMEOUT,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, str] | None:
    """If a newer release exists, return its info; otherwise None."""
    info = await fetch_latest_release(repo, timeout=timeout, transport=transport)
    if info is None:
        return None
    if is_newer(info["tag"], current_version):
        return info
    return None
