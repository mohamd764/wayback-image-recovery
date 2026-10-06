"""Content-addressed writes, dedupe, and resume checks."""

from __future__ import annotations

import asyncio
import hashlib
import os
from pathlib import Path

from wayback_image_recovery.matchers import expects_images
from wayback_image_recovery.models import SUCCESS_STATUSES, RecoveryConfig, RecoveryRecord, Snapshot
from wayback_image_recovery.paths import flat_relative_path, is_inside, safe_relative_path
from wayback_image_recovery.validate import validate_bytes


class ContentStore:
    """Write recovered bytes under the output directory and remember hashes."""

    def __init__(self, config: RecoveryConfig) -> None:
        self.config = config
        self.output = config.output_dir
        self._lock = asyncio.Lock()
        self.hash_to_rel: dict[str, str] = {}

    def seed(self, prior: dict[str, RecoveryRecord]) -> None:
        """Index hashes whose files are still present and match the manifest."""
        for record in prior.values():
            if record.status not in SUCCESS_STATUSES or not record.sha256 or not record.path:
                continue
            dest = self.output / record.path
            if not is_inside(self.output, dest) or not dest.is_file():
                continue
            try:
                digest = hashlib.sha256(dest.read_bytes()).hexdigest()
            except OSError:
                continue
            if digest == record.sha256:
                self.hash_to_rel.setdefault(digest, record.path)

    def reusable(self, url: str, prior: dict[str, RecoveryRecord]) -> RecoveryRecord | None:
        """Return a skipped record when a previous success is still on disk."""
        if not self.config.resume:
            return None
        previous = prior.get(url)
        if previous is None or previous.status not in SUCCESS_STATUSES:
            return None
        if not previous.sha256 or not previous.path:
            return None
        dest = self.output / previous.path
        if not is_inside(self.output, dest) or not dest.is_file():
            return None
        return RecoveryRecord(
            url=url,
            source=previous.source,
            timestamp=previous.timestamp,
            status="skipped",
            sha256=previous.sha256,
            path=previous.path,
            size_bytes=previous.size_bytes,
            mimetype=previous.mimetype,
        )

    def assess(self, data: bytes, snapshot: Snapshot) -> tuple[bool, str, str]:
        """Return ``(ok, status, detail)`` before anything is written."""
        size = len(data)
        if size == 0:
            return False, "invalid", "empty"
        if size < self.config.min_bytes:
            return False, "invalid", f"smaller than min-bytes ({self.config.min_bytes})"
        if size > self.config.max_bytes:
            return False, "error", f"larger than max-bytes ({self.config.max_bytes})"
        if self.config.validate:
            ok, reason = validate_bytes(
                data,
                mimetype=snapshot.mimetype,
                image_only=expects_images(self.config.mimetypes),
            )
            if not ok:
                return False, "invalid", reason
        return True, "recovered", ""

    async def commit(self, url: str, snapshot: Snapshot, data: bytes) -> RecoveryRecord:
        """Store ``data`` or point at an existing copy with the same hash."""
        digest = hashlib.sha256(data).hexdigest()
        async with self._lock:
            if self.config.dedupe and digest in self.hash_to_rel:
                return RecoveryRecord(
                    url=url,
                    source=snapshot.source,
                    timestamp=snapshot.timestamp,
                    status="duplicate",
                    sha256=digest,
                    path=self.hash_to_rel[digest],
                    size_bytes=len(data),
                    mimetype=snapshot.mimetype,
                )
            relative = self._relative_path(url, digest)
            dest = self._allocate(relative, digest)
            if not is_inside(self.output, dest):
                return RecoveryRecord(
                    url=url,
                    source=snapshot.source,
                    timestamp=snapshot.timestamp,
                    status="error",
                    error="refusing to write outside the output directory",
                    mimetype=snapshot.mimetype,
                )
            _write_atomic(dest, data)
            rel = dest.resolve().relative_to(self.output.resolve()).as_posix()
            self.hash_to_rel[digest] = rel
            return RecoveryRecord(
                url=url,
                source=snapshot.source,
                timestamp=snapshot.timestamp,
                status="recovered",
                sha256=digest,
                path=rel,
                size_bytes=len(data),
                mimetype=snapshot.mimetype,
            )

    def _relative_path(self, url: str, digest: str) -> Path:
        if self.config.flat:
            return flat_relative_path(url, digest)
        return safe_relative_path(url)

    def _allocate(self, relative: Path, digest: str) -> Path:
        candidate = self.output / relative
        if not candidate.exists():
            return candidate
        try:
            existing = hashlib.sha256(candidate.read_bytes()).hexdigest()
        except OSError:
            existing = ""
        if existing == digest:
            return candidate
        return candidate.with_name(f"{candidate.stem}_{digest[:8]}{candidate.suffix}")


def _write_atomic(dest: Path, data: bytes) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    temporary = dest.with_name(dest.name + ".partial")
    try:
        temporary.write_bytes(data)
        os.replace(temporary, dest)
    finally:
        if temporary.exists():
            temporary.unlink()
