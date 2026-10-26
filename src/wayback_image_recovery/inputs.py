"""Read URL lists, CSV files, sitemaps, and domain queries."""

from __future__ import annotations

import csv
import io
import mimetypes
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from xml.parsers import expat

from wayback_image_recovery.matchers import mime_allowed
from wayback_image_recovery.models import DomainQuery

SITEMAP_NS = "http://www.sitemaps.org/schemas/sitemap/0.9"
IMAGE_NS = "http://www.google.com/schemas/sitemap-image/1.1"
_SITEMAP_LOC = f"{{{SITEMAP_NS}}}loc"
_IMAGE_LOC = f"{{{IMAGE_NS}}}loc"
_IMAGE_ELEMENT = f"{{{IMAGE_NS}}}image"

# Suffixes this project already treats as images. ``mimetypes`` is consulted
# first; this set covers types the standard table omits on older Pythons.
_IMAGE_SUFFIXES = frozenset(
    {
        ".apng",
        ".avif",
        ".bmp",
        ".gif",
        ".heic",
        ".heif",
        ".ico",
        ".jfif",
        ".jp2",
        ".jpe",
        ".jpeg",
        ".jpg",
        ".jxl",
        ".png",
        ".svg",
        ".svgz",
        ".tif",
        ".tiff",
        ".webp",
    }
)

_DTD_MESSAGE = (
    "sitemap XML must not contain a DTD (external entities and entity expansion are disabled)"
)

_URL_COLUMNS = ("url", "uri", "image", "image_url", "src", "link", "original")
_MATCH_TYPES = {"domain", "host", "prefix", "exact"}


def read_url_lines(text: str) -> list[str]:
    """Return non-comment, non-empty lines from a URL list."""
    urls: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        urls.append(stripped)
    return urls


def read_csv_urls(text: str) -> list[str]:
    """Return URLs from a CSV body.

    A header row is recognized when it contains a known column name such as
    ``url`` or ``image_url``. Headerless files use the first column. Comma,
    semicolon, and tab delimiters are accepted.
    """
    sample = text.lstrip("\ufeff")
    if not sample.strip():
        return []
    lines = [
        line for line in sample.splitlines() if line.strip() and not line.lstrip().startswith("#")
    ]
    if not lines:
        return []
    dialect = _sniff_dialect("\n".join(lines[:20]))
    reader = csv.reader(io.StringIO("\n".join(lines)), dialect)
    rows = [row for row in reader if any(cell.strip() for cell in row)]
    if not rows:
        return []
    header = [cell.strip().lower() for cell in rows[0]]
    header_index = _header_index(header)
    first_cell = rows[0][0] if rows[0] else ""
    if header_index is not None and not _looks_like_url(first_cell):
        index = header_index
        data_rows = rows[1:]
    else:
        index = 0
        data_rows = rows
    urls: list[str] = []
    for row in data_rows:
        if index >= len(row):
            continue
        value = row[index].strip()
        if value and not value.startswith("#"):
            urls.append(value)
    return urls


def read_csv_file(path: Path) -> list[str]:
    return read_csv_urls(path.read_text(encoding="utf-8-sig"))


def read_sitemap_file(path: Path) -> list[str]:
    """Return image URLs from a local sitemap XML file."""
    return read_sitemap_xml(path.read_bytes())


def read_sitemap_xml(source: str | bytes) -> list[str]:
    """Return image URLs from a sitemap document.

    Image sitemaps contribute every ``image:loc``. Plain sitemaps contribute
    ``loc`` values whose path looks like an image; page URLs are ignored.
    A sitemap index raises ``ValueError`` listing the child sitemap URLs.
    Nothing is fetched from the network.
    """
    data = source.encode("utf-8") if isinstance(source, str) else source
    root = _parse_sitemap(data)
    if _local_name(root.tag) == "sitemapindex":
        raise ValueError(_sitemap_index_message(_loc_texts(root)))
    return _collect_image_urls(root)


def path_looks_like_image(url: str) -> bool:
    """Return True when ``url``'s path looks like an image file.

    The suffix is mapped with :mod:`mimetypes` and checked with the same
    ``image/`` prefix rule as :func:`wayback_image_recovery.matchers.mime_allowed`.
    """
    candidate = url.strip()
    if not candidate:
        return False
    guessed, _encoding = mimetypes.guess_type(candidate)
    if guessed is not None and mime_allowed(guessed, ("image/",)):
        return True
    return Path(urlsplit(candidate).path).suffix.lower() in _IMAGE_SUFFIXES


def parse_domain_query(value: str, match_type: str | None = None) -> DomainQuery:
    """Turn ``example.com`` or a URL prefix into a CDX query."""
    raw = value.strip()
    if not raw:
        raise ValueError("domain is empty")
    if match_type is not None and match_type not in _MATCH_TYPES:
        raise ValueError(f"match type must be one of: {', '.join(sorted(_MATCH_TYPES))}")
    if "://" not in raw and "/" not in raw and " " not in raw:
        host, path = raw, ""
    else:
        candidate = raw if "://" in raw else "https://" + raw
        parts = urlsplit(candidate)
        host = parts.netloc
        path = parts.path
    host = host.strip().lower()
    if "@" in host:
        host = host.split("@", 1)[1]
    if not host or any(char.isspace() for char in host):
        raise ValueError(f"invalid domain {value!r}")
    if path in {"", "/"}:
        return DomainQuery(url=host, match_type=match_type or "domain")
    prefix = host + path
    return DomainQuery(url=prefix, match_type=match_type or "prefix")


def normalize_url(url: str) -> str:
    """Strip whitespace and fragments, and require an http(s) URL."""
    raw = url.strip()
    parts = urlsplit(raw)
    if parts.scheme.lower() not in {"http", "https"}:
        raise ValueError(f"unsupported URL scheme: {parts.scheme or '(none)'}")
    if not parts.netloc:
        raise ValueError("URL is missing a host")
    return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))


def dedupe_preserve(urls: list[str]) -> list[str]:
    """Drop repeated URLs, keeping the first occurrence."""
    seen: set[str] = set()
    ordered: list[str] = []
    for url in urls:
        if url in seen:
            continue
        seen.add(url)
        ordered.append(url)
    return ordered


def _sniff_dialect(sample: str) -> csv.Dialect:
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        return csv.excel


def _header_index(header: list[str]) -> int | None:
    for name in _URL_COLUMNS:
        if name in header:
            return header.index(name)
    return None


def _looks_like_url(value: str) -> bool:
    stripped = value.strip().lower()
    return stripped.startswith("http://") or stripped.startswith("https://")


def _parse_sitemap(data: bytes) -> ET.Element:
    """Parse sitemap bytes and refuse DTDs.

    ``xml.etree.ElementTree`` expands internal entities, and its accelerated
    parser does not expose the expat hooks needed to stop that. Parsing with
    expat directly aborts on a DTD before an entity is expanded or an external
    entity is fetched. The tree is still an ElementTree.
    """
    target = ET.TreeBuilder()
    parser = expat.ParserCreate(None, "}")
    parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
    parser.buffer_text = 1
    parser.ordered_attributes = 1
    names: dict[str, str] = {}

    def fixname(key: str) -> str:
        name = names.get(key)
        if name is None:
            name = "{" + key if "}" in key else key
            names[key] = name
        return name

    def start(tag: str, attr_list: list[str]) -> None:
        attrib: dict[str, str] = {}
        for index in range(0, len(attr_list), 2):
            attrib[fixname(attr_list[index])] = attr_list[index + 1]
        target.start(fixname(tag), attrib)

    def reject_dtd(*_args: object) -> None:
        raise ValueError(_DTD_MESSAGE)

    parser.StartDoctypeDeclHandler = reject_dtd
    parser.EntityDeclHandler = reject_dtd
    parser.UnparsedEntityDeclHandler = reject_dtd
    parser.ExternalEntityRefHandler = reject_dtd
    parser.StartElementHandler = start
    parser.EndElementHandler = lambda tag: target.end(fixname(tag))
    parser.CharacterDataHandler = target.data
    try:
        parser.Parse(data, True)
    except ValueError:
        raise
    except expat.ExpatError as exc:
        raise ValueError(f"could not parse sitemap XML: {exc}") from exc
    return target.close()


def _collect_image_urls(root: ET.Element) -> list[str]:
    parents = {child: parent for parent in root.iter() for child in list(parent)}
    urls: list[str] = []
    for elem in root.iter():
        if not isinstance(elem.tag, str):
            continue
        text = _element_text(elem)
        if not text:
            continue
        parent = parents.get(elem)
        if _is_image_loc(elem, parent) or (_is_plain_loc(elem) and path_looks_like_image(text)):
            urls.append(text)
    return urls


def _is_image_loc(elem: ET.Element, parent: ET.Element | None) -> bool:
    if elem.tag == _IMAGE_LOC:
        return True
    if _local_name(elem.tag) != "loc" or parent is None:
        return False
    return parent.tag in {_IMAGE_ELEMENT, "image"}


def _is_plain_loc(elem: ET.Element) -> bool:
    return elem.tag in {_SITEMAP_LOC, "loc"}


def _loc_texts(root: ET.Element) -> list[str]:
    texts: list[str] = []
    for elem in root.iter():
        if isinstance(elem.tag, str) and _local_name(elem.tag) == "loc":
            text = _element_text(elem)
            if text:
                texts.append(text)
    return texts


def _element_text(elem: ET.Element) -> str:
    return (elem.text or "").strip()


def _local_name(tag: str) -> str:
    if tag.startswith("{") and "}" in tag:
        return tag.rsplit("}", 1)[1]
    return tag


def _sitemap_index_message(child_urls: list[str]) -> str:
    if child_urls:
        listed = "\n".join(f"  {url}" for url in child_urls)
    else:
        listed = "  (no child sitemap URLs were listed)"
    return (
        "sitemap index files are not fetched; only a local sitemap is read, "
        "and this tool does not crawl the live site. "
        "Download one child sitemap and pass that file to --sitemap:\n"
        f"{listed}"
    )
