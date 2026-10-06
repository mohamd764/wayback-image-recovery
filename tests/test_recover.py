"""End-to-end recovery against a fake HTTP client."""

from __future__ import annotations

import asyncio
import gzip
import hashlib
import json
from pathlib import Path

import pytest
from tests.helpers import FakeHttp, cdx_table, param, png_bytes, warc_response

from wayback_image_recovery.commoncrawl import IndexCache, lookup_commoncrawl
from wayback_image_recovery.models import RecoveryConfig
from wayback_image_recovery.recover import recover_urls, run, validate_config

CDX = "https://cdx.test/search"


def settings(tmp_path: Path, **overrides: object) -> RecoveryConfig:
    values: dict[str, object] = {
        "output_dir": tmp_path,
        "rate_per_second": 0,
        "use_common_crawl": False,
        "cdx_endpoint": CDX,
        "user_agent": "wir-test",
        "concurrency": 2,
    }
    values.update(overrides)
    return RecoveryConfig(**values)  # type: ignore[arg-type]


def _row(url: str, timestamp: str = "20210314120000") -> dict[str, str]:
    return {
        "original": url,
        "timestamp": timestamp,
        "statuscode": "200",
        "mimetype": "image/png",
        "digest": "DIGEST",
        "length": "100",
    }


def _wayback_client(
    bodies: dict[str, bytes], rows: dict[str, dict[str, str]] | None = None
) -> FakeHttp:
    resolved = rows or {url: _row(url) for url in bodies}
    fake = FakeHttp()

    def cdx_response(
        _method: str, _url: str, params: list[tuple[str, str]], _headers: dict[str, str]
    ):
        target = param(params, "url") or ""
        row = resolved.get(target)
        if row is None:
            return (200, b"[]")
        return (200, cdx_table([row]))

    def download(_method: str, url: str, _params: list[tuple[str, str]], _headers: dict[str, str]):
        for original, body in bodies.items():
            if original in url:
                return (200, body)
        return (404, b"missing")

    fake.add(lambda _m, url, _p, _h: url == CDX, cdx_response)
    fake.add(lambda _m, url, _p, _h: "id_/" in url, download)
    return fake


def test_wayback_download_writes_manifest_and_valid_png(tmp_path: Path) -> None:
    url = "https://example.com/images/logo.png"
    image = png_bytes()
    fake = _wayback_client({url: image})
    report = asyncio.run(recover_urls([url], settings(tmp_path), client=fake))

    assert report.summary["recovered"] == 1
    record = report.records[0]
    assert record.source == "wayback"
    assert record.timestamp == "20210314120000"
    assert record.sha256 == hashlib.sha256(image).hexdigest()
    assert record.path == "example.com/images/logo.png"
    saved = tmp_path / record.path
    assert saved.read_bytes() == image
    download = next(call for call in fake.calls if "id_/" in str(call["url"]))
    assert download["url"] == f"https://web.archive.org/web/20210314120000id_/{url}"

    csv_text = (tmp_path / "manifest.csv").read_text(encoding="utf-8")
    assert (
        csv_text.splitlines()[0] == "url,source,timestamp,status,sha256,path,bytes,mimetype,error"
    )
    payload = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert payload["tool"] == "wayback-image-recovery"
    assert payload["summary"]["recovered"] == 1


def test_html_error_page_is_invalid_and_not_saved(tmp_path: Path) -> None:
    url = "https://example.com/missing.jpg"
    fake = _wayback_client({url: b"<!DOCTYPE html><html><body>lost</body></html>"})
    report = asyncio.run(recover_urls([url], settings(tmp_path), client=fake))
    assert report.records[0].status == "invalid"
    assert report.records[0].error == "html_error_page"
    assert list(tmp_path.rglob("*.jpg")) == []


def test_missing_capture_is_not_found(tmp_path: Path) -> None:
    fake = _wayback_client({})
    report = asyncio.run(
        recover_urls(["https://example.com/nope.png"], settings(tmp_path), client=fake)
    )
    assert report.records[0].status == "not_found"
    assert any(param(call["params"], "url") == "http://example.com/nope.png" for call in fake.calls)  # type: ignore[arg-type]


def test_scheme_fallback_finds_the_other_scheme(tmp_path: Path) -> None:
    https = "https://example.com/a.png"
    http = "http://example.com/a.png"
    image = png_bytes((9, 9, 9))
    fake = _wayback_client({http: image}, rows={http: _row(http)})
    report = asyncio.run(recover_urls([https], settings(tmp_path), client=fake))
    assert report.records[0].status == "recovered"
    assert report.records[0].source == "wayback"


def test_common_crawl_fallback_after_wayback_miss(tmp_path: Path) -> None:
    url = "https://example.com/from-cc.png"
    image = png_bytes((7, 8, 9))
    record = gzip.compress(warc_response(image))
    index = "https://index.test/CC-MAIN-2024-30-index"
    fake = FakeHttp()
    fake.add(lambda _m, fetched, _p, _h: fetched == CDX, lambda *_a: (200, b"[]"))
    fake.add(
        lambda _m, fetched, _p, _h: fetched == index,
        lambda *_a: (
            200,
            (
                json.dumps(
                    {
                        "url": url,
                        "timestamp": "20220601000000",
                        "status": "200",
                        "mime": "image/png",
                        "filename": "crawl-data/CC-MAIN/file.warc.gz",
                        "offset": "0",
                        "length": str(len(record)),
                        "digest": "CC",
                    }
                )
                + "\n"
            ).encode(),
        ),
    )
    fake.add(
        lambda _m, fetched, _p, headers: fetched.startswith("https://data.test/"),
        lambda *_a: (206, record),
    )
    config = settings(
        tmp_path,
        use_common_crawl=True,
        cc_indexes=(index,),
        cc_data_base="https://data.test",
    )
    report = asyncio.run(recover_urls([url], config, client=fake))
    assert report.records[0].status == "recovered"
    assert report.records[0].source == "commoncrawl"
    assert report.records[0].timestamp == "20220601000000"
    ranged = next(call for call in fake.calls if str(call["url"]).startswith("https://data.test/"))
    headers = ranged["headers"]
    assert headers["range"] == f"bytes=0-{len(record) - 1}"  # type: ignore[index]
    assert headers["accept-encoding"] == "identity"  # type: ignore[index]
    assert (tmp_path / report.records[0].path).read_bytes() == image


def test_invalid_wayback_body_falls_through_to_common_crawl(tmp_path: Path) -> None:
    url = "https://example.com/logo.png"
    image = png_bytes((2, 2, 2))
    record = gzip.compress(warc_response(image))
    index = "https://index.test/only"
    fake = _wayback_client({url: b"<html><body>soft 404</body></html>"})
    fake.add(
        lambda _m, fetched, _p, _h: fetched == index,
        lambda *_a: (
            200,
            (
                json.dumps(
                    {
                        "url": url,
                        "timestamp": "20230101000000",
                        "status": "200",
                        "mime": "image/png",
                        "filename": "crawl-data/file.warc.gz",
                        "offset": "0",
                        "length": str(len(record)),
                    }
                )
                + "\n"
            ).encode(),
        ),
    )
    fake.add(
        lambda _m, fetched, _p, _h: (
            "data.commoncrawl.org" in fetched or fetched.startswith("https://data.commoncrawl.org")
        ),
        lambda *_a: (206, record),
    )
    # Default data base is the public host; override it so the fake can match.
    config = settings(
        tmp_path,
        use_common_crawl=True,
        cc_indexes=(index,),
        cc_data_base="https://data.test",
    )
    fake.routes.append(
        (
            lambda _m, fetched, _p, _h: fetched.startswith("https://data.test/"),
            lambda *_a: (206, record),
        )
    )
    report = asyncio.run(recover_urls([url], config, client=fake))
    assert report.summary == {
        "total": 1,
        "recovered": 1,
        "duplicate": 0,
        "skipped": 0,
        "not_found": 0,
        "invalid": 0,
        "error": 0,
    }
    assert report.records[0].source == "commoncrawl"


def test_duplicate_bytes_are_stored_once(tmp_path: Path) -> None:
    image = png_bytes((3, 3, 3))
    urls = ["https://example.com/one.png", "https://example.com/two.png"]
    fake = _wayback_client({url: image for url in urls})
    report = asyncio.run(recover_urls(urls, settings(tmp_path), client=fake))
    statuses = sorted(record.status for record in report.records)
    assert statuses == ["duplicate", "recovered"]
    paths = {record.path for record in report.records}
    assert len(paths) == 1
    written = [path for path in tmp_path.rglob("*.png")]
    assert len(written) == 1


def test_resume_skips_completed_urls_and_redownloads_missing_files(tmp_path: Path) -> None:
    url = "https://example.com/keep.png"
    image = png_bytes()
    first = asyncio.run(
        recover_urls([url], settings(tmp_path), client=_wayback_client({url: image}))
    )
    assert first.records[0].status == "recovered"
    saved = tmp_path / first.records[0].path

    quiet = FakeHttp()
    second = asyncio.run(recover_urls([url], settings(tmp_path), client=quiet))
    assert second.records[0].status == "skipped"
    assert second.records[0].sha256 == first.records[0].sha256
    assert quiet.calls == []

    saved.unlink()
    third = asyncio.run(
        recover_urls([url], settings(tmp_path), client=_wayback_client({url: image}))
    )
    assert third.records[0].status == "recovered"
    assert saved.is_file()


def test_second_url_reuses_hash_from_an_earlier_run(tmp_path: Path) -> None:
    image = png_bytes((5, 5, 5))
    first_url = "https://example.com/first.png"
    second_url = "https://example.com/second.png"
    asyncio.run(
        recover_urls([first_url], settings(tmp_path), client=_wayback_client({first_url: image}))
    )
    report = asyncio.run(
        recover_urls([second_url], settings(tmp_path), client=_wayback_client({second_url: image}))
    )
    assert report.records[0].status == "duplicate"
    assert report.records[0].path == "example.com/first.png"
    assert len(list(tmp_path.rglob("*.png"))) == 1


def test_domain_discovery_then_latest_lookup(tmp_path: Path) -> None:
    found = ["https://example.com/a.png", "https://example.com/b.png"]
    bodies = {found[0]: png_bytes((1, 0, 0)), found[1]: png_bytes((0, 1, 0))}
    fake = FakeHttp()

    def discover(_method: str, _url: str, params: list[tuple[str, str]], _headers: dict[str, str]):
        page = param(params, "page")
        if page == "0":
            return (200, cdx_table([_row(found[0], "20190101000000")]))
        if page == "1":
            return (200, cdx_table([_row(found[1], "20180101000000")]))
        return (200, b"[]")

    def lookup(_method: str, _url: str, params: list[tuple[str, str]], _headers: dict[str, str]):
        target = param(params, "url") or ""
        if target not in bodies:
            return (200, b"[]")
        return (200, cdx_table([_row(target, "20240101000000")]))

    fake.add(lambda _m, url, params, _h: url == CDX and param(params, "matchType"), discover)
    fake.add(lambda _m, url, params, _h: url == CDX and param(params, "limit") == "1", lookup)
    fake.add(
        lambda _m, url, _p, _h: "id_/" in url,
        lambda _m, url, _p, _h: next(
            ((200, body) for original, body in bodies.items() if original in url),
            (404, b"missing"),
        ),
    )
    report = run(
        [],
        domain="example.com",
        config=settings(tmp_path, page_size=1, max_urls=10),
        client=fake,
    )
    assert report.summary["recovered"] == 2
    assert {record.timestamp for record in report.records} == {"20240101000000"}
    assert {record.url for record in report.records} == set(found)


def test_discovery_stops_at_max_urls(tmp_path: Path) -> None:
    pages: list[str | None] = []
    fake = FakeHttp()

    def discover(_method: str, _url: str, params: list[tuple[str, str]], _headers: dict[str, str]):
        page = param(params, "page")
        pages.append(page)
        url = f"https://example.com/{page}.png"
        return (200, cdx_table([_row(url)]))

    fake.add(lambda _m, url, params, _h: url == CDX and param(params, "matchType"), discover)
    fake.add(
        lambda _m, url, params, _h: url == CDX and param(params, "limit") == "1",
        lambda _m, _u, params, _h: (200, cdx_table([_row(param(params, "url") or "")])),
    )
    image = png_bytes()
    fake.add(lambda _m, url, _p, _h: "id_/" in url, lambda *_a: (200, image))
    report = run(
        [],
        domain="example.com",
        config=settings(tmp_path, page_size=1, max_urls=1),
        client=fake,
    )
    assert report.summary["total"] == 1
    assert pages == ["0"]


def test_flat_layout_and_min_bytes(tmp_path: Path) -> None:
    url = "https://example.com/nested/a.png"
    image = png_bytes()
    flat = asyncio.run(
        recover_urls(
            [url], settings(tmp_path / "flat", flat=True), client=_wayback_client({url: image})
        )
    )
    assert "/" not in flat.records[0].path
    assert flat.records[0].path.endswith(".png")

    rejected = asyncio.run(
        recover_urls(
            [url],
            settings(tmp_path / "small", min_bytes=5_000_000),
            client=_wayback_client({url: image}),
        )
    )
    assert rejected.records[0].status == "invalid"
    assert "min-bytes" in rejected.records[0].error


def test_unsafe_scheme_does_not_touch_the_network(tmp_path: Path) -> None:
    fake = FakeHttp()
    report = asyncio.run(recover_urls(["ftp://example.com/a.png"], settings(tmp_path), client=fake))
    assert report.records[0].status == "error"
    assert fake.calls == []


def test_query_strings_keep_distinct_files(tmp_path: Path) -> None:
    left = "https://example.com/img.png?id=1"
    right = "https://example.com/img.png?id=2"
    fake = _wayback_client({left: png_bytes((1, 1, 1)), right: png_bytes((2, 2, 2))})
    report = asyncio.run(recover_urls([left, right], settings(tmp_path), client=fake))
    assert report.summary["recovered"] == 2
    assert len({record.path for record in report.records}) == 2


def test_encoded_traversal_cannot_escape_output(tmp_path: Path) -> None:
    url = "https://example.com/%2e%2e/%2e%2e/secret.png"
    image = png_bytes()
    report = asyncio.run(
        recover_urls([url], settings(tmp_path), client=_wayback_client({url: image}))
    )
    saved = (tmp_path / report.records[0].path).resolve()
    assert tmp_path.resolve() in saved.parents
    assert saved.is_file()


def test_repeated_input_is_processed_once(tmp_path: Path) -> None:
    url = "https://example.com/a.png"
    image = png_bytes()
    report = asyncio.run(
        recover_urls([url, url, url], settings(tmp_path), client=_wayback_client({url: image}))
    )
    assert report.summary["total"] == 1


def test_run_rejects_a_live_event_loop(tmp_path: Path) -> None:
    async def _call() -> None:
        with pytest.raises(RuntimeError):
            run([], config=settings(tmp_path), client=FakeHttp())

    asyncio.run(_call())


def test_validate_config_rejects_closest_without_timestamp(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        validate_config(settings(tmp_path, mode="closest", closest=None))


def test_collinfo_picks_the_newest_index() -> None:
    asyncio.run(_collinfo_picks_the_newest_index())


async def _collinfo_picks_the_newest_index() -> None:
    newest = "https://index.test/CC-MAIN-2024-30-index"
    oldest = "https://index.test/CC-MAIN-2020-01-index"
    fake = FakeHttp()
    fake.add(
        lambda _m, url, _p, _h: url == "https://collinfo.test/collinfo.json",
        lambda *_a: (
            200,
            json.dumps(
                [
                    {"id": "CC-MAIN-2020-01", "cdx-api": oldest},
                    {"id": "CC-MAIN-2024-30", "cdx-api": newest},
                ]
            ).encode(),
        ),
    )
    fake.add(lambda _m, url, _p, _h: url == newest, lambda *_a: (200, b""))
    fake.add(lambda _m, url, _p, _h: url == oldest, lambda *_a: (200, b"should-not-query"))
    config = RecoveryConfig(
        use_wayback=False,
        cc_index_limit=1,
        cc_collinfo_url="https://collinfo.test/collinfo.json",
        cdx_endpoint=CDX,
        rate_per_second=0,
    )
    found = await lookup_commoncrawl(
        fake,
        "https://example.com/a.png",
        config,
        IndexCache(),
    )
    assert found is None
    queried = [call["url"] for call in fake.calls]
    assert newest in queried
    assert oldest not in queried
