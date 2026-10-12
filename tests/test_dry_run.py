"""Dry-run must resolve snapshots without downloading or writing bodies."""

from __future__ import annotations

import asyncio
from pathlib import Path

from tests.helpers import FakeHttp, png_bytes
from tests.test_recover import _wayback_client, settings

from wayback_image_recovery.manifest import load_jsonl
from wayback_image_recovery.recover import recover_urls


def _download_calls(fake: FakeHttp) -> list[dict[str, object]]:
    """Every request the fake saw that was not a CDX lookup."""
    return [call for call in fake.calls if "cdx.test" not in str(call.get("url", ""))]


def test_dry_run_records_would_recover_without_downloading(tmp_path: Path) -> None:
    url = "https://example.com/images/logo.png"
    fake = _wayback_client({url: b"binary-body"})

    report = asyncio.run(recover_urls([url], settings(tmp_path, dry_run=True), client=fake))

    # The snapshot is resolved and reported as a recovery that would happen.
    assert report.summary["would_recover"] == 1
    assert report.summary["recovered"] == 0
    assert report.records[0].status == "would_recover"
    assert report.records[0].source == "wayback"
    assert report.records[0].timestamp == "20210314120000"

    # No body was fetched: the only traffic was the CDX lookup.
    assert _download_calls(fake) == []

    # Nothing was written to disk, but the manifest still records the plan.
    assert not list(tmp_path.rglob("*.png"))
    manifest = load_jsonl(tmp_path / "manifest.jsonl")
    assert len(manifest) == 1
    assert next(iter(manifest.values())).status == "would_recover"


def test_dry_run_and_real_run_agree_on_the_snapshot(tmp_path: Path) -> None:
    """A dry run predicts the same snapshot a real run then downloads."""
    url = "https://example.com/images/logo.png"

    dry = asyncio.run(
        recover_urls(
            [url], settings(tmp_path, dry_run=True), client=_wayback_client({url: png_bytes()})
        )
    )
    assert dry.summary["would_recover"] == 1

    real = asyncio.run(
        recover_urls([url], settings(tmp_path), client=_wayback_client({url: png_bytes()}))
    )
    assert real.summary["recovered"] == 1
    assert real.records[0].source == dry.records[0].source
    assert real.records[0].timestamp == dry.records[0].timestamp
