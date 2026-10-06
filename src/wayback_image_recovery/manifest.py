"""Manifest JSONL log plus CSV and JSON reports."""

from __future__ import annotations

import csv
import json
import logging
from pathlib import Path

from wayback_image_recovery.models import RecoveryRecord, RecoveryReport

logger = logging.getLogger(__name__)

CSV_COLUMNS = (
    "url",
    "source",
    "timestamp",
    "status",
    "sha256",
    "path",
    "bytes",
    "mimetype",
    "error",
)
SUMMARY_KEYS = ("recovered", "duplicate", "skipped", "not_found", "invalid", "error")


def summarize(records: list[RecoveryRecord]) -> dict[str, int]:
    summary = {key: 0 for key in SUMMARY_KEYS}
    summary["total"] = len(records)
    for record in records:
        summary[record.status] = summary.get(record.status, 0) + 1
    return summary


def load_jsonl(path: Path) -> dict[str, RecoveryRecord]:
    """Load the latest manifest event for each URL."""
    records: dict[str, RecoveryRecord] = {}
    if not path.exists():
        return records
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            logger.warning("skipping malformed manifest line %s in %s", line_no, path)
            continue
        if not isinstance(payload, dict) or not payload.get("url"):
            continue
        records[str(payload["url"])] = record_from_dict(payload)
    return records


def append_jsonl(path: Path, record: RecoveryRecord) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")
        handle.flush()


def write_reports(report: RecoveryReport) -> None:
    """Rewrite ``manifest.csv`` and ``manifest.json`` for this run."""
    output = report.output_dir
    output.mkdir(parents=True, exist_ok=True)
    csv_path = output / "manifest.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(CSV_COLUMNS), lineterminator="\n")
        writer.writeheader()
        for record in report.records:
            payload = record.to_dict()
            writer.writerow({column: payload.get(column, "") for column in CSV_COLUMNS})
    json_path = output / "manifest.json"
    json_path.write_text(json.dumps(report.to_dict(), indent=2) + "\n", encoding="utf-8")


def record_from_dict(payload: dict[str, object]) -> RecoveryRecord:
    return RecoveryRecord(
        url=str(payload.get("url") or ""),
        source=str(payload.get("source") or ""),
        timestamp=str(payload.get("timestamp") or ""),
        status=str(payload.get("status") or "error"),
        sha256=str(payload.get("sha256") or ""),
        path=str(payload.get("path") or ""),
        size_bytes=_as_int(payload.get("bytes", payload.get("size_bytes", 0))),
        mimetype=str(payload.get("mimetype") or ""),
        error=str(payload.get("error") or ""),
    )


def format_summary(report: RecoveryReport) -> str:
    summary = report.summary
    counts = (
        "recovered={recovered} duplicate={duplicate} skipped={skipped} "
        "not_found={not_found} invalid={invalid} error={error}"
    ).format(**summary)
    return "\n".join(
        [
            f"Processed {summary['total']} URL(s)",
            counts,
            f"Manifest: {report.output_dir / 'manifest.csv'}",
            f"Report: {report.output_dir / 'manifest.json'}",
        ]
    )


def _as_int(value: object) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0
