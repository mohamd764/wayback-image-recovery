"""Pillow-backed validation that rejects HTML error pages and truncated files."""

from __future__ import annotations

import io
import logging

from PIL import Image

logger = logging.getLogger(__name__)

_AVIF_BRANDS = {b"avif", b"avis"}
_HEIF_BRANDS = {b"heic", b"heix", b"heif", b"mif1", b"msf1"}


def validate_bytes(data: bytes, *, mimetype: str, image_only: bool) -> tuple[bool, str]:
    """Validate downloaded bytes.

    Image captures must decode with Pillow. SVG is accepted as XML because
    Pillow does not decode it. AVIF and HEIF fall back to an ISO-BMFF brand
    check when the installed Pillow build has no plugin for them. Other
    assets are kept when they are not empty HTML error pages.
    """
    if not data:
        return False, "empty"
    mime = (mimetype or "").split(";", 1)[0].strip().lower()
    if image_only or mime.startswith("image/") or _has_raster_magic(data):
        return _validate_image(data, mime)
    if _wayback_interstitial(data):
        return False, "wayback_interstitial"
    if mime not in {"text/html", "application/xhtml+xml"} and _looks_like_html_document(data):
        return False, "html_error_page"
    return True, ""


def _validate_image(data: bytes, mime: str) -> tuple[bool, str]:
    if _looks_like_html_document(data) and not _looks_like_svg(data):
        return False, "html_error_page"
    if mime == "image/svg+xml" or (not mime and _looks_like_svg(data)):
        if _looks_like_svg(data):
            return True, ""
        return False, "invalid_svg"
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.verify()
        with Image.open(io.BytesIO(data)) as image:
            image.load()
    except Exception as exc:
        logger.debug("Pillow rejected %s bytes of %s: %s", len(data), mime or "unknown", exc)
        if _iso_bmff_fallback(data, mime):
            return True, ""
        return False, "truncated_or_not_image"
    return True, ""


def _iso_bmff_fallback(data: bytes, mime: str) -> bool:
    """Accept AVIF/HEIF only when Pillow cannot decode a file that has a real brand."""
    if len(data) < 12 or data[4:8] != b"ftyp":
        return False
    brand = data[8:12]
    if mime == "image/avif":
        return brand in _AVIF_BRANDS
    if mime in {"image/heic", "image/heif"}:
        return brand in _HEIF_BRANDS
    return False


def _has_raster_magic(data: bytes) -> bool:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return True
    if data.startswith(b"\xff\xd8\xff"):
        return True
    if data.startswith((b"GIF87a", b"GIF89a")):
        return True
    if data.startswith(b"BM"):
        return True
    if data.startswith((b"II*\x00", b"MM\x00*")):
        return True
    if len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return True
    return len(data) >= 4 and data[:4] == b"\x00\x00\x01\x00"


def _looks_like_html_document(data: bytes) -> bool:
    head = data[:1024].lstrip().lower()
    return head.startswith((b"<!doctype html", b"<html", b"<head", b"<body"))


def _looks_like_svg(data: bytes) -> bool:
    sample = data[:8192].lower()
    svg_at = sample.find(b"<svg")
    if svg_at < 0:
        return False
    html_at = sample.find(b"<html")
    return html_at < 0 or svg_at < html_at


def _wayback_interstitial(data: bytes) -> bool:
    sample = data[:6000].lower()
    return b"wm-ipp" in sample or (b"wayback machine" in sample and b"not archived" in sample)
