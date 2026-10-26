# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project uses [semantic versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `wir recover --sitemap PATH` reads image URLs from a local sitemap XML file. Image sitemaps contribute every `image:loc`. Plain sitemaps contribute `loc` entries whose path looks like an image; page URLs are ignored. A sitemap index is rejected and its child sitemap URLs are listed; nothing is fetched from the web.

## [0.1.0] - 2026-10-06

### Added

- Command-line recovery from a domain, a URL list (file or stdin), or a CSV of asset URLs.
- Wayback CDX lookup for the latest capture, or the capture closest to a timestamp, using `id_` raw replay.
- Common Crawl fallback: newest indexes from `collinfo.json`, then an HTTP range read of the WARC record.
- Async downloads with a concurrency cap, a request-start rate limit, and retries with exponential backoff and jitter.
- Pillow validation that rejects HTML error pages and truncated images, plus optional minimum and maximum sizes.
- Content-hash dedupe, original path layout or flat output, and resume from `manifest.jsonl`.
- `manifest.csv` and `manifest.json` reports (`url`, `source`, `timestamp`, `status`, `sha256`, `path`).
- Importable API (`recover_urls`, `discover_urls`, `run`) and console scripts `wayback-image-recovery` and `wir`.
- pytest suite with a fake HTTP client, ruff, and GitHub Actions on Python 3.9–3.12.

[0.1.0]: https://github.com/mohamd764/wayback-image-recovery/releases/tag/v0.1.0
