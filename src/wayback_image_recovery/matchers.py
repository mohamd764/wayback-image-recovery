"""MIME type and status-code filters for CDX queries and local checks."""

from __future__ import annotations

import re
from collections.abc import Sequence


def unrestricted(values: Sequence[str]) -> bool:
    """Return True when the filter should accept every value."""
    if not values:
        return True
    return any(item.strip().lower() in {"any", "*"} for item in values)


def expects_images(patterns: Sequence[str]) -> bool:
    """Return True when every active pattern is an image MIME type."""
    if unrestricted(patterns):
        return False
    active = [item.strip().lower() for item in patterns if item and item.strip()]
    active = [item for item in active if item not in {"any", "*"}]
    if not active:
        return False
    return all(item.startswith("image/") for item in active)


def mime_allowed(mime: str, patterns: Sequence[str]) -> bool:
    """Return True when ``mime`` satisfies the configured patterns."""
    if unrestricted(patterns):
        return True
    value = (mime or "").split(";", 1)[0].strip().lower()
    for raw in patterns:
        pattern = raw.strip().lower()
        if not pattern or pattern in {"any", "*"}:
            return True
        if pattern.endswith("/*"):
            if value.startswith(pattern[:-1]):
                return True
        elif pattern.endswith("/"):
            if value.startswith(pattern):
                return True
        elif value == pattern:
            return True
    return False


def status_allowed(status: str, codes: Sequence[str]) -> bool:
    """Return True when ``status`` is one of the allowed HTTP codes."""
    if unrestricted(codes):
        return True
    value = (status or "").strip()
    allowed = {item.strip() for item in codes if item and item.strip().lower() not in {"any", "*"}}
    return value in allowed


def mimetype_cdx_filter(patterns: Sequence[str], *, field: str) -> str | None:
    """Build a CDX ``filter`` value for MIME types, or None to skip it."""
    if unrestricted(patterns):
        return None
    parts = [_mime_regex(item.strip()) for item in patterns if item and item.strip()]
    parts = [part for part in parts if part]
    if not parts:
        return None
    body = parts[0] if len(parts) == 1 else "(" + "|".join(parts) + ")"
    return f"{field}:{body}"


def status_cdx_filter(codes: Sequence[str], *, field: str) -> str | None:
    """Build a CDX ``filter`` value for HTTP status codes, or None to skip it."""
    if unrestricted(codes):
        return None
    cleaned = [item.strip() for item in codes if item and item.strip().lower() not in {"any", "*"}]
    if not cleaned:
        return None
    parts = [re.escape(item) for item in cleaned]
    body = parts[0] if len(parts) == 1 else "(" + "|".join(parts) + ")"
    return f"{field}:{body}"


def _mime_regex(pattern: str) -> str:
    lowered = pattern.lower()
    if lowered in {"any", "*"}:
        return ""
    if lowered.endswith("/*"):
        return re.escape(pattern[:-2]) + r"/.*"
    if pattern.endswith("/"):
        return re.escape(pattern) + r".*"
    return re.escape(pattern)
