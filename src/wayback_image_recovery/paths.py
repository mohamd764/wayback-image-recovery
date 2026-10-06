"""Safe output paths derived from archived URLs."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

_SAFE_PART = re.compile(r"[^A-Za-z0-9._-]+")


def safe_relative_path(url: str) -> Path:
    """Map a URL onto a relative path that cannot escape the output directory.

    Query strings are folded into an 8-character hash so two URLs that share a
    path but differ in the query do not overwrite each other.
    """
    parts = urlsplit(url.strip())
    host = parts.netloc.lower()
    if "@" in host:
        host = host.split("@", 1)[1]
    host = host.replace(":", "_")
    host = _SAFE_PART.sub("_", host).strip("._") or "host"

    decoded = unquote(parts.path or "")
    segments: list[str] = []
    for segment in decoded.split("/"):
        if segment in {"", ".", ".."}:
            continue
        cleaned = _SAFE_PART.sub("_", segment).strip("._")
        if not cleaned:
            continue
        segments.append(cleaned[:120])
    if not segments:
        segments = ["index"]

    if parts.query:
        digest = hashlib.sha256(parts.query.encode("utf-8")).hexdigest()[:8]
        stem = Path(segments[-1])
        segments[-1] = f"{stem.stem}_{digest}{stem.suffix}"
    return Path(host, *segments)


def flat_relative_path(url: str, digest: str) -> Path:
    """Return a content-addressed filename for flat output."""
    return Path(f"{digest[:16]}{_suffix(url)}")


def is_inside(root: Path, candidate: Path) -> bool:
    """Return True when ``candidate`` resolves inside ``root``."""
    root_resolved = root.resolve()
    candidate_resolved = candidate.resolve()
    return candidate_resolved == root_resolved or root_resolved in candidate_resolved.parents


def _suffix(url: str) -> str:
    path = unquote(urlsplit(url).path)
    suffix = Path(path).suffix.lower()
    if not suffix or len(suffix) > 10 or not re.fullmatch(r"\.[A-Za-z0-9]+", suffix):
        return ""
    return suffix
