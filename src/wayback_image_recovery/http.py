"""HTTP client, rate limiter, and retry policy."""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Protocol

from wayback_image_recovery.errors import ResponseTooLarge, TransportError
from wayback_image_recovery.models import RecoveryConfig

logger = logging.getLogger(__name__)

RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
Sleep = Callable[[float], Awaitable[None]]
Uniform = Callable[[float, float], float]
Monotonic = Callable[[], float]


class HttpResponse:
    """Bytes returned by one HTTP request."""

    def __init__(
        self,
        status: int,
        body: bytes,
        headers: Mapping[str, str] | None = None,
        url: str = "",
    ) -> None:
        self.status = status
        self.body = body
        self.headers = {key.lower(): value for key, value in (headers or {}).items()}
        self.url = url

    def json(self) -> object:
        import json

        return json.loads(self.body.decode("utf-8"))


class HttpClient(Protocol):
    """Minimal client used by lookups and downloads."""

    async def request(
        self,
        method: str,
        url: str,
        *,
        params: Sequence[tuple[str, str]] | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: float | None = None,
    ) -> HttpResponse: ...

    async def aclose(self) -> None: ...


class RateLimiter:
    """Space out request *starts* so a run stays under a fixed rate."""

    def __init__(
        self,
        rate_per_second: float,
        *,
        monotonic: Monotonic = time.monotonic,
        sleep: Sleep | None = None,
    ) -> None:
        self._interval = 0.0 if rate_per_second <= 0 else 1.0 / rate_per_second
        self._monotonic = monotonic
        self._sleep = sleep or asyncio.sleep
        # Created on first use so Python 3.9 does not bind the lock to a closed loop.
        self._lock: asyncio.Lock | None = None
        self._next = 0.0

    async def acquire(self) -> None:
        if self._interval <= 0:
            return
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            now = self._monotonic()
            wait = self._next - now
            if wait > 0:
                await self._sleep(wait)
                now = self._monotonic()
            self._next = max(self._next, now) + self._interval


def compute_backoff(
    attempt: int,
    *,
    base: float = 0.5,
    cap: float = 30.0,
    jitter: float = 0.25,
    uniform: Uniform | None = None,
) -> float:
    """Exponential backoff with a small jitter term.

    ``attempt`` is zero-based. The delay is ``min(cap, base * 2**attempt)``
    plus a random fraction of that delay.
    """
    if attempt < 0:
        raise ValueError("attempt must be >= 0")
    roll = uniform or random.uniform
    delay = min(cap, base * (2**attempt))
    return delay + roll(0.0, delay * jitter)


def retry_after_seconds(response: HttpResponse) -> float | None:
    """Parse a numeric Retry-After header, capped at 60 seconds."""
    raw = response.headers.get("retry-after", "").strip()
    if not raw:
        return None
    try:
        seconds = float(raw)
    except ValueError:
        return None
    if seconds < 0:
        return None
    return min(seconds, 60.0)


class PoliteClient:
    """Rate-limit and retry another :class:`HttpClient`.

    Injected clients passed straight to :func:`recover_urls` are used as-is.
    The default client built by :func:`wayback_image_recovery.recover.run`
    is wrapped in this class.
    """

    def __init__(
        self,
        inner: HttpClient,
        config: RecoveryConfig,
        *,
        sleep: Sleep | None = None,
        uniform: Uniform | None = None,
        monotonic: Monotonic = time.monotonic,
    ) -> None:
        self.inner = inner
        self.config = config
        self._sleep = sleep or asyncio.sleep
        self._uniform = uniform or random.uniform
        self.limiter = RateLimiter(
            config.rate_per_second,
            monotonic=monotonic,
            sleep=self._sleep,
        )

    async def request(
        self,
        method: str,
        url: str,
        *,
        params: Sequence[tuple[str, str]] | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: float | None = None,
    ) -> HttpResponse:
        merged = {"User-Agent": self.config.user_agent, "Accept": "*/*"}
        if headers:
            merged.update(headers)
        request_timeout = self.config.timeout if timeout is None else timeout
        last_error: Exception | None = None
        for attempt in range(self.config.max_retries + 1):
            await self.limiter.acquire()
            try:
                response = await self.inner.request(
                    method,
                    url,
                    params=params,
                    headers=merged,
                    timeout=request_timeout,
                )
            except ResponseTooLarge:
                raise
            except TransportError as exc:
                last_error = exc
                if attempt >= self.config.max_retries:
                    raise
                delay = compute_backoff(attempt, uniform=self._uniform)
                logger.debug("retrying %s %s after transport error (%s)", method, url, delay)
                await self._sleep(delay)
                continue
            if response.status in RETRY_STATUSES and attempt < self.config.max_retries:
                delay = retry_after_seconds(response)
                if delay is None:
                    delay = compute_backoff(attempt, uniform=self._uniform)
                logger.debug(
                    "retrying %s %s after HTTP %s in %.2fs", method, url, response.status, delay
                )
                await self._sleep(delay)
                continue
            return response
        if last_error is not None:
            raise last_error
        raise TransportError(f"request failed: {method} {url}")

    async def aclose(self) -> None:
        await self.inner.aclose()


class AiohttpClient:
    """aiohttp-backed client that refuses bodies over ``max_bytes``."""

    def __init__(self, *, timeout: float, max_bytes: int) -> None:
        import aiohttp

        self._aiohttp = aiohttp
        self._max_bytes = max_bytes
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._session = aiohttp.ClientSession(timeout=self._timeout)

    async def request(
        self,
        method: str,
        url: str,
        *,
        params: Sequence[tuple[str, str]] | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: float | None = None,
    ) -> HttpResponse:
        timeout_cfg = self._timeout
        if timeout is not None:
            timeout_cfg = self._aiohttp.ClientTimeout(total=timeout)
        try:
            async with self._session.request(
                method,
                url,
                params=list(params) if params else None,
                headers=dict(headers) if headers else None,
                timeout=timeout_cfg,
                allow_redirects=True,
            ) as response:
                body = await self._read_limited(response)
                return HttpResponse(
                    response.status,
                    body,
                    headers=response.headers,
                    url=str(response.url),
                )
        except ResponseTooLarge:
            raise
        except (self._aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise TransportError(str(exc)) from exc

    async def _read_limited(self, response: object) -> bytes:
        content = response.content  # type: ignore[attr-defined]
        buf = bytearray()
        async for chunk in content.iter_chunked(65536):
            if len(buf) + len(chunk) > self._max_bytes:
                raise ResponseTooLarge(self._max_bytes)
            buf.extend(chunk)
        return bytes(buf)

    async def aclose(self) -> None:
        await self._session.close()
