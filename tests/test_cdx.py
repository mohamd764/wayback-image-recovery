"""CDX parsing and query construction."""

from __future__ import annotations

import json

import pytest
from tests.helpers import cdx_table

from wayback_image_recovery.cdx import (
    build_discovery_params,
    build_lookup_params,
    normalize_timestamp,
    parse_cdx_payload,
    select_snapshot,
    snapshot_from_cdx,
    wayback_raw_url,
)
from wayback_image_recovery.errors import ArchiveResponseError
from wayback_image_recovery.matchers import mimetype_cdx_filter, status_cdx_filter
from wayback_image_recovery.models import DomainQuery, RecoveryConfig, Snapshot


def test_normalize_timestamp_accepts_dates() -> None:
    assert normalize_timestamp("2020-01-15") == "20200115000000"
    assert normalize_timestamp("20200115123045") == "20200115123045"


def test_normalize_timestamp_rejects_garbage() -> None:
    with pytest.raises(ValueError):
        normalize_timestamp("yesterday")


def test_raw_url_uses_identity_modifier() -> None:
    url = wayback_raw_url("20200101000000", "https://example.com/a.png")
    assert url == "https://web.archive.org/web/20200101000000id_/https://example.com/a.png"


def test_lookup_params_latest_and_closest() -> None:
    latest = build_lookup_params("https://example.com/a.png", RecoveryConfig())
    assert ("sort", "reverse") in latest
    assert ("limit", "1") in latest
    assert ("filter", "mimetype:image/.*") in latest
    assert ("filter", "statuscode:200") in latest

    closest = build_lookup_params(
        "https://example.com/a.png",
        RecoveryConfig(mode="closest", closest="2020-01-01"),
    )
    assert ("sort", "closest") in closest
    assert ("closest", "20200101000000") in closest


def test_discovery_params_include_match_type_and_page() -> None:
    params = build_discovery_params(
        DomainQuery(url="example.com", match_type="domain"),
        RecoveryConfig(page_size=25),
        2,
    )
    assert ("url", "example.com") in params
    assert ("matchType", "domain") in params
    assert ("collapse", "urlkey") in params
    assert ("page", "2") in params
    assert ("pageSize", "25") in params


def test_parse_cdx_skips_header_and_reads_rows() -> None:
    payload = cdx_table(
        [
            {
                "original": "https://example.com/a.png",
                "timestamp": "20200101010101",
                "statuscode": "200",
                "mimetype": "image/png",
                "digest": "ABC",
                "length": "10",
            }
        ]
    )
    rows = parse_cdx_payload(payload)
    assert rows == [
        {
            "original": "https://example.com/a.png",
            "timestamp": "20200101010101",
            "statuscode": "200",
            "mimetype": "image/png",
            "digest": "ABC",
            "length": "10",
        }
    ]
    snapshot = snapshot_from_cdx(rows[0])
    assert snapshot is not None
    assert snapshot.source == "wayback"
    assert "id_/" in snapshot.download_url


def test_parse_cdx_empty_and_error_shapes() -> None:
    assert parse_cdx_payload(b"") == []
    assert parse_cdx_payload(b"[]") == []
    with pytest.raises(ArchiveResponseError):
        parse_cdx_payload(b"Blocked")
    with pytest.raises(ArchiveResponseError):
        parse_cdx_payload(json.dumps({"message": "no"}).encode())


def test_mime_filter_escapes_and_wildcard() -> None:
    assert mimetype_cdx_filter(("image/",), field="mimetype") == "mimetype:image/.*"
    assert mimetype_cdx_filter(("image/svg+xml",), field="mime") == r"mime:image/svg\+xml"
    assert mimetype_cdx_filter(("any",), field="mimetype") is None
    assert status_cdx_filter(("200", "301"), field="statuscode") == "statuscode:(200|301)"
    assert status_cdx_filter(("*",), field="status") is None


def test_select_snapshot_latest_and_closest() -> None:
    older = Snapshot(
        original_url="https://example.com/a.png",
        timestamp="20200101000000",
        statuscode="200",
        mimetype="image/png",
        digest="",
        source="wayback",
        download_url="https://example.test/old",
    )
    newer = Snapshot(
        original_url="https://example.com/a.png",
        timestamp="20220101000000",
        statuscode="200",
        mimetype="image/png",
        digest="",
        source="wayback",
        download_url="https://example.test/new",
    )
    assert select_snapshot([older, newer], RecoveryConfig()).download_url.endswith("/new")
    chosen = select_snapshot(
        [older, newer],
        RecoveryConfig(mode="closest", closest="2020-06-01"),
    )
    assert chosen.timestamp == "20200101000000"
