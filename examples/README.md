# Examples

Short scripts that call the library. From a checkout:

```bash
python -m pip install -e ".[dev]"
python examples/recover_url_list.py
```

The sample inputs use `https://example.com/...` placeholders. Replace them with assets you have the right to restore before a real run. Each script exposes `main(client=None, output_dir=...)`. The command line uses the real client, which is rate-limited and retried. A test, or a mirror, can pass its own client; injected clients are used as-is.

## `recover_url_list.py`

Reads `urls.txt` (one URL per line; blank lines and `#` comments are ignored) and recovers the list with `recover_urls`. The config sets `rate_per_second=1.0`.

```bash
python examples/recover_url_list.py
python examples/recover_url_list.py --output ./recovered
```

`--domain` calls `discover_urls` for that host, then recovers what it found. On its own it does not also read `urls.txt`. Pass `--urls` as well when you want both.

```bash
python examples/recover_url_list.py --domain example.com --output ./recovered
```

## `recover_from_csv.py`

Reads `broken-images.csv`. A header row names the URL column `url`, `image_url`, or `src` (same idea as `wir recover --csv`). `run()` writes the manifest, and the script reads `manifest.csv` plus the summary in `manifest.json`.

```bash
python examples/recover_from_csv.py
python examples/recover_from_csv.py --csv examples/broken-images.csv --output ./recovered
```

## `resume_run.py`

Recovers `urls.txt`, then runs the same list again into the same directory with `resume=True` (also the library default). Files still on disk come back as `skipped` and are not downloaded again.

```bash
python examples/resume_run.py
python examples/resume_run.py --output ./recovered
```

## The archives

The Wayback Machine and Common Crawl are shared infrastructure. These scripts keep the library default of one request start per second. Please read the [Internet Archive Terms of Use](https://archive.org/about/terms.php) and the [Common Crawl Terms of Use](https://commoncrawl.org/terms-of-use) before you publish recovered files. A capture being public is not the same as a license to reuse it. Prefer restoring assets you own, operated, or have rights to. Do not raise `--rate` against the public archives unless you have permission; a rate of 0 or above 5 requests/second is for your own mirror.
