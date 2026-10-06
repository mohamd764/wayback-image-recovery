"""Test doubles and tiny image fixtures."""

from __future__ import annotations

import inspect
import io
import json
from collections.abc import Callable, Mapping, Sequence

from PIL import Image

from wayback_image_recovery.http import HttpResponse

CDX_FIELDS = ("original", "timestamp", "statuscode", "mimetype", "digest", "length")
Responder = Callable[..., object]


def png_bytes(color: tuple[int, int, int] = (12, 34, 56)) -> bytes:
    image = Image.new("RGB", (4, 4), color)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def cdx_table(rows: list[dict[str, str]]) -> bytes:
    table: list[list[str]] = [list(CDX_FIELDS)]
    for row in rows:
        table.append([row.get(field, "") for field in CDX_FIELDS])
    return json.dumps(table).encode("utf-8")


def param(params: Sequence[tuple[str, str]], key: str) -> str | None:
    for name, value in params:
        if name == key:
            return value
    return None


def warc_response(body: bytes, *, content_type: str = "image/png") -> bytes:
    http = (
        b"HTTP/1.1 200 OK\r\n"
        + f"Content-Type: {content_type}\r\n".encode()
        + f"Content-Length: {len(body)}\r\n".encode()
        + b"\r\n"
        + body
    )
    header = (
        b"WARC/1.0\r\n"
        b"WARC-Type: response\r\n"
        b"WARC-Target-URI: https://example.com/a.png\r\n"
        b"Content-Type: application/http; msgtype=response\r\n"
        + f"Content-Length: {len(http)}\r\n".encode()
        + b"\r\n"
    )
    return header + http


class FakeHttp:
    """Route table used in place of aiohttp."""

    def __init__(self) -> None:
        self.routes: list[tuple[Callable[..., bool], Responder]] = []
        self.calls: list[dict[str, object]] = []

    def add(self, predicate: Callable[..., bool], responder: Responder) -> FakeHttp:
        self.routes.append((predicate, responder))
        return self

    async def request(
        self,
        method: str,
        url: str,
        *,
        params: Sequence[tuple[str, str]] | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: float | None = None,
    ) -> HttpResponse:
        query = list(params or [])
        header_map = {key.lower(): value for key, value in dict(headers or {}).items()}
        self.calls.append(
            {
                "method": method,
                "url": url,
                "params": query,
                "headers": header_map,
                "timeout": timeout,
            }
        )
        for predicate, responder in self.routes:
            if not predicate(method, url, query, header_map):
                continue
            result = responder(method, url, query, header_map)
            if inspect.isawaitable(result):
                result = await result
            if isinstance(result, HttpResponse):
                return result
            if isinstance(result, Exception):
                raise result
            status, body = result[:2]
            extra = result[2] if len(result) > 2 else {}
            return HttpResponse(int(status), body, headers=extra, url=url)
        return HttpResponse(404, b"not routed", url=url)

    async def aclose(self) -> None:
        return None
