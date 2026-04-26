"""Tests for LcuClient — auth, retries, timeouts, error mapping."""
from __future__ import annotations

import base64

import httpx
import pytest
import respx

from champ_assistant.lcu.client import LcuClient, LcuClientError
from champ_assistant.lcu.lockfile import LockfileInfo

LOCKFILE = LockfileInfo(
    process_name="LeagueClient",
    pid=1234,
    port=64144,
    password="abc123",
    protocol="https",
)
BASE = "https://127.0.0.1:64144"


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@respx.mock
async def test_get_returns_response() -> None:
    route = respx.get(f"{BASE}/lol-summoner/v1/current-summoner").mock(
        return_value=httpx.Response(200, json={"name": "Dennis"})
    )
    async with LcuClient(LOCKFILE) as client:
        response = await client.get("/lol-summoner/v1/current-summoner")
    assert response.status_code == 200
    assert response.json() == {"name": "Dennis"}
    assert route.called


@pytest.mark.asyncio
@respx.mock
async def test_request_includes_basic_auth_header() -> None:
    route = respx.get(f"{BASE}/x").mock(return_value=httpx.Response(200))
    async with LcuClient(LOCKFILE) as client:
        await client.get("/x")
    auth_header = route.calls.last.request.headers["authorization"]
    assert auth_header.startswith("Basic ")
    decoded = base64.b64decode(auth_header.split(" ", 1)[1]).decode()
    assert decoded == "riot:abc123"


@pytest.mark.asyncio
@respx.mock
async def test_post_with_json_body() -> None:
    route = respx.post(f"{BASE}/lol-champ-select/v1/session/actions/1").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    async with LcuClient(LOCKFILE) as client:
        response = await client.post(
            "/lol-champ-select/v1/session/actions/1",
            json={"championId": 7},
        )
    assert response.status_code == 200
    body = route.calls.last.request.content.decode()
    assert "championId" in body


# ---------------------------------------------------------------------------
# Retry on transient errors
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@respx.mock
async def test_retries_on_timeout_then_succeeds() -> None:
    respx.get(f"{BASE}/x").mock(
        side_effect=[
            httpx.TimeoutException("first"),
            httpx.Response(200, json={"ok": True}),
        ]
    )
    async with LcuClient(LOCKFILE, backoff_base=0.0) as client:
        response = await client.get("/x")
    assert response.status_code == 200


@pytest.mark.asyncio
@respx.mock
async def test_retries_on_network_error_then_succeeds() -> None:
    respx.get(f"{BASE}/x").mock(
        side_effect=[
            httpx.ConnectError("conn-refused"),
            httpx.Response(200),
        ]
    )
    async with LcuClient(LOCKFILE, backoff_base=0.0) as client:
        response = await client.get("/x")
    assert response.status_code == 200


@pytest.mark.asyncio
@respx.mock
async def test_retries_on_5xx_then_succeeds() -> None:
    respx.get(f"{BASE}/x").mock(
        side_effect=[
            httpx.Response(503),
            httpx.Response(200),
        ]
    )
    async with LcuClient(LOCKFILE, backoff_base=0.0) as client:
        response = await client.get("/x")
    assert response.status_code == 200


@pytest.mark.asyncio
@respx.mock
async def test_does_not_retry_4xx() -> None:
    route = respx.get(f"{BASE}/x").mock(return_value=httpx.Response(404))
    async with LcuClient(LOCKFILE, backoff_base=0.0) as client:
        response = await client.get("/x")
    assert response.status_code == 404
    assert route.call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_persistent_timeout_raises_after_retries() -> None:
    route = respx.get(f"{BASE}/x").mock(side_effect=httpx.TimeoutException("nope"))
    async with LcuClient(LOCKFILE, max_retries=3, backoff_base=0.0) as client:
        with pytest.raises(LcuClientError) as excinfo:
            await client.get("/x")
    assert "3 attempts" in str(excinfo.value)
    assert isinstance(excinfo.value.__cause__, httpx.TimeoutException)
    assert route.call_count == 3


@pytest.mark.asyncio
@respx.mock
async def test_persistent_5xx_raises_after_retries() -> None:
    respx.get(f"{BASE}/x").mock(return_value=httpx.Response(500))
    async with LcuClient(LOCKFILE, max_retries=2, backoff_base=0.0) as client:
        with pytest.raises(LcuClientError):
            await client.get("/x")


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_request_outside_context_manager_raises() -> None:
    client = LcuClient(LOCKFILE)
    with pytest.raises(RuntimeError, match="async context manager"):
        await client.get("/x")


@pytest.mark.asyncio
@respx.mock
async def test_context_manager_closes_client() -> None:
    respx.get(f"{BASE}/x").mock(return_value=httpx.Response(200))
    client = LcuClient(LOCKFILE)
    async with client:
        await client.get("/x")
    assert client._client is None


# ---------------------------------------------------------------------------
# Configuration knobs
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@respx.mock
async def test_custom_max_retries_respected() -> None:
    route = respx.get(f"{BASE}/x").mock(side_effect=httpx.TimeoutException("nope"))
    async with LcuClient(LOCKFILE, max_retries=5, backoff_base=0.0) as client:
        with pytest.raises(LcuClientError):
            await client.get("/x")
    assert route.call_count == 5


@pytest.mark.asyncio
@respx.mock
async def test_request_passes_query_params() -> None:
    route = respx.get(f"{BASE}/x").mock(return_value=httpx.Response(200))
    async with LcuClient(LOCKFILE) as client:
        await client.get("/x", params={"foo": "bar"})
    assert route.calls.last.request.url.params["foo"] == "bar"


@pytest.mark.asyncio
@respx.mock
async def test_patch_method_works() -> None:
    route = respx.patch(f"{BASE}/x").mock(return_value=httpx.Response(204))
    async with LcuClient(LOCKFILE) as client:
        response = await client.patch("/x", json={"a": 1})
    assert response.status_code == 204
    assert route.called
