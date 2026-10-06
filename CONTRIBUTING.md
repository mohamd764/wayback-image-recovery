# Contributing

Thanks for helping improve wayback-image-recovery. The project is maintained by [Mohamed Fouad](https://github.com/mohamd764).

## Setup

```bash
git clone https://github.com/mohamd764/wayback-image-recovery.git
cd wayback-image-recovery
python -m pip install -e ".[dev]"
```

Python 3.9 through 3.12 are supported. Use a virtual environment if you are not already in one.

## Checks

```bash
ruff check .
ruff format --check .
pytest
```

`ruff format .` rewrites formatting. CI runs all three.

Tests must not contact the Internet Archive, Common Crawl, or any other network. `tests/conftest.py` blocks `socket.create_connection` and `socket.getaddrinfo`. Exercise HTTP behavior with `tests/helpers.py` (`FakeHttp`) or by injecting your own client into `recover_urls` / `run`. Do not add a marker that re-enables live calls in the default suite.

## Pull requests

- Open a pull request against `main` with a short description of the behavior change.
- Add or update tests for the path you touched.
- Update `CHANGELOG.md` under the next version when the change is user-visible.
- Keep new dependencies limited. `aiohttp` and Pillow are the runtime dependencies on purpose.
- Do not commit recovered assets, archive dumps, credentials, or anyone else's URL lists.

## Good first issues

The roadmap in the README is a list of tasks that are meant to be approachable: a dry run mode, a progress bar, retrying only `error` rows, sitemap input, an SQLite manifest, and a cheaper domain-discovery lookup. Comment on an issue before you start if one already exists, or open a feature request and mention which roadmap item you want.

## Reporting bugs

Use the bug report template and include the command you ran, the Python version, and the status column from `manifest.csv`. Redact URLs if they are private; `https://example.com/...` is enough to show the shape of the problem.
