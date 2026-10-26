"""Example scripts run against the fake HTTP client, never the live archives."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from examples.recover_from_csv import build_config as csv_config
from examples.recover_from_csv import main as recover_from_csv
from examples.recover_from_csv import read_broken_image_csv
from examples.recover_url_list import build_config as list_config
from examples.recover_url_list import main as recover_url_list
from examples.recover_url_list import read_url_file
from examples.resume_run import build_config as resume_config
from examples.resume_run import main as resume_run
from tests.helpers import FakeHttp, cdx_table, param, png_bytes

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
URLS_FILE = EXAMPLES / "urls.txt"
CSV_FILE = EXAMPLES / "broken-images.csv"
CDX = "https://web.archive.org/cdx/search/cdx"
COLLINFO = "https://index.commoncrawl.org/collinfo.json"
_URLS = re.compile(r"https?://[^\s,\"']+")


def _row(url: str, timestamp: str = "20210314120000") -> dict[str, str]:
    return {
        "original": url,
        "timestamp": timestamp,
        "statuscode": "200",
        "mimetype": "image/png",
        "digest": "DIGEST",
        "length": "100",
    }


def archive_client(
    bodies: dict[str, bytes],
    *,
    discovered: list[str] | None = None,
) -> FakeHttp:
    """Wayback CDX plus id_ downloads. An empty collinfo makes misses ``not_found``."""
    fake = FakeHttp()

    def cdx(
        _method: str, _url: str, params: list[tuple[str, str]], _headers: dict[str, str]
    ) -> tuple[int, bytes]:
        if param(params, "matchType"):
            page = param(params, "page")
            rows = [_row(url) for url in (discovered or [])] if page in (None, "0") else []
            return (200, cdx_table(rows) if rows else b"[]")
        target = param(params, "url") or ""
        row = _row(target) if target in bodies else None
        return (200, cdx_table([row]) if row else b"[]")

    def download(
        _method: str, url: str, _params: list[tuple[str, str]], _headers: dict[str, str]
    ) -> tuple[int, bytes]:
        for original, body in bodies.items():
            if original in url:
                return (200, body)
        return (404, b"missing")

    fake.add(lambda _method, url, _params, _headers: url == CDX, cdx)
    fake.add(lambda _method, url, _params, _headers: "id_/" in url, download)
    fake.add(lambda _method, url, _params, _headers: url == COLLINFO, lambda *_args: (200, b"[]"))
    return fake


def test_sample_inputs_use_placeholder_urls_only() -> None:
    text = URLS_FILE.read_text(encoding="utf-8") + "\n" + CSV_FILE.read_text(encoding="utf-8")
    found = _URLS.findall(text)
    assert found
    assert all(url.startswith(("https://example.com/", "http://example.com/")) for url in found)


def test_examples_keep_a_polite_rate_and_resume(tmp_path: Path) -> None:
    assert list_config(tmp_path).rate_per_second == 1.0
    assert csv_config(tmp_path).rate_per_second == 1.0
    resumed = resume_config(tmp_path)
    assert resumed.rate_per_second == 1.0
    assert resumed.resume is True


def test_recover_url_list_example(tmp_path: Path) -> None:
    urls = read_url_file(URLS_FILE)
    assert len(urls) == 2
    bodies = {url: png_bytes((index, 20, 40)) for index, url in enumerate(urls, start=1)}
    report = recover_url_list(
        client=archive_client(bodies),
        output_dir=tmp_path,
        urls_path=URLS_FILE,
    )
    assert report.summary["recovered"] == len(urls)
    assert report.summary["error"] == 0
    assert {record.url for record in report.records} == set(urls)
    for record in report.records:
        assert (tmp_path / record.path).is_file()


def test_discover_urls_example(tmp_path: Path) -> None:
    found = "https://example.com/from-discovery.png"
    fake = archive_client({found: png_bytes((9, 9, 9))}, discovered=[found])
    report = recover_url_list(
        client=fake,
        output_dir=tmp_path,
        urls_path=None,
        domain="example.com",
    )
    assert [record.url for record in report.records] == [found]
    assert report.summary["recovered"] == 1
    assert any(
        param(call["params"], "matchType") == "domain"  # type: ignore[arg-type]
        for call in fake.calls
    )


def test_url_list_example_rejects_an_empty_file(tmp_path: Path) -> None:
    empty = tmp_path / "empty.txt"
    empty.write_text("# nothing to recover\n\n", encoding="utf-8")
    fake = FakeHttp()
    with pytest.raises(SystemExit):
        recover_url_list(client=fake, output_dir=tmp_path / "out", urls_path=empty)
    assert fake.calls == []


def test_recover_from_csv_reads_manifest(tmp_path: Path) -> None:
    urls = read_broken_image_csv(CSV_FILE)
    assert urls[0] == "https://example.com/images/logo.png"
    bodies = {urls[0]: png_bytes()}
    summary = recover_from_csv(
        client=archive_client(bodies), output_dir=tmp_path, csv_path=CSV_FILE
    )
    assert summary["recovered"] == 1
    assert summary["not_found"] == len(urls) - 1
    assert summary["error"] == 0
    manifest = (tmp_path / "manifest.csv").read_text(encoding="utf-8")
    assert manifest.splitlines()[0].startswith("url,source,timestamp,status,")
    assert "not_found" in manifest
    stored = (tmp_path / "manifest.json").read_text(encoding="utf-8")
    assert '"recovered": 1' in stored


def test_csv_reader_accepts_image_url_and_src(tmp_path: Path) -> None:
    image_url = tmp_path / "image-url.csv"
    image_url.write_text(
        "page,image_url\nhttps://example.com/,https://example.com/images/logo.png\n",
        encoding="utf-8",
    )
    src = tmp_path / "src.csv"
    src.write_text("src\nhttps://example.com/images/header.png\n", encoding="utf-8")
    assert read_broken_image_csv(image_url) == ["https://example.com/images/logo.png"]
    assert read_broken_image_csv(src) == ["https://example.com/images/header.png"]
    missing = tmp_path / "notes.csv"
    missing.write_text("page,note\nhttps://example.com/,gone\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        read_broken_image_csv(missing)


def test_resume_example_skips_files_already_saved(tmp_path: Path) -> None:
    urls = read_url_file(URLS_FILE)
    bodies = {url: png_bytes((index, 8, 8)) for index, url in enumerate(urls, start=1)}
    fake = archive_client(bodies)
    first, second = resume_run(client=fake, output_dir=tmp_path, urls_path=URLS_FILE)
    downloads = [call for call in fake.calls if "id_/" in str(call["url"])]
    assert first.summary["recovered"] == len(urls)
    assert second.summary["skipped"] == len(urls)
    assert second.summary["recovered"] == 0
    # The second pass must not look the URLs up again or download them.
    assert len(downloads) == len(urls)
    assert len(fake.calls) == len(urls) * 2
