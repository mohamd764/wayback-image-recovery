"""Read URL lists, CSV files, and domain queries."""

from __future__ import annotations

import csv
import io
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from wayback_image_recovery.models import DomainQuery

_URL_COLUMNS = ("url", "uri", "image", "image_url", "src", "link", "original")
_MATCH_TYPES = {"domain", "host", "prefix", "exact"}


def read_url_lines(text: str) -> list[str]:
    """Return non-comment, non-empty lines from a URL list."""
    urls: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        urls.append(stripped)
    return urls


def read_csv_urls(text: str) -> list[str]:
    """Return URLs from a CSV body.

    A header row is recognized when it contains a known column name such as
    ``url`` or ``image_url``. Headerless files use the first column. Comma,
    semicolon, and tab delimiters are accepted.
    """
    sample = text.lstrip("\ufeff")
    if not sample.strip():
        return []
    lines = [
        line for line in sample.splitlines() if line.strip() and not line.lstrip().startswith("#")
    ]
    if not lines:
        return []
    dialect = _sniff_dialect("\n".join(lines[:20]))
    reader = csv.reader(io.StringIO("\n".join(lines)), dialect)
    rows = [row for row in reader if any(cell.strip() for cell in row)]
    if not rows:
        return []
    header = [cell.strip().lower() for cell in rows[0]]
    header_index = _header_index(header)
    first_cell = rows[0][0] if rows[0] else ""
    if header_index is not None and not _looks_like_url(first_cell):
        index = header_index
        data_rows = rows[1:]
    else:
        index = 0
        data_rows = rows
    urls: list[str] = []
    for row in data_rows:
        if index >= len(row):
            continue
        value = row[index].strip()
        if value and not value.startswith("#"):
            urls.append(value)
    return urls


def read_csv_file(path: Path) -> list[str]:
    return read_csv_urls(path.read_text(encoding="utf-8-sig"))


def parse_domain_query(value: str, match_type: str | None = None) -> DomainQuery:
    """Turn ``example.com`` or a URL prefix into a CDX query."""
    raw = value.strip()
    if not raw:
        raise ValueError("domain is empty")
    if match_type is not None and match_type not in _MATCH_TYPES:
        raise ValueError(f"match type must be one of: {', '.join(sorted(_MATCH_TYPES))}")
    if "://" not in raw and "/" not in raw and " " not in raw:
        host, path = raw, ""
    else:
        candidate = raw if "://" in raw else "https://" + raw
        parts = urlsplit(candidate)
        host = parts.netloc
        path = parts.path
    host = host.strip().lower()
    if "@" in host:
        host = host.split("@", 1)[1]
    if not host or any(char.isspace() for char in host):
        raise ValueError(f"invalid domain {value!r}")
    if path in {"", "/"}:
        return DomainQuery(url=host, match_type=match_type or "domain")
    prefix = host + path
    return DomainQuery(url=prefix, match_type=match_type or "prefix")


def normalize_url(url: str) -> str:
    """Strip whitespace and fragments, and require an http(s) URL."""
    raw = url.strip()
    parts = urlsplit(raw)
    if parts.scheme.lower() not in {"http", "https"}:
        raise ValueError(f"unsupported URL scheme: {parts.scheme or '(none)'}")
    if not parts.netloc:
        raise ValueError("URL is missing a host")
    return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))


def dedupe_preserve(urls: list[str]) -> list[str]:
    """Drop repeated URLs, keeping the first occurrence."""
    seen: set[str] = set()
    ordered: list[str] = []
    for url in urls:
        if url in seen:
            continue
        seen.add(url)
        ordered.append(url)
    return ordered


def _sniff_dialect(sample: str) -> csv.Dialect:
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        return csv.excel


def _header_index(header: list[str]) -> int | None:
    for name in _URL_COLUMNS:
        if name in header:
            return header.index(name)
    return None


def _looks_like_url(value: str) -> bool:
    stripped = value.strip().lower()
    return stripped.startswith("http://") or stripped.startswith("https://")
