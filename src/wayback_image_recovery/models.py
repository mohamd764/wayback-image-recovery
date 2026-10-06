"""Data objects shared by the library and the CLI."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from wayback_image_recovery._version import __version__

DEFAULT_USER_AGENT = (
    f"wayback-image-recovery/{__version__} "
    "(+https://github.com/mohamd764/wayback-image-recovery; archive asset recovery)"
)

CDX_ENDPOINT = "https://web.archive.org/cdx/search/cdx"
WAYBACK_REPLAY_BASE = "https://web.archive.org/web"
CC_COLLINFO_URL = "https://index.commoncrawl.org/collinfo.json"
CC_DATA_BASE = "https://data.commoncrawl.org"
SUCCESS_STATUSES = frozenset({"recovered", "duplicate", "skipped"})


def utc_now() -> str:
    """Return the current UTC time as a compact ISO-8601 string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class RecoveryConfig:
    """Settings for a recovery run.

    ``rate_per_second`` of ``0`` disables the client-side limiter. Leave the
    default in place when talking to the public Internet Archive or Common
    Crawl endpoints.
    """

    output_dir: Path = field(default_factory=lambda: Path("recovered"))
    concurrency: int = 4
    rate_per_second: float = 1.0
    max_retries: int = 4
    timeout: float = 60.0
    user_agent: str = DEFAULT_USER_AGENT
    min_bytes: int = 0
    max_bytes: int = 50 * 1024 * 1024
    flat: bool = False
    resume: bool = True
    mode: str = "latest"
    closest: str | None = None
    mimetypes: tuple[str, ...] = ("image/",)
    status_codes: tuple[str, ...] = ("200",)
    use_wayback: bool = True
    use_common_crawl: bool = True
    scheme_fallback: bool = True
    cc_index_limit: int = 3
    cc_indexes: tuple[str, ...] = ()
    max_urls: int = 10_000
    page_size: int = 500
    validate: bool = True
    dedupe: bool = True
    match_type: str | None = None
    cdx_endpoint: str = CDX_ENDPOINT
    wayback_replay_base: str = WAYBACK_REPLAY_BASE
    cc_collinfo_url: str = CC_COLLINFO_URL
    cc_data_base: str = CC_DATA_BASE

    def __post_init__(self) -> None:
        object.__setattr__(self, "output_dir", Path(self.output_dir))
        object.__setattr__(self, "mimetypes", tuple(self.mimetypes))
        object.__setattr__(self, "status_codes", tuple(self.status_codes))
        object.__setattr__(self, "cc_indexes", tuple(self.cc_indexes))


@dataclass(frozen=True)
class Snapshot:
    """One archived capture that can be downloaded."""

    original_url: str
    timestamp: str
    statuscode: str
    mimetype: str
    digest: str
    source: str
    download_url: str
    offset: int | None = None
    length: int | None = None
    filename: str = ""


@dataclass(frozen=True)
class RecoveryRecord:
    """One row of the manifest."""

    url: str
    source: str = ""
    timestamp: str = ""
    status: str = "error"
    sha256: str = ""
    path: str = ""
    size_bytes: int = 0
    mimetype: str = ""
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "source": self.source,
            "timestamp": self.timestamp,
            "status": self.status,
            "sha256": self.sha256,
            "path": self.path,
            "bytes": self.size_bytes,
            "mimetype": self.mimetype,
            "error": self.error,
        }


@dataclass(frozen=True)
class RecoveryReport:
    """Result of a recovery run."""

    records: list[RecoveryRecord]
    summary: dict[str, int]
    output_dir: Path
    generated_at: str = ""

    def __post_init__(self) -> None:
        if not self.generated_at:
            object.__setattr__(self, "generated_at", utc_now())

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool": "wayback-image-recovery",
            "version": __version__,
            "generated_at": self.generated_at,
            "summary": self.summary,
            "records": [record.to_dict() for record in self.records],
        }


@dataclass(frozen=True)
class DomainQuery:
    """A CDX domain or prefix query derived from user input."""

    url: str
    match_type: str
