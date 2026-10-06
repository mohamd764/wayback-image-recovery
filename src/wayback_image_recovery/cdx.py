"""Wayback Machine CDX lookup and domain discovery."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Sequence

from wayback_image_recovery.errors import ArchiveResponseError
from wayback_image_recovery.http import HttpClient
from wayback_image_recovery.matchers import (
    mime_allowed,
    mimetype_cdx_filter,
    status_allowed,
    status_cdx_filter,
)
from wayback_image_recovery.models import DomainQuery, RecoveryConfig, Snapshot

logger = logging.getLogger(__name__)

CDX_FIELDS = ("original", "timestamp", "statuscode", "mimetype", "digest", "length")
_HEADER_MARKERS = {"urlkey", "original", "timestamp"}


def normalize_timestamp(value: str) -> str:
    """Normalize a user timestamp to the 14-digit CDX form."""
    digits = re.sub(r"\D", "", value.strip())
    if not digits or not 4 <= len(digits) <= 14:
        raise ValueError(
            f"invalid timestamp {value!r}; use YYYYMMDD or a full YYYYMMDDhhmmss value"
        )
    return digits.ljust(14, "0")


def wayback_raw_url(
    timestamp: str,
    original: str,
    replay_base: str = "https://web.archive.org/web",
) -> str:
    """Build the ``id_`` replay URL that returns original bytes, not the toolbar."""
    return f"{replay_base.rstrip('/')}/{timestamp}id_/{original}"


def build_lookup_params(url: str, config: RecoveryConfig) -> list[tuple[str, str]]:
    """CDX parameters that select one latest or closest capture."""
    params: list[tuple[str, str]] = [
        ("url", url),
        ("output", "json"),
        ("fl", ",".join(CDX_FIELDS)),
        ("limit", "1"),
    ]
    if config.mode == "closest":
        if not config.closest:
            raise ValueError("closest mode requires a timestamp")
        params.append(("closest", normalize_timestamp(config.closest)))
        params.append(("sort", "closest"))
    else:
        params.append(("sort", "reverse"))
    params.extend(wayback_filter_params(config))
    return params


def build_discovery_params(
    query: DomainQuery,
    config: RecoveryConfig,
    page: int,
) -> list[tuple[str, str]]:
    """CDX parameters for one page of unique URLs under a domain or prefix.

    ``collapse=urlkey`` only collapses consecutive rows, and the CDX order is
    oldest-first inside a URL. The timestamp on a discovery row is therefore
    not treated as the latest capture. Callers resolve each URL again.
    """
    params: list[tuple[str, str]] = [
        ("url", query.url),
        ("matchType", query.match_type),
        ("output", "json"),
        ("fl", ",".join(CDX_FIELDS)),
        ("collapse", "urlkey"),
        ("page", str(page)),
        ("pageSize", str(config.page_size)),
    ]
    params.extend(wayback_filter_params(config))
    return params


def wayback_filter_params(config: RecoveryConfig) -> list[tuple[str, str]]:
    params: list[tuple[str, str]] = []
    mime = mimetype_cdx_filter(config.mimetypes, field="mimetype")
    if mime:
        params.append(("filter", mime))
    status = status_cdx_filter(config.status_codes, field="statuscode")
    if status:
        params.append(("filter", status))
    return params


def parse_cdx_payload(payload: bytes) -> list[dict[str, str]]:
    """Parse a CDX JSON document into field dictionaries."""
    text = payload.decode("utf-8", errors="replace").strip()
    if not text:
        return []
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        snippet = text[:180].replace("\n", " ")
        raise ArchiveResponseError(f"CDX response was not JSON: {snippet}") from exc
    if isinstance(data, dict):
        message = data.get("message") or data
        raise ArchiveResponseError(f"CDX error: {message}")
    if not isinstance(data, list):
        raise ArchiveResponseError("CDX response had an unexpected shape")
    return _rows_from_table(data)


def snapshot_from_cdx(
    row: dict[str, str],
    *,
    replay_base: str = "https://web.archive.org/web",
) -> Snapshot | None:
    original = row.get("original") or ""
    timestamp = row.get("timestamp") or ""
    if not original or not timestamp:
        return None
    return Snapshot(
        original_url=original,
        timestamp=timestamp,
        statuscode=row.get("statuscode", ""),
        mimetype=row.get("mimetype", ""),
        digest=row.get("digest", ""),
        source="wayback",
        download_url=wayback_raw_url(timestamp, original, replay_base),
    )


async def discover_original_urls(
    client: HttpClient,
    query: DomainQuery,
    config: RecoveryConfig,
) -> list[str]:
    """Return unique original URLs that match the discovery filters."""
    found: list[str] = []
    seen: set[str] = set()
    page = 0
    max_pages = min(200, (config.max_urls // max(config.page_size, 1)) + 2)
    while page < max_pages and len(found) < config.max_urls:
        params = build_discovery_params(query, config, page)
        response = await client.request("GET", config.cdx_endpoint, params=params)
        if response.status != 200:
            raise ArchiveResponseError(f"CDX discovery HTTP {response.status}")
        parsed = parse_cdx_payload(response.body)
        rows = [row for row in parsed if _row_matches(row, config)]
        for row in rows:
            original = row.get("original") or ""
            if not original or original in seen:
                continue
            seen.add(original)
            found.append(original)
            if len(found) >= config.max_urls:
                break
        if len(parsed) < config.page_size:
            break
        page += 1
    logger.info("discovered %s URL(s) for %s", len(found), query.url)
    return found


async def lookup_wayback(
    client: HttpClient,
    url: str,
    config: RecoveryConfig,
) -> Snapshot | None:
    """Return the latest or closest Wayback capture for ``url``."""
    for candidate in _lookup_candidates(url, config):
        params = build_lookup_params(candidate, config)
        response = await client.request("GET", config.cdx_endpoint, params=params)
        if response.status != 200:
            raise ArchiveResponseError(f"CDX HTTP {response.status} for {candidate}")
        snapshots = []
        for row in parse_cdx_payload(response.body):
            if not _row_matches(row, config):
                continue
            snapshot = snapshot_from_cdx(row, replay_base=config.wayback_replay_base)
            if snapshot is not None:
                snapshots.append(snapshot)
        if snapshots:
            return select_snapshot(snapshots, config)
    return None


def select_snapshot(snapshots: Sequence[Snapshot], config: RecoveryConfig) -> Snapshot:
    """Pick the latest capture, or the one closest to the requested timestamp."""
    if config.mode == "closest" and config.closest:
        target = int(normalize_timestamp(config.closest))
        return min(snapshots, key=lambda item: abs(int(item.timestamp) - target))
    return max(snapshots, key=lambda item: item.timestamp)


def _lookup_candidates(url: str, config: RecoveryConfig) -> list[str]:
    candidates = [url]
    if not config.scheme_fallback:
        return candidates
    flipped = _flip_scheme(url)
    if flipped and flipped not in candidates:
        candidates.append(flipped)
    return candidates


def _flip_scheme(url: str) -> str | None:
    if url.startswith("https://"):
        return "http://" + url[len("https://") :]
    if url.startswith("http://"):
        return "https://" + url[len("http://") :]
    return None


def _row_matches(row: dict[str, str], config: RecoveryConfig) -> bool:
    return mime_allowed(row.get("mimetype", ""), config.mimetypes) and status_allowed(
        row.get("statuscode", ""),
        config.status_codes,
    )


def _rows_from_table(data: list[object]) -> list[dict[str, str]]:
    if not data:
        return []
    header: list[str] | None = None
    rows = data
    first = data[0]
    if isinstance(first, list) and first and str(first[0]) in _HEADER_MARKERS:
        header = [str(cell) for cell in first]
        rows = data[1:]
    parsed: list[dict[str, str]] = []
    for row in rows:
        if isinstance(row, dict):
            parsed.append(
                {str(key): "" if value is None else str(value) for key, value in row.items()}
            )
            continue
        if not isinstance(row, list):
            continue
        if header is None:
            if len(row) == len(CDX_FIELDS):
                parsed.append({key: str(value) for key, value in zip(CDX_FIELDS, row)})
            continue
        item = {header[index]: str(row[index]) for index in range(min(len(header), len(row)))}
        parsed.append(item)
    return parsed
