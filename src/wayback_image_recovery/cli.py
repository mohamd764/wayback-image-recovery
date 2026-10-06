"""Command-line interface for wayback-image-recovery."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from wayback_image_recovery._version import __version__
from wayback_image_recovery.errors import ArchiveError
from wayback_image_recovery.inputs import read_csv_file, read_url_lines
from wayback_image_recovery.manifest import format_summary
from wayback_image_recovery.models import (
    CC_COLLINFO_URL,
    CC_DATA_BASE,
    CDX_ENDPOINT,
    WAYBACK_REPLAY_BASE,
    RecoveryConfig,
    RecoveryReport,
)
from wayback_image_recovery.recover import run

MAIN_EXAMPLES = """
examples:
  wir recover --domain example.com -o ./recovered
  wir recover urls.txt --closest 2020-01-01 -o ./recovered
  wir recover --csv broken.csv --min-bytes 1024 --flat
  cat urls.txt | wir recover - -o ./recovered
""".strip("\n")

RECOVER_EXAMPLES = """
examples:
  Look up every image capture under a domain, then download the latest raw
  snapshot (Wayback id_ URLs, Common Crawl WARC ranges as a fallback):

    wir recover --domain example.com -o ./recovered

  Recover an explicit list, preferring the capture closest to a date:

    wir recover urls.txt --closest 20200101 --output ./recovered

  A CSV export of broken image URLs (a url, image, or src column is detected):

    wir recover --csv broken-images.csv --min-bytes 2048

  Read URLs from stdin and keep the original directory layout:

    wir recover - -o ./recovered < urls.txt

  Re-run the same command after a failure. URLs already on disk are skipped.

    wir recover urls.txt -o ./recovered
""".strip("\n")


class _Formatter(argparse.RawDescriptionHelpFormatter, argparse.ArgumentDefaultsHelpFormatter):
    """Keep example blocks intact and show default values."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wayback-image-recovery",
        description=(
            "Recover lost website images and other assets from the Internet "
            "Archive Wayback Machine and Common Crawl."
        ),
        epilog=MAIN_EXAMPLES,
        formatter_class=_Formatter,
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"wayback-image-recovery {__version__}",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    recover = subparsers.add_parser(
        "recover",
        help="Find archived captures and download the original bytes",
        description=(
            "Resolve each URL to a latest or closest capture, download the "
            "original bytes, validate images, and write a manifest."
        ),
        epilog=RECOVER_EXAMPLES,
        formatter_class=_Formatter,
    )
    recover.add_argument(
        "urls_file",
        nargs="?",
        help="Newline-delimited URL file, or - to read stdin",
    )
    recover.add_argument("--domain", help="Discover captures for this host or URL prefix")
    recover.add_argument("--csv", dest="csv_path", help="CSV file of asset URLs")
    recover.add_argument(
        "-o",
        "--output",
        default="recovered",
        help="Directory for recovered files and the manifest",
    )
    recover.add_argument(
        "--match-type",
        choices=("domain", "host", "prefix", "exact"),
        help="CDX match type for --domain. Defaults to domain, or prefix when a path is given",
    )
    recover.add_argument(
        "--closest",
        metavar="TIMESTAMP",
        help="Prefer the capture closest to this UTC timestamp (YYYYMMDD or YYYYMMDDhhmmss)",
    )
    recover.add_argument(
        "--mimetype",
        action="append",
        default=None,
        help="MIME type or prefix to keep (repeatable). 'any' disables the filter",
    )
    recover.add_argument(
        "--status-code",
        action="append",
        default=None,
        help="HTTP status to keep (repeatable). 'any' disables the filter",
    )
    recover.add_argument(
        "--no-wayback", action="store_true", help="Do not query the Wayback CDX API"
    )
    recover.add_argument(
        "--no-common-crawl",
        action="store_true",
        help="Do not use Common Crawl when Wayback has no usable capture",
    )
    recover.add_argument(
        "--no-scheme-fallback",
        action="store_true",
        help="Do not retry a lookup with http and https swapped",
    )
    recover.add_argument(
        "--cc-index-limit",
        type=int,
        default=3,
        help="How many of the newest Common Crawl indexes to search",
    )
    recover.add_argument(
        "--cc-index",
        action="append",
        default=None,
        help="Explicit Common Crawl index API URL (repeatable, skips collinfo)",
    )
    recover.add_argument("--concurrency", type=int, default=4, help="URLs downloaded at once")
    recover.add_argument(
        "--rate",
        type=float,
        default=1.0,
        help="Maximum request starts per second. 0 disables the limiter",
    )
    recover.add_argument(
        "--retries", type=int, default=4, help="Retries after a timeout or 429/5xx"
    )
    recover.add_argument(
        "--timeout", type=float, default=60.0, help="Per-request timeout in seconds"
    )
    recover.add_argument("--user-agent", default=None, help="User-Agent sent to archive endpoints")
    recover.add_argument(
        "--min-bytes", type=int, default=0, help="Reject payloads smaller than this"
    )
    recover.add_argument(
        "--max-bytes",
        type=int,
        default=50 * 1024 * 1024,
        help="Reject payloads larger than this",
    )
    recover.add_argument(
        "--flat",
        action="store_true",
        help="Write content-addressed filenames instead of the original URL path",
    )
    recover.add_argument(
        "--no-resume",
        action="store_true",
        help="Ignore manifest.jsonl and download again",
    )
    recover.add_argument("--no-validate", action="store_true", help="Skip Pillow and HTML checks")
    recover.add_argument(
        "--no-dedupe", action="store_true", help="Write a copy even when the hash exists"
    )
    recover.add_argument(
        "--max-urls", type=int, default=10_000, help="Cap URLs accepted from --domain"
    )
    recover.add_argument("--page-size", type=int, default=500, help="CDX discovery page size")
    recover.add_argument(
        "--cdx-endpoint",
        default=CDX_ENDPOINT,
        help="Wayback CDX endpoint, for a mirror or a test double",
    )
    recover.add_argument(
        "--wayback-replay-base",
        default=WAYBACK_REPLAY_BASE,
        help="Base used to build id_ replay URLs",
    )
    recover.add_argument(
        "--cc-collinfo-url", default=CC_COLLINFO_URL, help="Common Crawl collinfo JSON URL"
    )
    recover.add_argument(
        "--cc-data-url", default=CC_DATA_BASE, help="Base URL for Common Crawl WARC files"
    )
    recover.add_argument(
        "--strict",
        action="store_true",
        help="Exit 1 unless every URL is recovered, duplicate, or skipped",
    )
    recover.add_argument(
        "-v", "--verbose", action="store_true", help="Log request retries and debug detail"
    )
    recover.add_argument("-q", "--quiet", action="store_true", help="Hide per-URL progress")
    recover.set_defaults(func=command_recover)
    return parser


def command_recover(args: argparse.Namespace) -> int:
    setup_logging(verbose=args.verbose, quiet=args.quiet)
    config = config_from_args(args)
    urls = collect_urls(args)
    if not urls and not args.domain:
        print(
            "Provide a URL file, --csv, --domain, or - for stdin.",
            file=sys.stderr,
        )
        return 2
    report = run(urls, domain=args.domain, config=config)
    print(format_summary(report), file=sys.stderr)
    return exit_code(report, strict=args.strict)


def config_from_args(args: argparse.Namespace) -> RecoveryConfig:
    kwargs: dict[str, object] = {
        "output_dir": Path(args.output),
        "concurrency": args.concurrency,
        "rate_per_second": args.rate,
        "max_retries": args.retries,
        "timeout": args.timeout,
        "min_bytes": args.min_bytes,
        "max_bytes": args.max_bytes,
        "flat": args.flat,
        "resume": not args.no_resume,
        "mode": "closest" if args.closest else "latest",
        "closest": args.closest,
        "mimetypes": tuple(args.mimetype) if args.mimetype else ("image/",),
        "status_codes": tuple(args.status_code) if args.status_code else ("200",),
        "use_wayback": not args.no_wayback,
        "use_common_crawl": not args.no_common_crawl,
        "scheme_fallback": not args.no_scheme_fallback,
        "cc_index_limit": args.cc_index_limit,
        "cc_indexes": tuple(args.cc_index or ()),
        "max_urls": args.max_urls,
        "page_size": args.page_size,
        "validate": not args.no_validate,
        "dedupe": not args.no_dedupe,
        "match_type": args.match_type,
        "cdx_endpoint": args.cdx_endpoint,
        "wayback_replay_base": args.wayback_replay_base,
        "cc_collinfo_url": args.cc_collinfo_url,
        "cc_data_base": args.cc_data_url,
    }
    if args.user_agent:
        kwargs["user_agent"] = args.user_agent
    return RecoveryConfig(**kwargs)  # type: ignore[arg-type]


def collect_urls(args: argparse.Namespace) -> list[str]:
    urls: list[str] = []
    if args.csv_path:
        path = Path(args.csv_path)
        if not path.is_file():
            raise ValueError(f"CSV file not found: {path}")
        urls.extend(read_csv_file(path))
    if args.urls_file:
        if args.urls_file == "-":
            urls.extend(read_url_lines(sys.stdin.read()))
        else:
            path = Path(args.urls_file)
            if not path.is_file():
                raise ValueError(f"URL file not found: {path}")
            urls.extend(read_url_lines(path.read_text(encoding="utf-8-sig")))
    return urls


def exit_code(report: RecoveryReport, *, strict: bool) -> int:
    """Return 1 when the run recorded transport errors, or when --strict is unsatisfied."""
    if report.summary.get("error", 0):
        return 1
    if not strict:
        return 0
    ok = {"recovered", "duplicate", "skipped"}
    if any(record.status not in ok for record in report.records):
        return 1
    return 0


def setup_logging(*, verbose: bool, quiet: bool) -> None:
    if verbose and quiet:
        raise ValueError("choose either --verbose or --quiet")
    level = logging.DEBUG if verbose else logging.WARNING if quiet else logging.INFO
    logging.basicConfig(level=level, format="%(message)s", force=True)


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and run a command. Returns a process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except ArchiveError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
