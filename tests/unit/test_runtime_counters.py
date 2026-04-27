"""Tests for the Groq-backed RuntimeCounterStore."""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from champ_assistant.data.runtime_counters import (
    GROQ_API_URL,
    RuntimeCounterStore,
)

VALID_PAYLOAD = {
    "choices": [
        {
            "message": {
                "content": json.dumps(
                    {
                        "counters": [
                            {"champion": "Darius", "score": 8.0, "tier": "S"},
                            {"champion": "Vayne", "score": 7.0, "tier": "A"},
                            {"champion": "Quinn", "score": 6.5, "tier": "A"},
                        ]
                    }
                )
            }
        }
    ]
}


@pytest.mark.asyncio
@respx.mock
async def test_get_returns_parsed_counters(tmp_path: Path) -> None:
    respx.post(GROQ_API_URL).mock(return_value=httpx.Response(200, json=VALID_PAYLOAD))
    store = RuntimeCounterStore(tmp_path / "cache", api_key="test-key")
    counters = await store.get("Garen", "TOP")
    assert [c.champion for c in counters] == ["Darius", "Vayne", "Quinn"]
    assert counters[0].score == 8.0
    await store.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_get_uses_cache_on_second_call(tmp_path: Path) -> None:
    route = respx.post(GROQ_API_URL).mock(return_value=httpx.Response(200, json=VALID_PAYLOAD))
    store = RuntimeCounterStore(tmp_path / "cache", api_key="test-key")
    await store.get("Garen", "TOP")
    await store.get("Garen", "TOP")
    await store.get("Garen", "TOP")
    assert route.call_count == 1
    await store.aclose()


@pytest.mark.asyncio
async def test_disabled_when_no_key(tmp_path: Path) -> None:
    store = RuntimeCounterStore(tmp_path / "cache", api_key="")
    assert store.enabled is False
    counters = await store.get("Garen", "TOP")
    assert counters == []
    await store.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_authorization_header_set(tmp_path: Path) -> None:
    route = respx.post(GROQ_API_URL).mock(return_value=httpx.Response(200, json=VALID_PAYLOAD))
    store = RuntimeCounterStore(tmp_path / "cache", api_key="my-test-key")
    await store.get("Garen", "TOP")
    auth = route.calls.last.request.headers["authorization"]
    assert auth == "Bearer my-test-key"
    await store.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_5xx_returns_empty_does_not_cache(tmp_path: Path) -> None:
    respx.post(GROQ_API_URL).mock(
        side_effect=[
            httpx.Response(500),
            httpx.Response(200, json=VALID_PAYLOAD),
        ]
    )
    store = RuntimeCounterStore(tmp_path / "cache", api_key="test-key")
    first = await store.get("Garen", "TOP")
    assert first == []
    second = await store.get("Garen", "TOP")
    assert len(second) == 3  # second call hits the API again
    await store.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_invalid_json_in_content_returns_empty(tmp_path: Path) -> None:
    bad = {"choices": [{"message": {"content": "not json at all"}}]}
    respx.post(GROQ_API_URL).mock(return_value=httpx.Response(200, json=bad))
    store = RuntimeCounterStore(tmp_path / "cache", api_key="test-key")
    counters = await store.get("Garen", "TOP")
    assert counters == []
    await store.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_strips_markdown_fences(tmp_path: Path) -> None:
    fenced = {
        "choices": [
            {"message": {"content": "```json\n" + json.dumps({"counters": [
                {"champion": "Darius", "score": 8, "tier": "S"}
            ]}) + "\n```"}}
        ]
    }
    respx.post(GROQ_API_URL).mock(return_value=httpx.Response(200, json=fenced))
    store = RuntimeCounterStore(tmp_path / "cache", api_key="test-key")
    counters = await store.get("Garen", "TOP")
    assert len(counters) == 1
    await store.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_concurrent_calls_dedup(tmp_path: Path) -> None:
    import asyncio
    route = respx.post(GROQ_API_URL).mock(return_value=httpx.Response(200, json=VALID_PAYLOAD))
    store = RuntimeCounterStore(tmp_path / "cache", api_key="test-key")
    a, b, c = await asyncio.gather(
        store.get("Garen", "TOP"),
        store.get("Garen", "TOP"),
        store.get("Garen", "TOP"),
    )
    assert a == b == c
    assert route.call_count == 1
    await store.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_cache_persists_across_instances(tmp_path: Path) -> None:
    respx.post(GROQ_API_URL).mock(return_value=httpx.Response(200, json=VALID_PAYLOAD))
    s1 = RuntimeCounterStore(tmp_path / "cache", api_key="test-key")
    await s1.get("Garen", "TOP")
    await s1.aclose()

    respx.post(GROQ_API_URL).mock(return_value=httpx.Response(200, json={"choices": []}))
    s2 = RuntimeCounterStore(tmp_path / "cache", api_key="test-key")
    counters = await s2.get("Garen", "TOP")
    assert len(counters) == 3
    await s2.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_set_patch_invalidates_cache_namespace(tmp_path: Path) -> None:
    """When Data Dragon reports a new patch, old entries become unreachable
    via the cache key (they live on disk but the next get() builds a new key
    that doesn't match)."""
    respx.post(GROQ_API_URL).mock(return_value=httpx.Response(200, json=VALID_PAYLOAD))
    store = RuntimeCounterStore(tmp_path / "cache", api_key="test-key", patch="14.8")
    counters = await store.get("Garen", "TOP")
    assert len(counters) == 3
    # New patch ships → switch the namespace → old key no longer matches.
    store.set_patch("14.9")
    assert store.get_cached("Garen", "TOP") is None
    # And switching back to 14.8 finds the old entry again.
    store.set_patch("14.8")
    assert store.get_cached("Garen", "TOP") is not None
    await store.aclose()


@pytest.mark.asyncio
async def test_get_cached_does_not_hit_network(tmp_path: Path) -> None:
    """Pre-warm cache then verify get_cached() returns it without a key."""
    pre = RuntimeCounterStore(tmp_path / "cache", api_key="x")
    pre.cache.set(
        pre._cache_key("Garen", "TOP"),
        [],  # empty placeholder — exercise the sync read
    )
    pre.cache.close()

    store = RuntimeCounterStore(tmp_path / "cache", api_key="")  # no key, disabled
    cached = store.get_cached("Garen", "TOP")
    assert cached == []
    await store.aclose()
