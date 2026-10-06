"""Common Crawl index parsing and WARC payload extraction."""

from __future__ import annotations

import gzip
import json

import pytest
from tests.helpers import png_bytes, warc_response

from wayback_image_recovery.commoncrawl import (
    build_cc_params,
    parse_cc_index,
    snapshot_from_cc,
)
from wayback_image_recovery.errors import ArchiveResponseError
from wayback_image_recovery.models import RecoveryConfig
from wayback_image_recovery.warc import http_payload


def test_parse_json_lines_and_array() -> None:
    line = json.dumps(
        {
            "url": "https://example.com/a.png",
            "timestamp": "20220101000000",
            "status": "200",
            "mime": "image/png",
            "filename": "crawl-data/CC-MAIN/file.warc.gz",
            "offset": "15",
            "length": "40",
            "digest": "ZZ",
        }
    )
    rows = parse_cc_index(line.encode())
    assert rows[0]["filename"].endswith(".warc.gz")
    snapshot = snapshot_from_cc(rows[0], RecoveryConfig(cc_data_base="https://data.test"))
    assert snapshot is not None
    assert snapshot.offset == 15
    assert snapshot.length == 40
    assert snapshot.download_url == "https://data.test/crawl-data/CC-MAIN/file.warc.gz"
    assert snapshot.source == "commoncrawl"


def test_parse_json_array() -> None:
    payload = json.dumps(
        [
            {
                "url": "https://example.com/a.png",
                "timestamp": "20220101000000",
                "status": "404",
                "mime": "image/png",
                "filename": "crawl-data/a.warc.gz",
                "offset": "1",
                "length": "2",
            }
        ]
    ).encode()
    rows = parse_cc_index(payload)
    assert snapshot_from_cc(rows[0], RecoveryConfig()) is None


def test_cc_params_use_mime_field() -> None:
    params = build_cc_params("https://example.com/a.png", RecoveryConfig())
    assert ("filter", "mime:image/.*") in params
    assert ("filter", "status:200") in params


def test_bad_index_row_raises() -> None:
    with pytest.raises(ArchiveResponseError):
        parse_cc_index(b'{"message": "nope"}\nnot-json')


def test_warc_gzip_member_roundtrip() -> None:
    image = png_bytes()
    record = gzip.compress(warc_response(image))
    assert http_payload(record) == image


def test_warc_plain_record_trims_to_content_length() -> None:
    image = png_bytes()
    record = warc_response(image) + b"TRAILING"
    assert http_payload(record) == image


def test_warc_rejects_non_records() -> None:
    with pytest.raises(ValueError):
        http_payload(b"this is not a warc record")
