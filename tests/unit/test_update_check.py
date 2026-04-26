"""Tests for the GitHub Releases update checker."""
from __future__ import annotations

import httpx
import pytest
import respx

from champ_assistant.update_check import (
    check_for_update,
    fetch_latest_release,
    is_newer,
)

LATEST_URL = "https://api.github.com/repos/dedlich/lolchecker/releases/latest"


@pytest.mark.parametrize(
    "latest,current,expected",
    [
        ("v0.2.0", "0.1.0", True),
        ("v0.2.0", "0.2.0", False),
        ("v0.1.5", "0.2.0", False),
        ("0.2.0", "0.1.99", True),
        ("v1.0.0-beta.1", "0.9.0", True),
        ("v0.1.0", "0.1.0-beta.1", False),  # 0.1.0 == 0.1.0
    ],
)
def test_is_newer(latest: str, current: str, expected: bool) -> None:
    assert is_newer(latest, current) is expected


@pytest.mark.asyncio
@respx.mock
async def test_fetch_latest_release_returns_tag_and_url() -> None:
    respx.get(LATEST_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "tag_name": "v0.2.0",
                "html_url": "https://github.com/dedlich/lolchecker/releases/tag/v0.2.0",
                "name": "v0.2.0",
            },
        )
    )
    info = await fetch_latest_release()
    assert info == {
        "tag": "v0.2.0",
        "url": "https://github.com/dedlich/lolchecker/releases/tag/v0.2.0",
    }


@pytest.mark.asyncio
@respx.mock
async def test_fetch_latest_release_returns_none_on_404() -> None:
    respx.get(LATEST_URL).mock(return_value=httpx.Response(404))
    assert await fetch_latest_release() is None


@pytest.mark.asyncio
@respx.mock
async def test_fetch_latest_release_returns_none_on_network_error() -> None:
    respx.get(LATEST_URL).mock(side_effect=httpx.ConnectError("offline"))
    assert await fetch_latest_release() is None


@pytest.mark.asyncio
@respx.mock
async def test_check_for_update_returns_info_when_newer() -> None:
    respx.get(LATEST_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "tag_name": "v0.3.0",
                "html_url": "https://github.com/dedlich/lolchecker/releases/tag/v0.3.0",
            },
        )
    )
    info = await check_for_update("0.1.0")
    assert info is not None
    assert info["tag"] == "v0.3.0"


@pytest.mark.asyncio
@respx.mock
async def test_check_for_update_returns_none_when_same() -> None:
    respx.get(LATEST_URL).mock(
        return_value=httpx.Response(
            200, json={"tag_name": "v0.1.0", "html_url": "https://example.com"}
        )
    )
    assert await check_for_update("0.1.0") is None


@pytest.mark.asyncio
@respx.mock
async def test_check_for_update_returns_none_on_failure() -> None:
    respx.get(LATEST_URL).mock(return_value=httpx.Response(500))
    assert await check_for_update("0.1.0") is None
