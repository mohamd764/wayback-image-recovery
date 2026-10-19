"""Recover an explicit URL list at a polite rate.

Run from the repository root after installing the package:

    python examples/recover_url_list.py
    python examples/recover_url_list.py --domain example.com --output ./recovered

``main(client=None, output_dir=...)`` is what the tests call. Omit ``client``
when you run the script: the library then uses its rate-limited client.
Pass a client of your own for a mirror or a test double; injected clients are
used as-is and are not rate-limited again.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from wayback_image_recovery import RecoveryConfig, RecoveryReport, discover_urls, recover_urls
from wayback_image_recovery.http import HttpClient

HERE = Path(__file__).resolve().parent
DEFAULT_URLS = HERE / "urls.txt"
DEFAULT_OUTPUT = Path("recovered")


def read_url_file(path: Path) -> list[str]:
    """Return non-empty, non-comment lines from a URL list."""
    urls: list[str] = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        urls.append(stripped)
    return urls


def build_config(output_dir: Path, rate_per_second: float = 1.0) -> RecoveryConfig:
    """Settings for a real run against the public archives.

    One request start per second is the polite default. A rate of 0, or
    anything above 5/s, is for your own mirror or when you have permission.
    """
    return RecoveryConfig(output_dir=output_dir, rate_per_second=rate_per_second)


def main(
    client: HttpClient | None = None,
    output_dir: Path | None = None,
    urls_path: Path | None = None,
    domain: str | None = None,
    rate_per_second: float = 1.0,
) -> RecoveryReport:
    """Recover URLs from a text file, from domain discovery, or from both.

    With neither ``urls_path`` nor ``domain``, the sample ``urls.txt`` is used.
    ``--domain`` on its own discovers image URLs and does not also read the file.
    """
    destination = output_dir if output_dir is not None else DEFAULT_OUTPUT
    config = build_config(destination, rate_per_second)
    urls: list[str] = []
    if domain:
        # discover_urls only lists captures. Each URL is resolved again below.
        urls.extend(asyncio.run(discover_urls(domain, config, client=client)))
    path = _urls_path(urls_path, domain)
    if path is not None:
        urls.extend(read_url_file(path))
    if not urls:
        raise SystemExit("No URLs to recover. Pass a URL file or --domain.")
    report = asyncio.run(recover_urls(urls, config, client=client))
    summary = report.summary
    print(
        "recovered={recovered} duplicate={duplicate} skipped={skipped} "
        "not_found={not_found} invalid={invalid} error={error}".format(**summary)
    )
    print(f"Manifest: {report.output_dir / 'manifest.csv'}")
    return report


def _urls_path(urls_path: Path | None, domain: str | None) -> Path | None:
    if urls_path is not None:
        return urls_path
    if domain:
        return None
    return DEFAULT_URLS


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Recover a list of archived image URLs.")
    parser.add_argument(
        "--urls",
        type=Path,
        default=None,
        help="Text file of URLs, one per line (default: examples/urls.txt)",
    )
    parser.add_argument(
        "--domain",
        default=None,
        help="Discover image URLs for this host with discover_urls before recovering",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--rate",
        type=float,
        default=1.0,
        help="Request starts per second (default 1; keep this for public archives)",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    args = _parse_args()
    main(
        output_dir=args.output,
        urls_path=args.urls,
        domain=args.domain,
        rate_per_second=args.rate,
    )
