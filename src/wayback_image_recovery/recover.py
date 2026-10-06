"""Orchestrate lookup, download, validation, and the manifest."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from typing import TypeVar

from wayback_image_recovery.cdx import discover_original_urls, lookup_wayback
from wayback_image_recovery.commoncrawl import IndexCache, lookup_commoncrawl
from wayback_image_recovery.errors import (
    ArchiveResponseError,
    ResponseTooLarge,
    SnapshotNotFound,
    TransportError,
)
from wayback_image_recovery.http import AiohttpClient, HttpClient, PoliteClient
from wayback_image_recovery.inputs import dedupe_preserve, normalize_url, parse_domain_query
from wayback_image_recovery.manifest import append_jsonl, load_jsonl, summarize, write_reports
from wayback_image_recovery.models import RecoveryConfig, RecoveryRecord, RecoveryReport, Snapshot
from wayback_image_recovery.storage import ContentStore
from wayback_image_recovery.warc import http_payload

logger = logging.getLogger(__name__)


def validate_config(config: RecoveryConfig) -> None:
    """Raise ``ValueError`` when a config cannot be run safely."""
    if config.concurrency < 1:
        raise ValueError("concurrency must be at least 1")
    if config.max_retries < 0:
        raise ValueError("retries must be >= 0")
    if config.timeout <= 0:
        raise ValueError("timeout must be > 0")
    if config.min_bytes < 0:
        raise ValueError("min-bytes must be >= 0")
    if config.max_bytes < 1:
        raise ValueError("max-bytes must be >= 1")
    if config.min_bytes > config.max_bytes:
        raise ValueError("min-bytes cannot exceed max-bytes")
    if config.max_urls < 1:
        raise ValueError("max-urls must be >= 1")
    if config.page_size < 1:
        raise ValueError("page-size must be >= 1")
    if config.cc_index_limit < 0:
        raise ValueError("cc-index-limit must be >= 0")
    if config.mode not in {"latest", "closest"}:
        raise ValueError("mode must be latest or closest")
    if config.mode == "closest" and not config.closest:
        raise ValueError("closest mode requires a timestamp")
    if not config.user_agent.strip():
        raise ValueError("user-agent must not be empty")
    if not config.use_wayback and not config.use_common_crawl:
        raise ValueError("enable Wayback, Common Crawl, or both")
    if config.closest:
        from wayback_image_recovery.cdx import normalize_timestamp

        normalize_timestamp(config.closest)


def warn_if_impolite(config: RecoveryConfig) -> None:
    """Warn when a public archive would be hit faster than this tool recommends."""
    public = "archive.org" in config.cdx_endpoint or "commoncrawl.org" in config.cc_collinfo_url
    if not public:
        return
    if config.rate_per_second <= 0 or config.rate_per_second > 5:
        logger.warning(
            "Request rate %s/s is aggressive for public archive endpoints. "
            "Prefer 1 request/second unless you have permission to go faster.",
            config.rate_per_second,
        )


async def discover_urls(
    domain: str,
    config: RecoveryConfig | None = None,
    *,
    client: HttpClient | None = None,
) -> list[str]:
    """Discover original URLs for a domain or URL prefix."""
    settings = config or RecoveryConfig()
    validate_config(settings)
    query = parse_domain_query(domain, settings.match_type)

    async def _go(http: HttpClient) -> list[str]:
        return await discover_original_urls(http, query, settings)

    return await _with_client(settings, client, _go)


async def recover_urls(
    urls: Sequence[str],
    config: RecoveryConfig | None = None,
    *,
    client: HttpClient | None = None,
) -> RecoveryReport:
    """Recover the given URLs. Inject ``client`` to avoid live network access."""
    settings = config or RecoveryConfig()
    validate_config(settings)

    async def _go(http: HttpClient) -> RecoveryReport:
        return await _recover(list(urls), settings, http, discover_domain=None)

    return await _with_client(settings, client, _go)


def run(
    urls: Sequence[str] | None = None,
    *,
    domain: str | None = None,
    config: RecoveryConfig | None = None,
    client: HttpClient | None = None,
) -> RecoveryReport:
    """Synchronous entry point used by the CLI.

    Raise ``RuntimeError`` when called from a running event loop. Async
    callers should await :func:`recover_urls` or :func:`discover_urls`.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(
            _async_run(list(urls or []), domain=domain, config=config, client=client)
        )
    raise RuntimeError(
        "run() cannot be called from a running event loop; await recover_urls() instead"
    )


async def _async_run(
    urls: list[str],
    *,
    domain: str | None,
    config: RecoveryConfig | None,
    client: HttpClient | None,
) -> RecoveryReport:
    settings = config or RecoveryConfig()
    validate_config(settings)

    async def _go(http: HttpClient) -> RecoveryReport:
        return await _recover(urls, settings, http, discover_domain=domain)

    return await _with_client(settings, client, _go)


T = TypeVar("T")


async def _with_client(
    config: RecoveryConfig,
    client: HttpClient | None,
    func: Callable[[HttpClient], Awaitable[T]],
) -> T:
    if client is not None:
        return await func(client)
    warn_if_impolite(config)
    inner = AiohttpClient(
        timeout=config.timeout,
        max_bytes=config.max_bytes + 1024 * 1024,
    )
    polite = PoliteClient(inner, config)
    try:
        return await func(polite)
    finally:
        await inner.aclose()


async def _recover(
    urls: list[str],
    config: RecoveryConfig,
    client: HttpClient,
    *,
    discover_domain: str | None,
) -> RecoveryReport:
    config.output_dir.mkdir(parents=True, exist_ok=True)
    collected = list(urls)
    if discover_domain:
        query = parse_domain_query(discover_domain, config.match_type)
        try:
            discovered = await discover_original_urls(client, query, config)
        except (TransportError, ArchiveResponseError):
            if not collected:
                raise
            logger.warning("domain discovery failed; continuing with explicit URLs")
        else:
            collected.extend(discovered)

    normalized: list[str] = []
    invalid: list[RecoveryRecord] = []
    for url in dedupe_preserve(collected):
        try:
            normalized.append(normalize_url(url))
        except ValueError as exc:
            invalid.append(RecoveryRecord(url=url.strip(), status="error", error=str(exc)))

    normalized = dedupe_preserve(normalized)
    jsonl_path = config.output_dir / "manifest.jsonl"
    for record in invalid:
        append_jsonl(jsonl_path, record)
    prior = load_jsonl(jsonl_path) if config.resume else {}
    store = ContentStore(config)
    store.seed(prior)
    cache = IndexCache()
    results: dict[str, RecoveryRecord] = {}
    semaphore = asyncio.Semaphore(config.concurrency)
    write_lock = asyncio.Lock()
    total = len(normalized)
    counter = {"done": 0}

    async def _one(url: str) -> None:
        async with semaphore:
            try:
                record = await recover_one(url, config, client, store, prior, cache)
            except Exception as exc:
                logger.debug("recovery failed for %s", url, exc_info=True)
                record = RecoveryRecord(url=url, status="error", error=str(exc))
        async with write_lock:
            counter["done"] += 1
            logger.info(
                "[%s/%s] %s  %s  %s",
                counter["done"],
                total,
                record.status,
                record.timestamp or "-",
                url,
            )
            append_jsonl(jsonl_path, record)
            results[url] = record

    await asyncio.gather(*(_one(url) for url in normalized))
    records = invalid + [results[url] for url in normalized]
    summary = summarize(records)
    report = RecoveryReport(records=records, summary=summary, output_dir=config.output_dir)
    write_reports(report)
    return report


async def recover_one(
    url: str,
    config: RecoveryConfig,
    client: HttpClient,
    store: ContentStore,
    prior: dict[str, RecoveryRecord],
    cache: IndexCache,
) -> RecoveryRecord:
    """Resolve and store one URL."""
    existing = store.reusable(url, prior)
    if existing is not None:
        return existing

    invalid_reason = ""
    errors: list[str] = []
    sources: list[str] = []
    if config.use_wayback:
        sources.append("wayback")
    if config.use_common_crawl:
        sources.append("commoncrawl")

    for source in sources:
        try:
            snapshot = await _lookup(source, url, config, client, cache)
        except (TransportError, ArchiveResponseError) as exc:
            errors.append(f"{source}: {exc}")
            continue
        if snapshot is None:
            continue
        try:
            body = await fetch_snapshot(client, snapshot)
        except SnapshotNotFound:
            continue
        except ResponseTooLarge as exc:
            errors.append(f"{source}: {exc}")
            continue
        except (TransportError, ArchiveResponseError, ValueError) as exc:
            errors.append(f"{source}: {exc}")
            continue
        ok, status, detail = store.assess(body, snapshot)
        if not ok:
            if status == "invalid":
                invalid_reason = detail
            else:
                errors.append(f"{source}: {detail}")
            continue
        return await store.commit(url, snapshot, body)

    if errors:
        return RecoveryRecord(url=url, status="error", error="; ".join(errors))
    if invalid_reason:
        return RecoveryRecord(url=url, status="invalid", error=invalid_reason)
    return RecoveryRecord(url=url, status="not_found", error="no matching capture")


async def _lookup(
    source: str,
    url: str,
    config: RecoveryConfig,
    client: HttpClient,
    cache: IndexCache,
) -> Snapshot | None:
    if source == "wayback":
        return await lookup_wayback(client, url, config)
    return await lookup_commoncrawl(client, url, config, cache)


async def fetch_snapshot(client: HttpClient, snapshot: Snapshot) -> bytes:
    """Download original bytes for a Wayback or Common Crawl capture."""
    if snapshot.source == "wayback":
        response = await client.request("GET", snapshot.download_url)
        if response.status == 404:
            raise SnapshotNotFound(snapshot.download_url)
        if response.status != 200:
            raise ArchiveResponseError(f"download HTTP {response.status}")
        return response.body

    if snapshot.offset is None or snapshot.length is None:
        raise ArchiveResponseError("Common Crawl capture is missing a WARC byte range")
    end = snapshot.offset + snapshot.length - 1
    response = await client.request(
        "GET",
        snapshot.download_url,
        headers={
            "Range": f"bytes={snapshot.offset}-{end}",
            "Accept-Encoding": "identity",
        },
    )
    if response.status == 404:
        raise SnapshotNotFound(snapshot.download_url)
    if response.status not in {200, 206}:
        raise ArchiveResponseError(f"WARC fetch HTTP {response.status}")
    try:
        return http_payload(response.body)
    except ValueError as exc:
        raise ArchiveResponseError(f"could not parse WARC record: {exc}") from exc
