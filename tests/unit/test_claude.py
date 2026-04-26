"""Tests for ClaudeAdvisor — caching, circuit breaker, error mapping.

Uses a fake Anthropic client so we don't hit the network.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import anthropic
import httpx
import pytest

from champ_assistant.advisor.claude import (
    CircuitBreakerOpen,
    ClaudeAdvisor,
    ClaudeApiError,
)


# --- Fakes ----------------------------------------------------------------


class _Block:
    type = "text"

    def __init__(self, text: str) -> None:
        self.text = text


class _Response:
    def __init__(self, text: str) -> None:
        self.content = [_Block(text)]


def _make_request_object() -> httpx.Request:
    """Build a real httpx Request — the anthropic SDK's APIError requires one."""
    return httpx.Request("POST", "https://api.anthropic.com/v1/messages")


class FakeMessages:
    def __init__(self, behaviors: list[Any]) -> None:
        self.behaviors = list(behaviors)
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if not self.behaviors:
            return _Response("default explanation")
        b = self.behaviors.pop(0)
        if isinstance(b, BaseException):
            raise b
        if callable(b):
            return b(**kwargs)
        return b


class FakeAnthropicClient:
    """Mimics anthropic.AsyncAnthropic for tests — only .messages.create + close."""

    def __init__(self, behaviors: list[Any] | None = None) -> None:
        self.messages = FakeMessages(behaviors or [])
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class ManualClock:
    """Controllable clock for circuit-breaker timing tests."""

    def __init__(self, t: float = 0.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


# --- Fixtures -------------------------------------------------------------


@pytest.fixture
def fake_client() -> FakeAnthropicClient:
    return FakeAnthropicClient([_Response("Lane Phase: trade hard...")])


@pytest.fixture
def advisor(tmp_path: Path, fake_client: FakeAnthropicClient) -> ClaudeAdvisor:
    return ClaudeAdvisor(
        tmp_path / "cache",
        client=fake_client,
        cooldown_seconds=300.0,
        failure_threshold=3,
    )


# --- Happy path ------------------------------------------------------------


@pytest.mark.asyncio
async def test_returns_explanation(
    advisor: ClaudeAdvisor, fake_client: FakeAnthropicClient
) -> None:
    text = await advisor.explain_matchup("Garen", "Darius", "TOP", patch="14.8")
    assert "Lane Phase" in text
    assert len(fake_client.messages.calls) == 1


@pytest.mark.asyncio
async def test_request_uses_haiku_model(
    advisor: ClaudeAdvisor, fake_client: FakeAnthropicClient
) -> None:
    await advisor.explain_matchup("Garen", "Darius", "TOP")
    call = fake_client.messages.calls[0]
    assert call["model"] == "claude-haiku-4-5"
    assert call["max_tokens"] == 512


@pytest.mark.asyncio
async def test_system_prompt_has_cache_control(
    advisor: ClaudeAdvisor, fake_client: FakeAnthropicClient
) -> None:
    """Anthropic prompt caching marker is set, even if it no-ops on short prompts."""
    await advisor.explain_matchup("Garen", "Darius", "TOP")
    system = fake_client.messages.calls[0]["system"]
    assert isinstance(system, list)
    assert system[0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}


@pytest.mark.asyncio
async def test_user_message_includes_matchup_context(
    advisor: ClaudeAdvisor, fake_client: FakeAnthropicClient
) -> None:
    await advisor.explain_matchup("Garen", "Darius", "TOP", patch="14.8")
    msg = fake_client.messages.calls[0]["messages"][0]["content"]
    assert "Darius" in msg
    assert "Garen" in msg
    assert "TOP" in msg
    assert "14.8" in msg


# --- Caching --------------------------------------------------------------


@pytest.mark.asyncio
async def test_identical_query_hits_cache(
    advisor: ClaudeAdvisor, fake_client: FakeAnthropicClient
) -> None:
    await advisor.explain_matchup("Garen", "Darius", "TOP", patch="14.8")
    await advisor.explain_matchup("Garen", "Darius", "TOP", patch="14.8")
    await advisor.explain_matchup("Garen", "Darius", "TOP", patch="14.8")
    assert len(fake_client.messages.calls) == 1


@pytest.mark.asyncio
async def test_different_patch_misses_cache(tmp_path: Path) -> None:
    fake = FakeAnthropicClient(
        [_Response("explanation 14.8"), _Response("explanation 14.9")]
    )
    a = ClaudeAdvisor(tmp_path / "cache", client=fake)
    out1 = await a.explain_matchup("Garen", "Darius", "TOP", patch="14.8")
    out2 = await a.explain_matchup("Garen", "Darius", "TOP", patch="14.9")
    assert out1 != out2
    assert len(fake.messages.calls) == 2


@pytest.mark.asyncio
async def test_different_role_misses_cache(tmp_path: Path) -> None:
    fake = FakeAnthropicClient(
        [_Response("top text"), _Response("mid text")]
    )
    a = ClaudeAdvisor(tmp_path / "cache", client=fake)
    await a.explain_matchup("Yasuo", "Annie", "TOP")
    await a.explain_matchup("Yasuo", "Annie", "MID")
    assert len(fake.messages.calls) == 2


@pytest.mark.asyncio
async def test_cache_persists_across_advisor_instances(tmp_path: Path) -> None:
    fake1 = FakeAnthropicClient([_Response("first call")])
    a1 = ClaudeAdvisor(tmp_path / "cache", client=fake1)
    out1 = await a1.explain_matchup("Garen", "Darius", "TOP", patch="14.8")
    await a1.aclose()

    fake2 = FakeAnthropicClient([_Response("never used")])
    a2 = ClaudeAdvisor(tmp_path / "cache", client=fake2)
    out2 = await a2.explain_matchup("Garen", "Darius", "TOP", patch="14.8")
    assert out1 == out2
    assert len(fake2.messages.calls) == 0


# --- Error mapping --------------------------------------------------------


@pytest.mark.asyncio
async def test_rate_limit_error_wraps_to_claude_api_error(tmp_path: Path) -> None:
    fake = FakeAnthropicClient(
        [
            anthropic.RateLimitError(
                message="429 too many",
                response=httpx.Response(429, request=_make_request_object()),
                body=None,
            )
        ]
    )
    a = ClaudeAdvisor(tmp_path / "cache", client=fake)
    with pytest.raises(ClaudeApiError) as excinfo:
        await a.explain_matchup("Garen", "Darius", "TOP")
    assert isinstance(excinfo.value.__cause__, anthropic.RateLimitError)


@pytest.mark.asyncio
async def test_connection_error_wraps_to_claude_api_error(tmp_path: Path) -> None:
    fake = FakeAnthropicClient(
        [anthropic.APIConnectionError(request=_make_request_object())]
    )
    a = ClaudeAdvisor(tmp_path / "cache", client=fake)
    with pytest.raises(ClaudeApiError):
        await a.explain_matchup("Garen", "Darius", "TOP")


@pytest.mark.asyncio
async def test_unexpected_error_wraps_to_claude_api_error(tmp_path: Path) -> None:
    fake = FakeAnthropicClient([RuntimeError("kaboom")])
    a = ClaudeAdvisor(tmp_path / "cache", client=fake)
    with pytest.raises(ClaudeApiError):
        await a.explain_matchup("Garen", "Darius", "TOP")


@pytest.mark.asyncio
async def test_empty_response_raises_and_does_not_cache(tmp_path: Path) -> None:
    fake = FakeAnthropicClient([_Response("   "), _Response("real text")])
    a = ClaudeAdvisor(tmp_path / "cache", client=fake, failure_threshold=10)
    with pytest.raises(ClaudeApiError):
        await a.explain_matchup("Garen", "Darius", "TOP")
    # Empty result was not cached → next call hits the API again.
    out = await a.explain_matchup("Garen", "Darius", "TOP")
    assert out == "real text"


@pytest.mark.asyncio
async def test_blank_inputs_rejected(advisor: ClaudeAdvisor) -> None:
    with pytest.raises(ValueError):
        await advisor.explain_matchup("", "Darius", "TOP")
    with pytest.raises(ValueError):
        await advisor.explain_matchup("Garen", "", "TOP")
    with pytest.raises(ValueError):
        await advisor.explain_matchup("Garen", "Darius", "")


# --- Circuit breaker ------------------------------------------------------


@pytest.mark.asyncio
async def test_three_consecutive_failures_open_breaker(tmp_path: Path) -> None:
    fake = FakeAnthropicClient(
        [
            anthropic.APIConnectionError(request=_make_request_object()),
            anthropic.APIConnectionError(request=_make_request_object()),
            anthropic.APIConnectionError(request=_make_request_object()),
        ]
    )
    clock = ManualClock()
    a = ClaudeAdvisor(
        tmp_path / "cache",
        client=fake,
        clock=clock,
        failure_threshold=3,
        cooldown_seconds=300.0,
    )

    for i in range(3):
        with pytest.raises(ClaudeApiError):
            await a.explain_matchup("Garen", f"E{i}", "TOP")

    # Fourth call should NOT hit the API — breaker is open.
    fake.messages.behaviors = [_Response("would not reach here")]
    with pytest.raises(CircuitBreakerOpen):
        await a.explain_matchup("Garen", "E4", "TOP")
    assert len(fake.messages.calls) == 3  # only the first 3 attempts


@pytest.mark.asyncio
async def test_breaker_resets_after_cooldown(tmp_path: Path) -> None:
    fake = FakeAnthropicClient(
        [
            anthropic.APIConnectionError(request=_make_request_object()),
            anthropic.APIConnectionError(request=_make_request_object()),
            anthropic.APIConnectionError(request=_make_request_object()),
            _Response("ok again"),
        ]
    )
    clock = ManualClock()
    a = ClaudeAdvisor(
        tmp_path / "cache",
        client=fake,
        clock=clock,
        failure_threshold=3,
        cooldown_seconds=300.0,
    )
    for i in range(3):
        with pytest.raises(ClaudeApiError):
            await a.explain_matchup("Garen", f"E{i}", "TOP")

    # Still within cooldown → breaker open
    with pytest.raises(CircuitBreakerOpen):
        await a.explain_matchup("Garen", "E", "TOP")

    # Advance past cooldown → next call should retry the API
    clock.advance(301.0)
    text = await a.explain_matchup("Garen", "E", "TOP")
    assert text == "ok again"


@pytest.mark.asyncio
async def test_success_resets_consecutive_failures(tmp_path: Path) -> None:
    fake = FakeAnthropicClient(
        [
            anthropic.APIConnectionError(request=_make_request_object()),
            anthropic.APIConnectionError(request=_make_request_object()),
            _Response("recovered"),
            anthropic.APIConnectionError(request=_make_request_object()),
            anthropic.APIConnectionError(request=_make_request_object()),
        ]
    )
    a = ClaudeAdvisor(
        tmp_path / "cache",
        client=fake,
        failure_threshold=3,
    )
    for i in range(2):
        with pytest.raises(ClaudeApiError):
            await a.explain_matchup("Garen", f"F{i}", "TOP")
    out = await a.explain_matchup("Garen", "OK", "TOP")
    assert out == "recovered"
    # Counter has reset; need 3 more failures to open the breaker.
    for i in range(2):
        with pytest.raises(ClaudeApiError):
            await a.explain_matchup("Garen", f"X{i}", "TOP")
    assert a.breaker_state.opened_at is None


@pytest.mark.asyncio
async def test_cache_hit_skips_breaker_check(tmp_path: Path) -> None:
    """A cached response is returned even if the breaker is open."""
    fake = FakeAnthropicClient([_Response("cached text")])
    a = ClaudeAdvisor(tmp_path / "cache", client=fake, failure_threshold=1)
    # First call populates cache successfully.
    await a.explain_matchup("Garen", "Darius", "TOP")
    # Force-open the breaker.
    a.breaker_state.consecutive_failures = 5
    a.breaker_state.opened_at = a._now()  # type: ignore[attr-defined]
    # Cached call should still succeed.
    out = await a.explain_matchup("Garen", "Darius", "TOP")
    assert out == "cached text"
