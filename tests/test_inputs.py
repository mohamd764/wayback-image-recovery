"""URL, CSV, and domain input parsing."""

from __future__ import annotations

import pytest

from wayback_image_recovery.inputs import (
    normalize_url,
    parse_domain_query,
    read_csv_urls,
    read_url_lines,
)


def test_url_lines_skip_comments_and_blanks() -> None:
    text = "\n# comment\nhttps://example.com/a.png\n\nhttps://example.com/b.png\n"
    assert read_url_lines(text) == [
        "https://example.com/a.png",
        "https://example.com/b.png",
    ]


def test_csv_detects_image_url_column_and_semicolon() -> None:
    text = "page;image_url\nhttps://example.com/;https://example.com/a.png\n"
    assert read_csv_urls(text) == ["https://example.com/a.png"]


def test_headerless_csv_uses_the_first_column() -> None:
    text = "https://example.com/a.png,broken\nhttps://example.com/b.png,broken\n"
    assert read_csv_urls(text) == [
        "https://example.com/a.png",
        "https://example.com/b.png",
    ]


def test_domain_and_prefix_queries() -> None:
    bare = parse_domain_query("Example.com")
    assert bare.url == "example.com"
    assert bare.match_type == "domain"
    prefix = parse_domain_query("https://example.com/news/")
    assert prefix.url == "example.com/news/"
    assert prefix.match_type == "prefix"


def test_normalize_strips_fragment_and_rejects_other_schemes() -> None:
    assert normalize_url(" https://example.com/a.png#part ") == "https://example.com/a.png"
    with pytest.raises(ValueError):
        normalize_url("ftp://example.com/a.png")
