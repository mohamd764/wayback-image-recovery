"""Shared fixtures. Tests must not open live network connections."""

from __future__ import annotations

import logging
import socket

import pytest


@pytest.fixture(autouse=True)
def _reset_logging() -> object:
    """Drop handlers left behind by CLI tests so later logs do not write to a closed stream."""
    yield
    root = logging.getLogger()
    for handler in root.handlers[:]:
        root.removeHandler(handler)
        handler.close()
    root.setLevel(logging.WARNING)


@pytest.fixture(autouse=True)
def _block_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def _blocked(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("live network is disabled in tests")

    monkeypatch.setattr(socket, "create_connection", _blocked)
    monkeypatch.setattr(socket, "getaddrinfo", _blocked)
