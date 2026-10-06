"""Image and asset validation."""

from __future__ import annotations

from tests.helpers import png_bytes

from wayback_image_recovery.validate import validate_bytes


def test_png_is_valid() -> None:
    ok, reason = validate_bytes(png_bytes(), mimetype="image/png", image_only=True)
    assert ok
    assert reason == ""


def test_html_error_page_is_rejected() -> None:
    body = b"<!DOCTYPE html><html><body>Not an image</body></html>"
    ok, reason = validate_bytes(body, mimetype="image/jpeg", image_only=True)
    assert not ok
    assert reason == "html_error_page"


def test_truncated_png_is_rejected() -> None:
    ok, reason = validate_bytes(png_bytes()[:24], mimetype="image/png", image_only=True)
    assert not ok
    assert reason == "truncated_or_not_image"


def test_svg_is_accepted_without_pillow() -> None:
    svg = b"<?xml version='1.0'?><svg xmlns='http://www.w3.org/2000/svg'></svg>"
    ok, _reason = validate_bytes(svg, mimetype="image/svg+xml", image_only=True)
    assert ok


def test_html_mentioning_the_toolbar_is_not_an_asset() -> None:
    body = b"<html><body>The Wayback Machine has not archived that URL. wm-ipp</body></html>"
    ok, reason = validate_bytes(body, mimetype="text/html", image_only=False)
    assert not ok
    assert reason == "wayback_interstitial"


def test_pdf_bytes_pass_when_images_are_not_required() -> None:
    ok, _reason = validate_bytes(b"%PDF-1.7\n", mimetype="application/pdf", image_only=False)
    assert ok


def test_avif_brand_is_accepted_when_pillow_cannot_decode_it() -> None:
    data = b"\x00\x00\x00\x18ftypavif" + b"\x00" * 32
    ok, reason = validate_bytes(data, mimetype="image/avif", image_only=True)
    assert ok
    assert reason == ""


def test_empty_is_invalid() -> None:
    ok, reason = validate_bytes(b"", mimetype="image/png", image_only=True)
    assert not ok
    assert reason == "empty"
