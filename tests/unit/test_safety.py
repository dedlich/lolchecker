"""Tests for CrashHandler — ensure it logs, notifies, and never sys.exit's."""
from __future__ import annotations

import asyncio
import logging
import sys
from collections.abc import Iterator

import pytest

from champ_assistant.safety import CrashHandler


@pytest.fixture
def handler() -> Iterator[CrashHandler]:
    h = CrashHandler()
    yield h
    h.uninstall()


def _trigger_sync(h: CrashHandler, exc: BaseException) -> None:
    try:
        raise exc
    except BaseException:
        exc_type, exc_value, exc_tb = sys.exc_info()
        assert exc_type is not None
        assert exc_value is not None
        h._handle(exc_type, exc_value, exc_tb)


# ---------------------------------------------------------------------------
# Sync exception path
# ---------------------------------------------------------------------------

def test_handle_notifies_subscribers(handler: CrashHandler) -> None:
    received: list[str] = []
    handler.subscribe(received.append)

    _trigger_sync(handler, RuntimeError("boom"))

    assert received == ["boom"]


def test_handle_does_not_call_sys_exit(
    handler: CrashHandler, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_exit(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("sys.exit must not be called")

    monkeypatch.setattr(sys, "exit", fail_exit)
    _trigger_sync(handler, ValueError("nope"))


def test_handle_logs_traceback(
    handler: CrashHandler, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.ERROR, logger="champ_assistant.safety"):
        _trigger_sync(handler, RuntimeError("kaputt"))
    assert any(rec.message == "uncaught_exception" for rec in caplog.records)


def test_subscriber_exception_does_not_break_handler(handler: CrashHandler) -> None:
    received: list[str] = []
    handler.subscribe(lambda _msg: (_ for _ in ()).throw(RuntimeError("subscriber-fail")))
    handler.subscribe(received.append)

    _trigger_sync(handler, RuntimeError("primary"))

    assert received == ["primary"]


def test_unsubscribe_stops_notifications(handler: CrashHandler) -> None:
    received: list[str] = []
    handler.subscribe(received.append)
    handler.unsubscribe(received.append)

    _trigger_sync(handler, RuntimeError("ignored"))
    assert received == []


def test_keyboard_interrupt_propagates_to_original(handler: CrashHandler) -> None:
    seen: list[type[BaseException]] = []

    def fake_excepthook(exc_type, _value, _tb):  # type: ignore[no-untyped-def]
        seen.append(exc_type)

    sys.excepthook = fake_excepthook
    handler.install()
    _trigger_sync(handler, KeyboardInterrupt())
    assert seen == [KeyboardInterrupt]


# ---------------------------------------------------------------------------
# Install / uninstall
# ---------------------------------------------------------------------------

def test_install_replaces_excepthook_and_uninstall_restores(handler: CrashHandler) -> None:
    original = sys.excepthook
    handler.install()
    assert sys.excepthook is handler._sync_hook
    handler.uninstall()
    assert sys.excepthook is original


# ---------------------------------------------------------------------------
# Async path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_handle_async_notifies_with_exception() -> None:
    handler = CrashHandler()
    received: list[str] = []
    handler.subscribe(received.append)
    loop = asyncio.get_running_loop()

    handler._handle_async(loop, {"message": "manual", "exception": RuntimeError("boom")})
    assert received == ["boom"]


@pytest.mark.asyncio
async def test_handle_async_without_exception_uses_message() -> None:
    handler = CrashHandler()
    received: list[str] = []
    handler.subscribe(received.append)
    loop = asyncio.get_running_loop()

    handler._handle_async(loop, {"message": "loop-warning"})
    assert received == ["loop-warning"]


@pytest.mark.asyncio
async def test_install_sets_async_handler_and_uninstall_restores() -> None:
    handler = CrashHandler()
    loop = asyncio.get_running_loop()
    original = loop.get_exception_handler()
    handler.install(loop=loop)
    assert loop.get_exception_handler() is handler._async_hook
    handler.uninstall()
    assert loop.get_exception_handler() is original
