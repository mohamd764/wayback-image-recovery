"""Parse a Common Crawl WARC response record down to the HTTP body."""

from __future__ import annotations

import gzip
import io


def http_payload(data: bytes) -> bytes:
    """Return the entity body stored in a WARC response record.

    Common Crawl compresses each WARC record as its own gzip member. ``data``
    may be that compressed member or the already-decompressed record.
    """
    record = maybe_gunzip(data)
    warc_body = _after_headers(record, kind="WARC")
    return _after_headers(warc_body, kind="HTTP")


def maybe_gunzip(data: bytes) -> bytes:
    """Decompress a single gzip member, or return ``data`` unchanged."""
    if len(data) < 2 or data[0] != 0x1F or data[1] != 0x8B:
        return data
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(data)) as handle:
            return handle.read()
    except OSError as exc:
        raise ValueError("gzip member could not be decompressed") from exc


def _after_headers(data: bytes, *, kind: str) -> bytes:
    for separator in (b"\r\n\r\n", b"\n\n"):
        index = data.find(separator)
        if index < 0:
            continue
        header = data[:index]
        body = data[index + len(separator) :]
        if kind == "WARC" and not header.startswith(b"WARC/"):
            raise ValueError("expected a WARC record")
        if kind == "HTTP" and not header.upper().startswith(b"HTTP/"):
            raise ValueError("expected an HTTP response inside the WARC record")
        if kind == "HTTP":
            length = _content_length(header)
            if length is not None and 0 <= length <= len(body):
                return body[:length]
        return body
    raise ValueError(f"missing {kind} header separator")


def _content_length(header: bytes) -> int | None:
    for line in header.splitlines():
        if not line.lower().startswith(b"content-length:"):
            continue
        raw = line.split(b":", 1)[1].strip()
        try:
            return int(raw)
        except ValueError:
            return None
    return None
