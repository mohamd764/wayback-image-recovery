"""Errors raised while talking to archives."""

from __future__ import annotations


class ArchiveError(Exception):
    """Base error for archive lookups and downloads."""


class TransportError(ArchiveError):
    """The HTTP client could not complete a request."""


class ArchiveResponseError(ArchiveError):
    """An archive responded, but the response cannot be used."""


class ResponseTooLarge(ArchiveError):
    """A response exceeded the configured byte limit."""

    def __init__(self, limit: int) -> None:
        super().__init__(f"response exceeds max-bytes ({limit})")
        self.limit = limit


class SnapshotNotFound(ArchiveError):
    """A resolved snapshot URL is no longer available."""
