"""CLI parsing, help text, and exit codes."""

from __future__ import annotations

import importlib.metadata as metadata
from pathlib import Path

import pytest

from wayback_image_recovery.cli import main
from wayback_image_recovery.models import RecoveryRecord, RecoveryReport


def test_help_includes_examples(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["recover", "--help"])
    assert exc.value.code == 0
    output = capsys.readouterr().out
    assert "wir recover --domain example.com" in output
    assert "--closest" in output
    assert "--csv" in output
    assert "id_" in output or "Common Crawl" in output


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert "wayback-image-recovery 0.1.0" in capsys.readouterr().out


def test_missing_input_exits_2(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["recover"]) == 2
    assert "Provide" in capsys.readouterr().err


def test_unknown_flag_exits_2(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["recover", "--not-a-flag"])
    assert exc.value.code == 2


def test_recover_maps_options(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    seen: dict[str, object] = {}

    def fake_run(urls, *, domain, config, client=None):  # type: ignore[no-untyped-def]
        seen["urls"] = list(urls)
        seen["domain"] = domain
        seen["config"] = config
        summary = {
            "total": 0,
            "recovered": 0,
            "duplicate": 0,
            "skipped": 0,
            "not_found": 0,
            "invalid": 0,
            "error": 0,
        }
        return RecoveryReport(records=[], summary=summary, output_dir=config.output_dir)

    monkeypatch.setattr("wayback_image_recovery.cli.run", fake_run)
    url_file = tmp_path / "urls.txt"
    url_file.write_text("# c\nhttps://example.com/a.png\n", encoding="utf-8")
    code = main(
        [
            "recover",
            str(url_file),
            "--domain",
            "example.com/news/",
            "-o",
            str(tmp_path / "out"),
            "--closest",
            "2020-01-15",
            "--rate",
            "2",
            "--mimetype",
            "image/png",
            "--status-code",
            "200",
            "--status-code",
            "301",
            "--flat",
            "--cc-index",
            "https://index.test/a",
            "--min-bytes",
            "100",
            "--no-scheme-fallback",
        ]
    )
    assert code == 0
    config = seen["config"]
    assert seen["domain"] == "example.com/news/"
    assert seen["urls"] == ["https://example.com/a.png"]
    assert config.mode == "closest"  # type: ignore[attr-defined]
    assert config.closest == "2020-01-15"  # type: ignore[attr-defined]
    assert config.rate_per_second == 2  # type: ignore[attr-defined]
    assert config.mimetypes == ("image/png",)  # type: ignore[attr-defined]
    assert config.status_codes == ("200", "301")  # type: ignore[attr-defined]
    assert config.flat is True  # type: ignore[attr-defined]
    assert config.cc_indexes == ("https://index.test/a",)  # type: ignore[attr-defined]
    assert config.min_bytes == 100  # type: ignore[attr-defined]
    assert config.scheme_fallback is False  # type: ignore[attr-defined]


def test_strict_and_error_exit_codes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    url_file = tmp_path / "urls.txt"
    url_file.write_text("https://example.com/a.png\n", encoding="utf-8")

    def report_with(status: str, *, error_count: int) -> RecoveryReport:
        summary = {
            "total": 1,
            "recovered": 0,
            "duplicate": 0,
            "skipped": 0,
            "not_found": 1 if status == "not_found" else 0,
            "invalid": 0,
            "error": error_count,
        }
        return RecoveryReport(
            records=[RecoveryRecord(url="https://example.com/a.png", status=status)],
            summary=summary,
            output_dir=tmp_path,
        )

    monkeypatch.setattr(
        "wayback_image_recovery.cli.run",
        lambda *_a, **_k: report_with("not_found", error_count=0),
    )
    assert main(["recover", str(url_file), "-o", str(tmp_path)]) == 0
    assert main(["recover", str(url_file), "-o", str(tmp_path), "--strict"]) == 1

    monkeypatch.setattr(
        "wayback_image_recovery.cli.run",
        lambda *_a, **_k: report_with("error", error_count=1),
    )
    assert main(["recover", str(url_file), "-o", str(tmp_path)]) == 1


def test_bad_timestamp_is_a_usage_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    url_file = tmp_path / "urls.txt"
    url_file.write_text("https://example.com/a.png\n", encoding="utf-8")
    code = main(["recover", str(url_file), "--closest", "nope", "-o", str(tmp_path)])
    assert code == 2
    assert "timestamp" in capsys.readouterr().err


def test_verbose_and_quiet_conflict(tmp_path: Path) -> None:
    url_file = tmp_path / "urls.txt"
    url_file.write_text("https://example.com/a.png\n", encoding="utf-8")
    assert main(["recover", str(url_file), "-v", "-q", "-o", str(tmp_path)]) == 2


def test_console_scripts_are_registered() -> None:
    try:
        selected = metadata.entry_points(group="console_scripts")
    except TypeError:  # Python 3.9
        selected = metadata.entry_points().get("console_scripts", [])
    scripts = {item.name: item.value for item in selected}
    assert scripts["wayback-image-recovery"] == "wayback_image_recovery.cli:main"
    assert scripts["wir"] == "wayback_image_recovery.cli:main"
