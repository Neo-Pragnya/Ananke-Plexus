"""Filesystem content-addressed store for immutable payloads (spec §11)."""

from __future__ import annotations

import contextlib
import os
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path

from ananke.plexus.registry.errors import IntegrityError, NotFoundError
from ananke.plexus.registry.hashing import sha256_hex, strip_algo


class ContentStore:
    """Blobs live at ``blobs/sha256/<aa>/<hex>``; writes are atomic and read-only."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.blobs = root / "blobs" / "sha256"

    def _path(self, hex_digest: str) -> Path:
        h = strip_algo(hex_digest)
        if len(h) != 64 or any(c not in "0123456789abcdef" for c in h):
            raise IntegrityError(f"malformed digest: {hex_digest!r}")
        return self.blobs / h[:2] / h

    def ensure(self) -> None:
        self.blobs.mkdir(parents=True, exist_ok=True)

    def put(self, data: bytes) -> str:
        """Store ``data`` and return its hex sha256 (idempotent)."""
        digest = sha256_hex(data)
        target = self._path(digest)
        if target.exists():
            return digest
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".tmp-")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(tmp, 0o444)
            os.replace(tmp, target)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise
        return digest

    def exists(self, digest: str) -> bool:
        return self._path(digest).is_file()

    def get(self, digest: str, *, verify: bool = True) -> bytes:
        path = self._path(digest)
        if not path.is_file():
            raise NotFoundError(f"blob not found: {digest}")
        data = path.read_bytes()
        if verify and sha256_hex(data) != strip_algo(digest):
            raise IntegrityError(f"blob hash mismatch (tampered or corrupt): {digest}")
        return data

    def size(self, digest: str) -> int:
        return self._path(digest).stat().st_size

    def mtime(self, digest: str) -> float:
        return self._path(digest).stat().st_mtime

    def delete(self, digest: str) -> bool:
        path = self._path(digest)
        if not path.exists():
            return False
        with contextlib.suppress(OSError):
            os.chmod(path, 0o644)
        path.unlink()
        return True

    def iter_digests(self) -> Iterator[str]:
        if not self.blobs.exists():
            return
        for shard in sorted(self.blobs.iterdir()):
            if not shard.is_dir():
                continue
            for blob in sorted(shard.iterdir()):
                if blob.name.startswith(".tmp-"):
                    continue
                yield blob.name

    def stale_temp_files(self, older_than_s: float = 3600.0) -> list[Path]:
        out: list[Path] = []
        if not self.blobs.exists():
            return out
        cutoff = time.time() - older_than_s
        for shard in self.blobs.iterdir():
            if shard.is_dir():
                out.extend(p for p in shard.glob(".tmp-*") if p.stat().st_mtime < cutoff)
        return out
