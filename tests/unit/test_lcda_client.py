"""Tests for the Live Client Data API HTTP client."""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from champ_assistant.lcda.client import LcdaClient, LcdaUnavailable

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "lcda"


def _load(name: str) -> dict:
    return json.loads((FIXTURE_DIR / name).read_text())


def _transport(handler) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_all_game_data_returns_payload() -> None:
    payload = _load("allgamedata_midgame.json")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/liveclientdata/allgamedata"
        return httpx.Response(200, json=payload)

    client = LcdaClient(transport=_transport(handler))
    try:
        data = await client.all_game_data()
        assert data["gameData"]["gameMode"] == "CLASSIC"
        assert any(e["EventName"] == "BaronKill" for e in data["events"]["Events"])
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_event_data_returns_event_list() -> None:
    payload = _load("allgamedata_midgame.json")
    events_only = {"Events": payload["events"]["Events"]}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/liveclientdata/eventdata"
        return httpx.Response(200, json=events_only)

    client = LcdaClient(transport=_transport(handler))
    try:
        events = await client.event_data()
        assert len(events) == 7
        assert events[0]["EventName"] == "GameStart"
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_404_raises_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    client = LcdaClient(transport=_transport(handler))
    try:
        with pytest.raises(LcdaUnavailable):
            await client.all_game_data()
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_connection_error_raises_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("nothing listening on 2999")

    client = LcdaClient(transport=_transport(handler))
    try:
        with pytest.raises(LcdaUnavailable):
            await client.all_game_data()
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_is_available_true_when_endpoint_responds() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"gameMode": "CLASSIC", "gameTime": 12.0})

    client = LcdaClient(transport=_transport(handler))
    try:
        assert await client.is_available() is True
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_is_available_false_when_endpoint_missing() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("not running")

    client = LcdaClient(transport=_transport(handler))
    try:
        assert await client.is_available() is False
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_event_data_handles_empty_events_block() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"Events": []})

    client = LcdaClient(transport=_transport(handler))
    try:
        events = await client.event_data()
        assert events == []
    finally:
        await client.aclose()
