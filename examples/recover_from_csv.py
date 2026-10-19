"""Recover image URLs from a CSV export and print the manifest summary.

The CSV needs a ``url``, ``image_url``, or ``src`` column, the same names the
``wir recover --csv`` command accepts. Other columns are ignored.

    python examples/recover_from_csv.py
    python examples/recover_from_csv.py --csv examples/broken-images.csv --output ./recovered

The sample file uses example.com placeholders. Replace them before a real run.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from wayback_image_recovery import RecoveryConfig, run
from wayback_image_recovery.http import HttpClient

HERE = Path(__file__).resolve().parent
DEFAULT_CSV = HERE / "broken-images.csv"
DEFAULT_OUTPUT = Path("recovered")
_URL_COLUMNS = ("url", "image_url", "src")


def read_broken_image_csv(path: Path) -> list[str]:
    """Return asset URLs from a headered CSV."""
    lines = [
        line
        for line in path.read_text(encoding="utf-8-sig").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if not lines:
        return []
    reader = csv.DictReader(lines)
    if not reader.fieldnames:
        raise SystemExit(f"{path} is missing a header row")
    fields = {name.strip().lower(): name for name in reader.fieldnames if name}
    column = next((fields[name] for name in _URL_COLUMNS if name in fields), None)
    if column is None:
        raise SystemExit(f"{path} needs a url, image_url, or src column")
    urls: list[str] = []
    for row in reader:
        value = (row.get(column) or "").strip()
        if value and not value.startswith("#"):
            urls.append(value)
    return urls


def build_config(output_dir: Path, rate_per_second: float = 1.0) -> RecoveryConfig:
    """Polite settings. See ``examples/recover_url_list.py`` for the rate note."""
    return RecoveryConfig(output_dir=output_dir, rate_per_second=rate_per_second)


def read_manifest(output_dir: Path) -> dict[str, int]:
    """Read ``manifest.csv`` and the summary object in ``manifest.json``."""
    csv_path = output_dir / "manifest.csv"
    json_path = output_dir / "manifest.json"
    with csv_path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    summary = {str(key): int(value) for key, value in payload["summary"].items()}
    print(f"Manifest: {csv_path} ({len(rows)} row(s))")
    print(
        "recovered={recovered} duplicate={duplicate} skipped={skipped} "
        "not_found={not_found} invalid={invalid} error={error}".format(**summary)
    )
    return summary


def main(
    client: HttpClient | None = None,
    output_dir: Path | None = None,
    csv_path: Path | None = None,
    rate_per_second: float = 1.0,
) -> dict[str, int]:
    """Recover the CSV with ``run()`` and return the summary read back from disk."""
    destination = output_dir if output_dir is not None else DEFAULT_OUTPUT
    source = csv_path if csv_path is not None else DEFAULT_CSV
    urls = read_broken_image_csv(source)
    if not urls:
        raise SystemExit(f"No URLs in {source}")
    config = build_config(destination, rate_per_second)
    # run() is the synchronous helper. It cannot be called from a running loop.
    run(urls, config=config, client=client)
    return read_manifest(destination)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Recover broken image URLs listed in a CSV.")
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--rate", type=float, default=1.0)
    return parser.parse_args(argv)


if __name__ == "__main__":
    args = _parse_args()
    main(output_dir=args.output, csv_path=args.csv, rate_per_second=args.rate)
