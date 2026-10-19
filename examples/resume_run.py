"""Recover a URL list, then run it again to show resume.

The second pass uses the same output directory. URLs whose files are still
on disk are recorded as ``skipped`` and are not downloaded again. Rows with
status ``error`` are retried; ``not_found`` is not.

    python examples/resume_run.py
    python examples/resume_run.py --output ./recovered
"""

from __future__ import annotations

import argparse
from pathlib import Path

from wayback_image_recovery import RecoveryConfig, RecoveryReport, run
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
    """Resume is on by default. The rate stays at one request start per second."""
    return RecoveryConfig(output_dir=output_dir, rate_per_second=rate_per_second, resume=True)


def main(
    client: HttpClient | None = None,
    output_dir: Path | None = None,
    urls_path: Path | None = None,
    rate_per_second: float = 1.0,
) -> tuple[RecoveryReport, RecoveryReport]:
    """Run twice. The second report should be mostly ``skipped`` when files remain."""
    destination = output_dir if output_dir is not None else DEFAULT_OUTPUT
    source = urls_path if urls_path is not None else DEFAULT_URLS
    urls = read_url_file(source)
    if not urls:
        raise SystemExit(f"No URLs in {source}")
    config = build_config(destination, rate_per_second)
    first = run(urls, config=config, client=client)
    # Same directory and the same client. Resume reads manifest.jsonl and skips
    # files that are still present, so this pass should not fetch them again.
    second = run(urls, config=config, client=client)
    print(f"first: {first.summary}")
    print(f"second (resume): {second.summary}")
    print(f"Manifest: {destination / 'manifest.csv'}")
    return first, second


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Recover a URL list, then resume that run.")
    parser.add_argument("--urls", type=Path, default=DEFAULT_URLS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--rate", type=float, default=1.0)
    return parser.parse_args(argv)


if __name__ == "__main__":
    args = _parse_args()
    main(output_dir=args.output, urls_path=args.urls, rate_per_second=args.rate)
