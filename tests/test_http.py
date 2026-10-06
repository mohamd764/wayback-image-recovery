"""Rate limiting and retries."""

from __future__ import annotations

import asyncio

import pytest
from tests.helpers import FakeHttp

from wayback_image_recovery.errors import ResponseTooLarge, TransportError
from wayback_image_recovery.http import (
    HttpResponse,
    PoliteClient,
    RateLimiter,
    compute_backoff,
    retry_after_seconds,
)
from wayback_image_recovery.models import RecoveryConfig


def test_backoff_grows_and_caps() -> None:
    assert compute_backoff(0, uniform=lambda _low, _high: 0.0) == 0.5
    capped = compute_backoff(8, uniform=lambda _low, high: high)
    assert capped == pytest.approx(30 + 7.5)


def test_retry_after_is_numeric_and_capped() -> None:
    assert retry_after_seconds(HttpResponse(429, b"", headers={"Retry-After": "12"})) == 12
    assert retry_after_seconds(HttpResponse(429, b"", headers={"Retry-After": "90"})) == 60
    assert retry_after_seconds(HttpResponse(429, b"", headers={"Retry-After": "soon"})) is None


class _Clock:
    def __init__(self) -> None:
        self.now = 100.0
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def test_rate_limiter_can_be_built_before_the_loop_starts() -> None:
    """Python 3.9 binds locks to a loop at construction; defer that until acquire."""
    limiter = RateLimiter(1000)

    async def _exercise() -> None:
        await limiter.acquire()
        await limiter.acquire()

    asyncio.run(_exercise())


def test_rate_limiter_spaces_request_starts() -> None:
    async def _exercise() -> None:
        clock = _Clock()
        limiter = RateLimiter(10, monotonic=clock.monotonic, sleep=clock.sleep)
        await limiter.acquire()
        await limiter.acquire()
        await limiter.acquire()
        assert clock.slept == pytest.approx([0.1, 0.1])

    asyncio.run(_exercise())


def test_polite_client_retries_transport_errors() -> None:
    state = {"n": 0}

    def respond(_method: str, _url: str, _params: list[tuple[str, str]], _headers: dict[str, str]):
        state["n"] += 1
        if state["n"] < 3:
            raise TransportError("temporary")
        return HttpResponse(200, b"ok")

    fake = FakeHttp().add(lambda *_args: True, respond)
    slept: list[float] = []

    async def record(seconds: float) -> None:
        slept.append(seconds)

    client = PoliteClient(
        fake,
        RecoveryConfig(rate_per_second=0, max_retries=4, user_agent="wir-test"),
        sleep=record,
        uniform=lambda _low, _high: 0.0,
    )

    async def _exercise() -> None:
        response = await client.request("GET", "https://example.test/cdx")
        assert response.body == b"ok"

    asyncio.run(_exercise())
    assert state["n"] == 3
    assert slept == [0.5, 1.0]
    headers = fake.calls[0]["headers"]
    assert isinstance(headers, dict)
    assert headers["user-agent"] == "wir-test"


def test_polite_client_honors_retry_after() -> None:
    state = {"n": 0}

    def respond(*_args: object):
        state["n"] += 1
        if state["n"] == 1:
            return HttpResponse(503, b"busy", headers={"Retry-After": "9"})
        return HttpResponse(200, b"ok")

    fake = FakeHttp().add(lambda *_args: True, respond)
    slept: list[float] = []

    async def record(seconds: float) -> None:
        slept.append(seconds)

    client = PoliteClient(
        fake,
        RecoveryConfig(rate_per_second=0, max_retries=2),
        sleep=record,
        uniform=lambda _low, _high: 0.0,
    )

    async def _exercise() -> None:
        response = await client.request("GET", "https://example.test/a")
        assert response.status == 200

    asyncio.run(_exercise())
    assert slept == [9.0]


def test_response_too_large_is_not_retried() -> None:
    fake = FakeHttp().add(lambda *_args: True, lambda *_args: ResponseTooLarge(8))
    client = PoliteClient(
        fake,
        RecoveryConfig(rate_per_second=0, max_retries=3),
        sleep=_no_sleep,
        uniform=lambda _low, _high: 0.0,
    )

    async def _exercise() -> None:
        with pytest.raises(ResponseTooLarge):
            await client.request("GET", "https://example.test/big")

    asyncio.run(_exercise())
    assert len(fake.calls) == 1


async def _no_sleep(_seconds: float) -> None:
    return None
