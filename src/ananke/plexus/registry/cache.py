"""Rebuildable derived-state cache (spec §10, §111, §147).

A tiny SQLite key/value store holding source fingerprints and docs page digests. It is
never authoritative: deleting ``cache.db`` only costs recomputation. (The spec suggests
``redb``; that is a Rust crate, so the Python implementation uses stdlib SQLite behind the
same interface.)
"""

from __future__ import annotations

import contextlib
import sqlite3
from pathlib import Path


class KVCache:
    def __init__(self, path: Path) -> None:
        self.path = path

    def _conn(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=10.0)
        conn.execute("CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        return conn

    def get(self, key: str) -> str | None:
        try:
            with contextlib.closing(self._conn()) as conn:
                row = conn.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        except sqlite3.DatabaseError:
            return None
        return None if row is None else str(row[0])

    def _write(self, key: str, value: str) -> None:
        with contextlib.closing(self._conn()) as conn, conn:
            conn.execute(
                "INSERT INTO kv(key, value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    def set(self, key: str, value: str) -> None:
        try:
            self._write(key, value)
        except sqlite3.DatabaseError:
            # the cache is derived state: discard a corrupt file and start over
            self.clear()
            with contextlib.suppress(sqlite3.DatabaseError):
                self._write(key, value)

    def delete_prefix(self, prefix: str) -> None:
        with (
            contextlib.suppress(sqlite3.DatabaseError),
            contextlib.closing(self._conn()) as conn,
            conn,
        ):
            conn.execute("DELETE FROM kv WHERE key LIKE ?", (prefix + "%",))

    def clear(self) -> None:
        with contextlib.suppress(OSError):
            self.path.unlink()

    def count(self) -> int:
        try:
            with contextlib.closing(self._conn()) as conn:
                return int(conn.execute("SELECT COUNT(*) FROM kv").fetchone()[0])
        except sqlite3.DatabaseError:
            return 0
