"""Manifest log and report files."""

from __future__ import annotations

import json
from pathlib import Path

from wayback_image_recovery.manifest import format_summary, load_jsonl, summarize, write_reports
from wayback_image_recovery.models import RecoveryRecord, RecoveryReport


def test_jsonl_keeps_the_latest_event_and_skips_garbage(tmp_path: Path) -> None:
    path = tmp_path / "manifest.jsonl"
    path.write_text(
        "\n".join(
            [
                json.dumps({"url": "https://example.com/a.png", "status": "error", "error": "old"}),
                "not-json",
                json.dumps(
                    {
                        "url": "https://example.com/a.png",
                        "status": "recovered",
                        "source": "wayback",
                        "timestamp": "20200101000000",
                        "sha256": "abc",
                        "path": "example.com/a.png",
                        "bytes": 12,
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    loaded = load_jsonl(path)
    assert loaded["https://example.com/a.png"].status == "recovered"
    assert loaded["https://example.com/a.png"].size_bytes == 12


def test_summary_and_report_roundtrip(tmp_path: Path) -> None:
    records = [
        RecoveryRecord(
            url="https://example.com/a.png", status="recovered", sha256="abc", path="a.png"
        ),
        RecoveryRecord(url="https://example.com/b.png", status="not_found"),
    ]
    summary = summarize(records)
    assert summary["total"] == 2
    assert summary["recovered"] == 1
    assert summary["not_found"] == 1
    report = RecoveryReport(records=records, summary=summary, output_dir=tmp_path)
    write_reports(report)
    text = format_summary(report)
    assert "Processed 2 URL(s)" in text
    assert "recovered=1" in text
    payload = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert payload["records"][0]["sha256"] == "abc"
    assert "generated_at" in payload
