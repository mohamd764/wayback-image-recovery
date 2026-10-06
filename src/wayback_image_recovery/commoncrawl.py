"""Common Crawl index lookup and WARC range fetch metadata."""

from __future__ import annotations

import json
import logging

from wayback_image_recovery.cdx import select_snapshot
from wayback_image_recovery.errors import ArchiveResponseError, TransportError
from wayback_image_recovery.http import HttpClient
from wayback_image_recovery.matchers import (
    mime_allowed,
    mimetype_cdx_filter,
    status_allowed,
    status_cdx_filter,
)
from wayback_image_recovery.models import RecoveryConfig, Snapshot

logger = logging.getLogger(__name__)

CC_FIELDS = "url,timestamp,status,mime,filename,offset,length,digest"


class IndexCache:
    """Remember the collinfo list for the duration of one run."""

    def __init__(self) -> None:
        self.indexes: list[str] | None = None


def build_cc_params(url: str, config: RecoveryConfig) -> list[tuple[str, str]]:
    params = [
        ("url", url),
        ("output", "json"),
        ("limit", "20"),
        ("fl", CC_FIELDS),
    ]
    mime = mimetype_cdx_filter(config.mimetypes, field="mime")
    if mime:
        params.append(("filter", mime))
    status = status_cdx_filter(config.status_codes, field="status")
    if status:
        params.append(("filter", status))
    return params


def parse_cc_index(payload: bytes) -> list[dict[str, str]]:
    """Parse JSON-lines or a JSON array from a Common Crawl index response.

    A normal hit is one JSON object per line. A single object is still a hit
    when it carries a URL or WARC filename. An object that only carries
    ``message`` is an index error.
    """
    text = payload.decode("utf-8", errors="replace").strip()
    if not text:
        return []
    if text.startswith("["):
        return _parse_cc_array(text, payload)
    rows: list[dict[str, str]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            item = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise ArchiveResponseError("Common Crawl index row was not JSON") from exc
        if isinstance(item, dict):
            rows.append(_capture_row(item))
    return rows


def _parse_cc_array(text: str, payload: bytes) -> list[dict[str, str]]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ArchiveResponseError("Common Crawl index was not JSON") from exc
    if not isinstance(data, list):
        raise ArchiveResponseError("Common Crawl index had an unexpected shape")
    if data and isinstance(data[0], list):
        from wayback_image_recovery.cdx import parse_cdx_payload

        return parse_cdx_payload(payload)
    return [_capture_row(item) for item in data if isinstance(item, dict)]


def _capture_row(item: dict[str, object]) -> dict[str, str]:
    if not _is_capture(item) and ("message" in item or "error" in item):
        message = item.get("message") or item.get("error") or "Common Crawl index error"
        raise ArchiveResponseError(str(message))
    return _stringify(item)


def _is_capture(item: dict[str, object]) -> bool:
    return any(item.get(key) for key in ("url", "original", "filename"))


def snapshot_from_cc(row: dict[str, str], config: RecoveryConfig) -> Snapshot | None:
    original = row.get("url") or row.get("original") or ""
    timestamp = row.get("timestamp") or ""
    filename = (row.get("filename") or "").lstrip("/")
    status = row.get("status") or row.get("statuscode") or ""
    mime = row.get("mime") or row.get("mimetype") or ""
    if not original or not timestamp or not filename:
        return None
    if not mime_allowed(mime, config.mimetypes) or not status_allowed(status, config.status_codes):
        return None
    try:
        offset = int(row["offset"])
        length = int(row["length"])
    except (KeyError, TypeError, ValueError):
        return None
    if length <= 0 or offset < 0:
        return None
    base = config.cc_data_base.rstrip("/")
    return Snapshot(
        original_url=original,
        timestamp=timestamp,
        statuscode=status,
        mimetype=mime,
        digest=row.get("digest") or "",
        source="commoncrawl",
        download_url=f"{base}/{filename}",
        offset=offset,
        length=length,
        filename=filename,
    )


async def lookup_commoncrawl(
    client: HttpClient,
    url: str,
    config: RecoveryConfig,
    cache: IndexCache,
) -> Snapshot | None:
    """Search the configured Common Crawl indexes and pick one capture."""
    indexes = await resolve_indexes(client, config, cache)
    if not indexes:
        return None
    candidates: list[Snapshot] = []
    failures: list[str] = []
    for api in indexes:
        try:
            response = await client.request("GET", api, params=build_cc_params(url, config))
        except (TransportError, ArchiveResponseError) as exc:
            failures.append(f"{api}: {exc}")
            continue
        if response.status == 404:
            continue
        if response.status != 200:
            failures.append(f"{api} HTTP {response.status}")
            continue
        try:
            rows = parse_cc_index(response.body)
        except ArchiveResponseError as exc:
            failures.append(f"{api}: {exc}")
            continue
        for row in rows:
            snapshot = snapshot_from_cc(row, config)
            if snapshot is not None:
                candidates.append(snapshot)
    if candidates:
        return select_snapshot(candidates, config)
    if failures and len(failures) == len(indexes):
        raise ArchiveResponseError("; ".join(failures))
    if failures:
        logger.warning("some Common Crawl indexes failed for %s: %s", url, "; ".join(failures))
    return None


async def resolve_indexes(
    client: HttpClient,
    config: RecoveryConfig,
    cache: IndexCache,
) -> list[str]:
    """Return explicit index URLs, or the newest entries from collinfo."""
    if config.cc_indexes:
        return list(config.cc_indexes)
    if cache.indexes is not None:
        return cache.indexes
    response = await client.request("GET", config.cc_collinfo_url)
    if response.status != 200:
        raise ArchiveResponseError(f"Common Crawl collinfo HTTP {response.status}")
    try:
        payload = json.loads(response.body.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise ArchiveResponseError("Common Crawl collinfo was not JSON") from exc
    if not isinstance(payload, list):
        raise ArchiveResponseError("Common Crawl collinfo had an unexpected shape")
    indexed: list[tuple[str, str]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        api = item.get("cdx-api")
        ident = str(item.get("id") or "")
        if isinstance(api, str) and api:
            indexed.append((ident, api))
    indexed.sort(key=lambda item: item[0], reverse=True)
    limit = config.cc_index_limit
    chosen = [api for _ident, api in indexed[:limit]] if limit else []
    cache.indexes = chosen
    logger.info("using %s Common Crawl index(es)", len(chosen))
    return chosen


def _stringify(item: dict[str, object]) -> dict[str, str]:
    return {str(key): "" if value is None else str(value) for key, value in item.items()}
