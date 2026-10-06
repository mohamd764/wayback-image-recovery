"""Public import surface."""

from wayback_image_recovery import (
    RecoveryConfig,
    RecoveryRecord,
    RecoveryReport,
    Snapshot,
    __version__,
    discover_urls,
    recover_urls,
    run,
)


def test_version_and_exports() -> None:
    assert __version__ == "0.1.0"
    assert RecoveryConfig().rate_per_second == 1.0
    assert callable(discover_urls)
    assert callable(recover_urls)
    assert callable(run)
    assert RecoveryRecord(url="https://example.com/a.png").to_dict()["url"].endswith("a.png")
    assert (
        Snapshot(
            original_url="https://example.com/a.png",
            timestamp="20200101000000",
            statuscode="200",
            mimetype="image/png",
            digest="",
            source="wayback",
            download_url="https://example.test",
        ).source
        == "wayback"
    )
    assert RecoveryReport(records=[], summary={"total": 0}, output_dir=".").summary["total"] == 0
