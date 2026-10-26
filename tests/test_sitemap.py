"""Local sitemap input: image URLs, ignored pages, and no live fetches."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from tests.helpers import png_bytes
from tests.test_recover import CDX, _wayback_client, settings

from wayback_image_recovery.cli import main
from wayback_image_recovery.inputs import path_looks_like_image, read_sitemap_file, read_sitemap_xml
from wayback_image_recovery.recover import run

FIXTURES = Path(__file__).parent / "fixtures"
IMAGE_SITEMAP = FIXTURES / "image-sitemap.xml"
PLAIN_SITEMAP = FIXTURES / "plain-sitemap.xml"
SITEMAP_INDEX = FIXTURES / "sitemap-index.xml"

IMAGE_URLS = [
    "https://example.com/images/logo.png",
    "https://example.com/photos/banner.jpg?w=100",
    "https://example.com/assets/hero.webp",
]
PLAIN_URLS = [
    "https://example.com/images/logo.png",
    "https://example.com/photos/banner.JPEG",
    "https://example.com/icons/favicon.ico",
]
CHILD_SITEMAPS = [
    "https://example.com/sitemaps/images.xml",
    "https://example.com/sitemaps/pages.xml",
]

_URLSET = "http://www.sitemaps.org/schemas/sitemap/0.9"
_IMAGE = "http://www.google.com/schemas/sitemap-image/1.1"


def test_image_sitemap_keeps_image_locs_and_drops_pages() -> None:
    assert read_sitemap_file(IMAGE_SITEMAP) == [
        "https://example.com/images/logo.png",
        "https://example.com/images/logo.png#preview",
        "https://example.com/photos/banner.jpg?w=100",
        "https://example.com/assets/hero.webp",
    ]


def test_plain_sitemap_keeps_image_paths_only() -> None:
    assert read_sitemap_file(PLAIN_SITEMAP) == [
        "https://example.com/images/logo.png",
        "https://example.com/images/logo.png",
        "https://example.com/photos/banner.JPEG",
        "https://example.com/icons/favicon.ico",
    ]


def test_image_loc_is_kept_without_an_image_suffix() -> None:
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
    <urlset xmlns="{_URLSET}" xmlns:image="{_IMAGE}">
      <url>
        <loc>https://example.com/gallery</loc>
        <image:image>
          <image:loc>https://cdn.example.com/img/12345</image:loc>
        </image:image>
      </url>
    </urlset>
    """
    assert read_sitemap_xml(xml) == ["https://cdn.example.com/img/12345"]


def test_unnamespaced_image_element_is_collected() -> None:
    xml = """<urlset>
      <url>
        <loc>https://example.com/page</loc>
        <image><loc>https://cdn.example.com/img/12345</loc></image>
      </url>
    </urlset>
    """
    assert read_sitemap_xml(xml) == ["https://cdn.example.com/img/12345"]


def test_ampersand_in_loc_is_decoded() -> None:
    xml = f"""<urlset xmlns="{_URLSET}">
      <url><loc>https://example.com/a.png?a=1&amp;b=2</loc></url>
    </urlset>
    """
    assert read_sitemap_xml(xml) == ["https://example.com/a.png?a=1&b=2"]


def test_comment_mentioning_doctype_is_not_a_dtd() -> None:
    xml = f"""<?xml version="1.0"?>
    <!-- not a declaration: <!DOCTYPE urlset> -->
    <urlset xmlns="{_URLSET}">
      <url><loc>https://example.com/a.png</loc></url>
    </urlset>
    """
    assert read_sitemap_xml(xml) == ["https://example.com/a.png"]


def test_utf8_bom_is_accepted() -> None:
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
    <urlset xmlns="{_URLSET}">
      <url><loc>https://example.com/a.png</loc></url>
    </urlset>
    """
    assert read_sitemap_xml(xml.encode("utf-8-sig")) == ["https://example.com/a.png"]


def test_sitemap_index_lists_children_and_does_not_fetch() -> None:
    with pytest.raises(ValueError) as exc:
        read_sitemap_file(SITEMAP_INDEX)
    message = str(exc.value)
    assert "not fetched" in message
    assert "does not crawl" in message
    for url in CHILD_SITEMAPS:
        assert url in message


def test_unnamespaced_sitemap_index_is_rejected() -> None:
    xml = """<sitemapindex>
      <sitemap><loc>https://example.com/sitemaps/images.xml</loc></sitemap>
    </sitemapindex>
    """
    with pytest.raises(ValueError) as exc:
        read_sitemap_xml(xml)
    assert "https://example.com/sitemaps/images.xml" in str(exc.value)


def test_entity_expansion_and_external_entities_are_rejected(tmp_path: Path) -> None:
    secret = tmp_path / "secret.txt"
    secret.write_text("SENTINEL-SECRET-VALUE", encoding="utf-8")
    internal = """<?xml version="1.0"?>
    <!DOCTYPE urlset [<!ENTITY leak "EXPANDED-ENTITY-MARKER">]>
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url><loc>https://example.com/&leak;.png</loc></url>
    </urlset>
    """
    external = f"""<?xml version="1.0"?>
    <!DOCTYPE urlset [<!ENTITY leak SYSTEM "file://{secret}">]>
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url><loc>&leak;</loc></url>
    </urlset>
    """
    remote = """<?xml version="1.0"?>
    <!DOCTYPE urlset [<!ENTITY leak SYSTEM "http://127.0.0.1/evil.dtd">]>
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url><loc>&leak;</loc></url>
    </urlset>
    """
    bomb = """<?xml version="1.0"?>
    <!DOCTYPE lolz [
      <!ENTITY lol "lol">
      <!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">
    ]>
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url><loc>https://example.com/&lol2;.png</loc></url>
    </urlset>
    """
    for payload in (internal, external, remote, bomb):
        with pytest.raises(ValueError) as exc:
            read_sitemap_xml(payload)
        message = str(exc.value)
        assert "must not contain a DTD" in message
        assert "EXPANDED-ENTITY-MARKER" not in message
        assert "SENTINEL-SECRET-VALUE" not in message
        assert "lollol" not in message


def test_malformed_sitemap_is_a_usage_error() -> None:
    with pytest.raises(ValueError, match="could not parse sitemap XML"):
        read_sitemap_xml("<urlset>")


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://example.com/a.PNG", True),
        ("https://example.com/a.svg", True),
        ("https://example.com/a.png?x=1", True),
        ("https://example.com/page.html", False),
        ("https://example.com/render?file=a.png", False),
        ("https://example.com/logo.png/download", False),
        ("https://example.com/files/spec.pdf", False),
    ],
)
def test_path_looks_like_image(url: str, expected: bool) -> None:
    assert path_looks_like_image(url) is expected


def test_sitemap_combined_with_other_inputs_exits_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    sitemap = tmp_path / "sitemap.xml"
    sitemap.write_text("<urlset/>", encoding="utf-8")
    urls = tmp_path / "urls.txt"
    urls.write_text("https://example.com/a.png\n", encoding="utf-8")
    csv_path = tmp_path / "broken.csv"
    csv_path.write_text("url\nhttps://example.com/a.png\n", encoding="utf-8")
    cases = [
        ["recover", "--sitemap", str(sitemap), str(urls)],
        ["recover", "--sitemap", str(sitemap), "--domain", "example.com"],
        ["recover", "--sitemap", str(sitemap), "--csv", str(csv_path)],
    ]
    for argv in cases:
        assert main(argv) == 2
        assert "cannot be combined" in capsys.readouterr().err


def test_sitemap_with_stdin_does_not_read_stdin(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    class _Stdin:
        def read(self) -> str:
            raise AssertionError("stdin should not be read")

    monkeypatch.setattr(sys, "stdin", _Stdin())
    sitemap = tmp_path / "sitemap.xml"
    sitemap.write_text("<urlset/>", encoding="utf-8")
    code = main(["recover", "-", "--sitemap", str(sitemap), "-o", str(tmp_path)])
    assert code == 2
    assert "cannot be combined" in capsys.readouterr().err


def test_missing_sitemap_exits_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["recover", "--sitemap", str(tmp_path / "missing.xml")])
    assert code == 2
    assert "not found" in capsys.readouterr().err


def test_sitemap_index_cli_exits_2_without_running(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def _fail(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("sitemap index must not start a recovery")

    monkeypatch.setattr("wayback_image_recovery.cli.run", _fail)
    code = main(["recover", "--sitemap", str(SITEMAP_INDEX), "-o", "recovered"])
    assert code == 2
    err = capsys.readouterr().err
    assert "not fetched" in err
    for url in CHILD_SITEMAPS:
        assert url in err


def test_empty_sitemap_exits_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    sitemap = tmp_path / "pages.xml"
    sitemap.write_text(
        f"""<urlset xmlns="{_URLSET}">
          <url><loc>https://example.com/about</loc></url>
        </urlset>
        """,
        encoding="utf-8",
    )
    code = main(["recover", "--sitemap", str(sitemap), "-o", str(tmp_path)])
    assert code == 2
    assert "no image URLs" in capsys.readouterr().err


def test_cli_image_sitemap_recovers_unique_urls(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _wayback_client(
        {url: png_bytes((index, 20, 40)) for index, url in enumerate(IMAGE_URLS, start=1)}
    )

    def wrapped(urls, *, domain, config, client=None):  # type: ignore[no-untyped-def]
        assert domain is None
        assert client is None
        return run(urls, domain=domain, config=config, client=fake)

    monkeypatch.setattr("wayback_image_recovery.cli.run", wrapped)
    code = main(
        [
            "recover",
            "--sitemap",
            str(IMAGE_SITEMAP),
            "-o",
            str(tmp_path),
            "--cdx-endpoint",
            CDX,
            "--rate",
            "0",
            "--no-common-crawl",
            "--quiet",
        ]
    )
    assert code == 0
    payload = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert payload["summary"]["total"] == 3
    assert payload["summary"]["recovered"] == 3
    assert [row["url"] for row in payload["records"]] == IMAGE_URLS
    traffic = json.dumps(fake.calls)
    assert "https://example.com/gallery" not in traffic
    assert "other.html" not in traffic
    assert "logo.png#preview" not in traffic


def test_plain_sitemap_recovery_ignores_pages(tmp_path: Path) -> None:
    fake = _wayback_client(
        {url: png_bytes((index, 8, 8)) for index, url in enumerate(PLAIN_URLS, start=1)}
    )
    report = run(
        read_sitemap_file(PLAIN_SITEMAP),
        config=settings(tmp_path),
        client=fake,
    )
    assert report.summary["total"] == 3
    assert report.summary["recovered"] == 3
    assert [record.url for record in report.records] == PLAIN_URLS
    traffic = json.dumps(fake.calls)
    assert "https://example.com/about" not in traffic
    assert "spec.pdf" not in traffic
    assert "clip.mp4" not in traffic
    assert "logo.png/download" not in traffic
