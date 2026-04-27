"""LCU REST client.

Wraps ``httpx.AsyncClient`` with the auth + TLS settings the local League
client requires (masterplan §6 / §7):

  - HTTP Basic ("riot" : <lockfile password>)
  - ``verify=False`` — Riot uses a self-signed cert on 127.0.0.1; this is safe
    *only* because the host is loopback
  - Default 5s timeout per masterplan §4.5
  - 3 retries with exponential backoff for transient errors (timeout, network,
    5xx). 4xx is *not* retried — it indicates a programming error.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from .lockfile import LockfileInfo

logger = logging.getLogger(__name__)


class LcuClientError(Exception):
    """Raised when an LCU request fails after all retries."""


class LcuClient:
    DEFAULT_TIMEOUT = 5.0
    DEFAULT_MAX_RETRIES = 3
    DEFAULT_BACKOFF_BASE = 0.25

    def __init__(
        self,
        lockfile: LockfileInfo,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
        backoff_base: float = DEFAULT_BACKOFF_BASE,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.lockfile = lockfile
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self._transport = transport
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> LcuClient:
        kwargs: dict[str, Any] = dict(
            base_url=self.lockfile.base_url,
            auth=self.lockfile.auth,
            verify=False,  # noqa: S501 — required for LCU's self-signed loopback cert
            timeout=self.timeout,
        )
        if self._transport is not None:
            kwargs["transport"] = self._transport
        self._client = httpx.AsyncClient(**kwargs)
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def request(
        self,
        method: str,
        path: str,
        *,
        json: Any | None = None,
        params: dict[str, Any] | None = None,
    ) -> httpx.Response:
        if self._client is None:
            raise RuntimeError("LcuClient must be used as an async context manager")

        last_exc: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                response = await self._client.request(method, path, json=json, params=params)
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                last_exc = exc
                logger.warning(
                    "lcu_request_retry",
                    extra={
                        "method": method,
                        "path": path,
                        "attempt": attempt + 1,
                        "error_type": type(exc).__name__,
                    },
                )
            else:
                # 5xx is retried; 4xx (and 2xx/3xx) are returned directly.
                if 500 <= response.status_code < 600:
                    last_exc = httpx.HTTPStatusError(
                        f"server error {response.status_code}",
                        request=response.request,
                        response=response,
                    )
                    logger.warning(
                        "lcu_request_5xx_retry",
                        extra={
                            "method": method,
                            "path": path,
                            "attempt": attempt + 1,
                            "status": response.status_code,
                        },
                    )
                else:
                    return response

            if attempt < self.max_retries - 1:
                await asyncio.sleep(self.backoff_base * (2**attempt))

        assert last_exc is not None
        raise LcuClientError(
            f"LCU {method} {path} failed after {self.max_retries} attempts"
        ) from last_exc

    async def get(self, path: str, **kwargs: Any) -> httpx.Response:
        return await self.request("GET", path, **kwargs)

    async def post(self, path: str, **kwargs: Any) -> httpx.Response:
        return await self.request("POST", path, **kwargs)

    async def patch(self, path: str, **kwargs: Any) -> httpx.Response:
        return await self.request("PATCH", path, **kwargs)

    async def put(self, path: str, **kwargs: Any) -> httpx.Response:
        return await self.request("PUT", path, **kwargs)

    async def delete(self, path: str, **kwargs: Any) -> httpx.Response:
        return await self.request("DELETE", path, **kwargs)
