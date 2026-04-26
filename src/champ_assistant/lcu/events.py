"""LCU WebSocket event stream.

The LCU exposes a WAMP-flavoured JSON WebSocket on the same host:port as
REST. We send subscribe frames and yield matching server events.

Frame format (JSON arrays):
    [5, "<topic>"]              SUBSCRIBE   (we send)
    [6, "<topic>"]              UNSUBSCRIBE (we send)
    [8, "<topic>", <payload>]   EVENT       (server sends)

Reconnect strategy (masterplan §4.3): exponential backoff capped at 30s,
no max-retry — the client may simply not be running. The websockets
library handles ping/pong keepalive natively (we configure ping_interval
to satisfy masterplan §4.2's "Sleep/Wake → WebSocket tot" mitigation).

The websocket connection is built by an injectable factory so unit tests
can swap in a fake without standing up a real server.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import ssl
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, Protocol

import websockets
from websockets.exceptions import ConnectionClosed

from .lockfile import LockfileInfo

logger = logging.getLogger(__name__)

OP_SUBSCRIBE = 5
OP_UNSUBSCRIBE = 6
OP_EVENT = 8


class WebSocketLike(Protocol):
    """Minimal interface we use from a websocket connection."""

    async def send(self, message: str) -> None: ...
    async def close(self, code: int = 1000, reason: str = "") -> None: ...
    def __aiter__(self) -> AsyncIterator[str | bytes]: ...


WsConnectFactory = Callable[[LockfileInfo], Awaitable[WebSocketLike]]


async def default_connect(lockfile: LockfileInfo) -> WebSocketLike:
    """Open a websocket connection to the running League client."""
    ssl_ctx: ssl.SSLContext | None
    if lockfile.protocol == "https":
        # LCU presents a self-signed cert on 127.0.0.1 — masterplan §7.
        ssl_ctx = ssl.create_default_context()
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE
        scheme = "wss"
    else:
        ssl_ctx = None
        scheme = "ws"

    user_pass = f"{lockfile.auth[0]}:{lockfile.auth[1]}".encode()
    auth_header = "Basic " + base64.b64encode(user_pass).decode()
    uri = f"{scheme}://127.0.0.1:{lockfile.port}/"

    return await websockets.connect(  # type: ignore[return-value]
        uri,
        ssl=ssl_ctx,
        additional_headers={"Authorization": auth_header},
        ping_interval=30,
        ping_timeout=10,
    )


class LcuEventStream:
    """Async-iterable stream of filtered LCU JSON events with reconnect."""

    RECONNECT_INITIAL = 1.0
    RECONNECT_MAX = 30.0
    RECONNECT_FACTOR = 1.5

    def __init__(
        self,
        lockfile: LockfileInfo,
        subscriptions: list[str],
        *,
        connect: WsConnectFactory | None = None,
        reconnect_initial: float = RECONNECT_INITIAL,
        reconnect_max: float = RECONNECT_MAX,
        reconnect_factor: float = RECONNECT_FACTOR,
    ) -> None:
        if not subscriptions:
            raise ValueError("At least one subscription topic is required")
        self.lockfile = lockfile
        self.subscriptions = list(subscriptions)
        self._connect_factory = connect or default_connect
        self._reconnect_initial = reconnect_initial
        self._reconnect_max = reconnect_max
        self._reconnect_factor = reconnect_factor
        self._closed = False
        self._ws: WebSocketLike | None = None

    @property
    def closed(self) -> bool:
        return self._closed

    async def __aenter__(self) -> LcuEventStream:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def close(self) -> None:
        self._closed = True
        ws, self._ws = self._ws, None
        if ws is not None:
            try:
                await ws.close()
            except Exception:
                logger.debug("ws_close_error_ignored")

    async def __aiter__(self) -> AsyncIterator[dict[str, Any]]:
        backoff = self._reconnect_initial
        while not self._closed:
            try:
                self._ws = await self._connect_factory(self.lockfile)
            except Exception as exc:
                logger.warning(
                    "ws_connect_failed",
                    extra={"error_type": type(exc).__name__, "backoff": backoff},
                )
                if not await self._sleep_unless_closed(backoff):
                    return
                backoff = min(backoff * self._reconnect_factor, self._reconnect_max)
                continue

            backoff = self._reconnect_initial  # success → reset
            try:
                for topic in self.subscriptions:
                    await self._ws.send(json.dumps([OP_SUBSCRIBE, topic]))

                async for raw in self._ws:
                    if self._closed:
                        break
                    event = self._parse(raw)
                    if event is not None:
                        yield event
            except ConnectionClosed:
                logger.info("ws_connection_closed")
            except Exception as exc:
                logger.warning("ws_loop_error", extra={"error_type": type(exc).__name__})
            finally:
                ws, self._ws = self._ws, None
                if ws is not None:
                    try:
                        await ws.close()
                    except Exception:
                        logger.debug("ws_close_error_ignored")

            if not self._closed:
                if not await self._sleep_unless_closed(backoff):
                    return
                backoff = min(backoff * self._reconnect_factor, self._reconnect_max)

    async def _sleep_unless_closed(self, seconds: float) -> bool:
        """Sleep ``seconds`` but bail early if close() was called. Returns False if closed.

        Always yields to the event loop at least once — otherwise a fast
        reconnect loop (backoff=0) starves the rest of the loop and tests hang.
        """
        try:
            await asyncio.sleep(max(seconds, 0))
        except asyncio.CancelledError:
            return False
        return not self._closed

    def _parse(self, raw: str | bytes) -> dict[str, Any] | None:
        if isinstance(raw, bytes):
            try:
                raw = raw.decode("utf-8")
            except UnicodeDecodeError:
                return None
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            logger.debug("ws_unparseable_message")
            return None
        if not isinstance(data, list) or len(data) < 3:
            return None
        opcode, topic, payload = data[0], data[1], data[2]
        if opcode != OP_EVENT:
            return None
        if topic not in self.subscriptions:
            return None
        if not isinstance(payload, dict):
            return None
        return {"topic": topic, "payload": payload}
