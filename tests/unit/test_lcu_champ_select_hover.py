"""Tests for the LCU hover_action helper.

Verifies the PATCH path + payload contract — does NOT exercise the
real LCU. We stub a minimal async client that records the call and
returns a synthetic httpx.Response.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from champ_assistant.lcu.champ_select import commit_action, hover_action


@dataclass
class _FakeResponse:
    status_code: int = 204


@dataclass
class _RecordingClient:
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    response: _FakeResponse = field(default_factory=_FakeResponse)

    async def patch(self, path: str, *, json: dict[str, Any]) -> _FakeResponse:
        self.calls.append((path, json))
        return self.response


def test_hover_action_patches_the_right_path() -> None:
    client = _RecordingClient()
    asyncio.run(hover_action(client, action_id=42, champion_id=86))
    assert client.calls[0][0] == "/lol-champ-select/v1/session/actions/42"


def test_hover_action_sends_completed_false() -> None:
    """Hover-only contract — completed: false. Auto-locking would
    risk griefing teammates if the wrong button is clicked."""
    client = _RecordingClient()
    asyncio.run(hover_action(client, action_id=1, champion_id=122))
    body = client.calls[0][1]
    assert body == {"championId": 122, "completed": False}


def test_hover_action_returns_status_code() -> None:
    client = _RecordingClient(response=_FakeResponse(status_code=204))
    status = asyncio.run(hover_action(client, action_id=1, champion_id=86))
    assert status == 204


def test_hover_action_propagates_4xx_status() -> None:
    """4xx (e.g. 409 'action already completed') must surface so the
    caller can show a useful status-bar message rather than claim
    success."""
    client = _RecordingClient(response=_FakeResponse(status_code=409))
    status = asyncio.run(hover_action(client, action_id=99, champion_id=86))
    assert status == 409


def test_commit_action_sends_completed_true() -> None:
    """Single-click direct lock — completed: true makes the pick/ban
    final without a separate confirmation step. User preference."""
    client = _RecordingClient()
    asyncio.run(commit_action(client, action_id=7, champion_id=122))
    body = client.calls[0][1]
    assert body == {"championId": 122, "completed": True}


def test_commit_action_uses_actions_endpoint() -> None:
    client = _RecordingClient()
    asyncio.run(commit_action(client, action_id=42, champion_id=86))
    assert client.calls[0][0] == "/lol-champ-select/v1/session/actions/42"
