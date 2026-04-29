"""Crash prevention layer.

Goals:
  - Catch *all* unhandled exceptions (sync via ``sys.excepthook``,
    async via ``loop.set_exception_handler``).
  - Log them with a full traceback.
  - Notify subscribers (the UI later wires a Qt signal here) so the user sees
    a toast instead of a silent failure.
  - **Never** call ``sys.exit`` — the app must keep running.

Decoupled from Qt on purpose: ``safety`` is testable without ``pytest-qt``,
and the UI module subscribes a Qt-signal emitter via :meth:`CrashHandler.subscribe`.
"""
from __future__ import annotations

import asyncio
import logging
import sys
import traceback
from collections.abc import Callable
from types import TracebackType
from typing import Any

ExceptHook = Callable[[type[BaseException], BaseException, TracebackType | None], None]
Subscriber = Callable[[str], None]

logger = logging.getLogger(__name__)


class CrashHandler:
    """Global sink for sync + asyncio uncaught exceptions."""

    def __init__(self) -> None:
        self._subscribers: list[Subscriber] = []
        self._original_excepthook: ExceptHook | None = None
        self._installed_loop: asyncio.AbstractEventLoop | None = None
        self._original_async_handler: Callable[..., Any] | None = None
        # Pre-bind the methods so identity comparisons (`is`) work after install.
        # `self._handle` would otherwise produce a fresh bound-method on every access.
        self._sync_hook: ExceptHook = self._handle
        self._async_hook: Callable[[asyncio.AbstractEventLoop, dict[str, Any]], None] = (
            self._handle_async
        )
        # Optional crash-report writer — wired by ``__main__`` after the
        # uptime clock + state collectors are available. Stored as a
        # plain callable so this module stays Qt-free + dep-free.
        self._on_uncaught: Callable[
            [type[BaseException], BaseException, TracebackType | None], None,
        ] | None = None

    def set_uncaught_callback(
        self,
        callback: Callable[
            [type[BaseException], BaseException, TracebackType | None], None,
        ] | None,
    ) -> None:
        """Hand over a callable that fires on every (non-KeyboardInterrupt)
        uncaught exception — both sync and async paths converge here.
        Idempotent; passing ``None`` unhooks."""
        self._on_uncaught = callback

    # -- Subscription --------------------------------------------------

    def subscribe(self, callback: Subscriber) -> None:
        self._subscribers.append(callback)

    def unsubscribe(self, callback: Subscriber) -> None:
        try:
            self._subscribers.remove(callback)
        except ValueError:
            pass

    # -- Lifecycle -----------------------------------------------------

    def install(self, loop: asyncio.AbstractEventLoop | None = None) -> None:
        """Install global hooks. Call once at startup, ideally with the qasync loop."""
        self._original_excepthook = sys.excepthook
        sys.excepthook = self._sync_hook

        if loop is not None:
            self._original_async_handler = loop.get_exception_handler()
            loop.set_exception_handler(self._async_hook)
            self._installed_loop = loop

    def uninstall(self) -> None:
        if self._original_excepthook is not None:
            sys.excepthook = self._original_excepthook
            self._original_excepthook = None
        if self._installed_loop is not None:
            self._installed_loop.set_exception_handler(self._original_async_handler)
            self._installed_loop = None
            self._original_async_handler = None

    # -- Handlers ------------------------------------------------------

    def _handle(
        self,
        exc_type: type[BaseException],
        exc_value: BaseException,
        exc_tb: TracebackType | None,
    ) -> None:
        # KeyboardInterrupt should still propagate so users can Ctrl+C.
        if issubclass(exc_type, KeyboardInterrupt):
            if self._original_excepthook is not None:
                self._original_excepthook(exc_type, exc_value, exc_tb)
            return

        trace = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        logger.error("uncaught_exception", extra={"trace": trace})
        self._fire_uncaught(exc_type, exc_value, exc_tb)
        self._notify(str(exc_value) or exc_type.__name__)
        # Deliberately NOT calling sys.exit — graceful degradation only.

    def _handle_async(
        self,
        loop: asyncio.AbstractEventLoop,
        context: dict[str, Any],
    ) -> None:
        # Note: avoid the keys "message" and "asctime" in `extra` — stdlib
        # logging reserves those on LogRecord and raises KeyError on collision.
        detail = context.get("message", "Async error")
        exc = context.get("exception")
        if exc is not None:
            trace = "".join(
                traceback.format_exception(type(exc), exc, exc.__traceback__)
            )
            logger.error("async_exception", extra={"detail": detail, "trace": trace})
            self._fire_uncaught(type(exc), exc, exc.__traceback__)
        else:
            logger.error("async_exception", extra={"detail": detail, "context": str(context)})
        self._notify(str(exc) if exc else detail)

    def _fire_uncaught(
        self,
        exc_type: type[BaseException],
        exc_value: BaseException,
        exc_tb: TracebackType | None,
    ) -> None:
        """Fan-out to the registered uncaught callback (crash_report
        writer in production). Failures here are isolated — a broken
        crash writer must not break the crash handler itself."""
        cb = self._on_uncaught
        if cb is None:
            return
        try:
            cb(exc_type, exc_value, exc_tb)
        except Exception:  # noqa: BLE001
            logger.exception("safety: uncaught_callback raised — ignoring")

    # -- Notification --------------------------------------------------

    def _notify(self, message: str) -> None:
        for cb in list(self._subscribers):
            try:
                cb(message)
            except Exception:
                # A misbehaving subscriber must not break the crash handler itself.
                logger.exception("crash_subscriber_failed")
