"""SQLite metadata store (spec §7, §8, §55-§60, §134, §164).

The store is the only module that touches SQL. It exposes typed methods so no other
subsystem bypasses the canonical models. WAL, foreign keys and a busy timeout are always
on; writes run in short ``BEGIN IMMEDIATE`` transactions; payload identity columns are
protected by triggers so a version can never be silently overwritten.
"""

from __future__ import annotations

import contextlib
import json
import re
import shutil
import sqlite3
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ananke.plexus.registry.errors import (
    NotFoundError,
    NotInitializedError,
    RegistryError,
    SchemaVersionError,
    VersionContentConflictError,
)
from ananke.plexus.registry.models import (
    REGISTRY_SCHEMA_VERSION,
    ArtifactKind,
    ArtifactManifest,
    LicenseInfo,
    LifecycleStatus,
    Provenance,
    Quality,
    Security,
    TrustStatus,
    VersionRecord,
    artifact_uri,
)
from ananke.plexus.registry.payload import FileEntry
from ananke.plexus.registry.semver import normalize_version

SCHEMA_V1 = """
CREATE TABLE registry_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE artifacts (
    id          INTEGER PRIMARY KEY,
    kind        TEXT NOT NULL,
    namespace   TEXT NOT NULL,
    name        TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    UNIQUE(kind, namespace, name)
);

CREATE TABLE artifact_versions (
    id                 INTEGER PRIMARY KEY,
    artifact_id        INTEGER NOT NULL,
    version            TEXT NOT NULL,
    normalized_version TEXT,
    lifecycle_status   TEXT NOT NULL,
    lifecycle_message  TEXT,
    replacement        TEXT,
    channel            TEXT NOT NULL,
    trust_status       TEXT NOT NULL,
    summary            TEXT,
    description        TEXT,
    payload_blake3     TEXT NOT NULL DEFAULT '',
    payload_sha256     TEXT NOT NULL,
    payload_size       INTEGER NOT NULL DEFAULT 0,
    manifest_json      TEXT NOT NULL,
    revision           INTEGER NOT NULL DEFAULT 0,
    created_at         TEXT NOT NULL,
    FOREIGN KEY(artifact_id) REFERENCES artifacts(id),
    UNIQUE(artifact_id, version)
);
CREATE INDEX idx_versions_artifact ON artifact_versions(artifact_id);
CREATE INDEX idx_versions_digest ON artifact_versions(payload_sha256);

CREATE TABLE artifact_revisions (
    id          INTEGER PRIMARY KEY,
    version_id  INTEGER NOT NULL,
    revision    INTEGER NOT NULL,
    change_json TEXT NOT NULL,
    actor       TEXT,
    reason      TEXT,
    created_at  TEXT NOT NULL,
    FOREIGN KEY(version_id) REFERENCES artifact_versions(id) ON DELETE CASCADE,
    UNIQUE(version_id, revision)
);

CREATE TABLE artifact_aliases (
    alias      TEXT PRIMARY KEY,
    kind       TEXT NOT NULL,
    namespace  TEXT NOT NULL,
    name       TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE artifact_tags (
    version_id INTEGER NOT NULL REFERENCES artifact_versions(id) ON DELETE CASCADE,
    tag        TEXT NOT NULL,
    PRIMARY KEY(version_id, tag)
);
CREATE TABLE artifact_capabilities (
    version_id INTEGER NOT NULL REFERENCES artifact_versions(id) ON DELETE CASCADE,
    capability TEXT NOT NULL,
    PRIMARY KEY(version_id, capability)
);
CREATE INDEX idx_caps_cap ON artifact_capabilities(capability);

CREATE TABLE artifact_dependencies (
    id              INTEGER PRIMARY KEY,
    version_id      INTEGER NOT NULL,
    dependency_type TEXT NOT NULL,
    dependency_id   TEXT NOT NULL,
    version_req     TEXT,
    optional        INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY(version_id) REFERENCES artifact_versions(id) ON DELETE CASCADE
);
CREATE INDEX idx_deps_target ON artifact_dependencies(dependency_id);

CREATE TABLE artifact_permissions (
    version_id INTEGER NOT NULL REFERENCES artifact_versions(id) ON DELETE CASCADE,
    category   TEXT NOT NULL,
    pattern    TEXT NOT NULL,
    PRIMARY KEY(version_id, category, pattern)
);
CREATE TABLE artifact_compatibility (
    version_id INTEGER NOT NULL REFERENCES artifact_versions(id) ON DELETE CASCADE,
    dimension  TEXT NOT NULL,
    value      TEXT NOT NULL,
    PRIMARY KEY(version_id, dimension, value)
);
CREATE TABLE artifact_sources (
    version_id       INTEGER PRIMARY KEY REFERENCES artifact_versions(id) ON DELETE CASCADE,
    source_type      TEXT NOT NULL,
    source_name      TEXT,
    source_version   TEXT,
    source_url       TEXT,
    git_repository   TEXT,
    git_commit       TEXT,
    importer_id      TEXT,
    importer_version TEXT,
    publisher        TEXT,
    native_id        TEXT,
    fingerprint      TEXT,
    discovered_at    TEXT,
    provenance_json  TEXT NOT NULL
);
CREATE INDEX idx_sources_native ON artifact_sources(source_type, source_name, native_id);
CREATE TABLE artifact_hashes (
    version_id INTEGER NOT NULL REFERENCES artifact_versions(id) ON DELETE CASCADE,
    algorithm  TEXT NOT NULL,
    digest     TEXT NOT NULL,
    scope      TEXT NOT NULL DEFAULT 'payload',
    PRIMARY KEY(version_id, algorithm, scope)
);
CREATE TABLE artifact_channels (
    id         INTEGER PRIMARY KEY,
    version_id INTEGER NOT NULL REFERENCES artifact_versions(id) ON DELETE CASCADE,
    channel    TEXT NOT NULL,
    actor      TEXT,
    reason     TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE artifact_trust (
    id            INTEGER PRIMARY KEY,
    version_id    INTEGER NOT NULL REFERENCES artifact_versions(id) ON DELETE CASCADE,
    status        TEXT NOT NULL,
    actor         TEXT,
    reason        TEXT,
    evidence_json TEXT,
    created_at    TEXT NOT NULL
);
CREATE TABLE artifact_licenses (
    version_id INTEGER PRIMARY KEY REFERENCES artifact_versions(id) ON DELETE CASCADE,
    expression TEXT,
    source     TEXT,
    confidence REAL NOT NULL DEFAULT 1.0,
    approval   TEXT NOT NULL DEFAULT 'unknown'
);
CREATE TABLE artifact_quality (
    version_id       INTEGER PRIMARY KEY REFERENCES artifact_versions(id) ON DELETE CASCADE,
    tests_status     TEXT NOT NULL DEFAULT 'unknown',
    eval_suite       TEXT,
    eval_score       REAL,
    last_verified_at TEXT
);
CREATE TABLE artifact_security (
    version_id      INTEGER PRIMARY KEY REFERENCES artifact_versions(id) ON DELETE CASCADE,
    security_status TEXT NOT NULL DEFAULT 'unknown',
    last_scanned_at TEXT,
    scanner         TEXT,
    findings_count  INTEGER NOT NULL DEFAULT 0,
    critical_count  INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE artifact_schemas (
    version_id  INTEGER NOT NULL REFERENCES artifact_versions(id) ON DELETE CASCADE,
    role        TEXT NOT NULL,
    format      TEXT NOT NULL,
    schema_json TEXT NOT NULL,
    PRIMARY KEY(version_id, role)
);
CREATE TABLE artifact_files (
    version_id INTEGER NOT NULL REFERENCES artifact_versions(id) ON DELETE CASCADE,
    path       TEXT NOT NULL,
    size       INTEGER NOT NULL,
    sha256     TEXT NOT NULL,
    PRIMARY KEY(version_id, path)
);
CREATE TABLE artifact_owners (
    artifact_id INTEGER NOT NULL REFERENCES artifacts(id) ON DELETE CASCADE,
    role        TEXT NOT NULL,
    principal   TEXT NOT NULL,
    PRIMARY KEY(artifact_id, role, principal)
);

CREATE TABLE agents (
    version_id       INTEGER PRIMARY KEY REFERENCES artifact_versions(id) ON DELETE CASCADE,
    runtime_provider TEXT,
    instructions_ref TEXT,
    eval_suite       TEXT
);
CREATE TABLE agent_skills (
    agent_version_id INTEGER NOT NULL REFERENCES artifact_versions(id) ON DELETE CASCADE,
    skill_ref        TEXT NOT NULL,
    version_req      TEXT,
    position         INTEGER NOT NULL,
    PRIMARY KEY(agent_version_id, position)
);

CREATE TABLE registry_events (
    seq          INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id     TEXT UNIQUE NOT NULL,
    event_type   TEXT NOT NULL,
    artifact_uri TEXT,
    actor        TEXT,
    payload_json TEXT NOT NULL,
    created_at   TEXT NOT NULL
);
CREATE TRIGGER trg_events_no_update BEFORE UPDATE ON registry_events
BEGIN SELECT RAISE(ABORT, 'registry_events is append-only'); END;
CREATE TRIGGER trg_events_no_delete BEFORE DELETE ON registry_events
BEGIN SELECT RAISE(ABORT, 'registry_events is append-only'); END;

CREATE TRIGGER trg_versions_immutable BEFORE UPDATE OF
    artifact_id, version, payload_sha256, payload_blake3, payload_size, manifest_json, created_at
ON artifact_versions
BEGIN SELECT RAISE(ABORT, 'VERSION_IMMUTABLE: payload identity cannot change'); END;

CREATE TABLE registry_snapshots (
    id            TEXT PRIMARY KEY,
    created_at    TEXT NOT NULL,
    version_count INTEGER NOT NULL,
    note          TEXT
);
CREATE TABLE activations (
    id         INTEGER PRIMARY KEY,
    project    TEXT NOT NULL,
    kind       TEXT NOT NULL,
    namespace  TEXT NOT NULL,
    name       TEXT NOT NULL,
    version    TEXT NOT NULL,
    digest     TEXT NOT NULL,
    state      TEXT NOT NULL,
    path       TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(project, kind, namespace, name)
);
CREATE TABLE locks (
    id          INTEGER PRIMARY KEY,
    path        TEXT NOT NULL,
    lock_hash   TEXT NOT NULL,
    snapshot_id TEXT,
    digests_json TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    actor       TEXT
);
CREATE TABLE usage_events (
    id         INTEGER PRIMARY KEY,
    event      TEXT NOT NULL,
    uri        TEXT NOT NULL,
    version    TEXT,
    project    TEXT,
    created_at TEXT NOT NULL
);
"""

_FTS_DDL = (
    "CREATE VIRTUAL TABLE artifact_fts USING fts5("
    "uri UNINDEXED, version_id UNINDEXED, name, summary, description, tags, capabilities, "
    "tools, namespace, tokenize='porter unicode61')"
)

SCHEMA_V2 = """
CREATE TABLE artifact_signatures (
    id         INTEGER PRIMARY KEY,
    version_id INTEGER NOT NULL REFERENCES artifact_versions(id) ON DELETE CASCADE,
    key_id     TEXT NOT NULL,
    algorithm  TEXT NOT NULL,
    signature  TEXT NOT NULL,
    signer     TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(version_id, key_id)
);
"""

MIGRATIONS: list[tuple[int, str, str]] = [
    (1, "initial schema", SCHEMA_V1),
    (2, "artifact signatures", SCHEMA_V2),
]
SUPPORTED_SCHEMA = MIGRATIONS[-1][0]

_UNSET: Any = object()


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass
class VersionInsert:
    manifest: ArtifactManifest
    version: str
    digest_sha256: str
    digest_blake3: str | None
    payload_size: int
    provenance: Provenance
    license: LicenseInfo
    files: list[FileEntry] = field(default_factory=list)
    schemas: list[tuple[str, str, dict[str, Any]]] = field(default_factory=list)
    channel: str = "candidate"
    trust: TrustStatus = TrustStatus.DISCOVERED
    lifecycle: LifecycleStatus = LifecycleStatus.ACTIVE
    quality: Quality = field(default_factory=Quality)
    security: Security = field(default_factory=Security)
    created_at: str = ""
    actor: str = "local"


@dataclass(frozen=True)
class ArtifactSummary:
    kind: ArtifactKind
    namespace: str
    name: str
    version_count: int
    latest_version: str | None

    @property
    def uri(self) -> str:
        return artifact_uri(self.kind, self.namespace, self.name)


_VERSION_SELECT = """
SELECT v.id AS vid, a.kind, a.namespace, a.name, v.version, v.lifecycle_status,
       v.lifecycle_message, v.replacement, v.channel, v.trust_status, v.summary,
       v.description, v.payload_blake3, v.payload_sha256, v.payload_size, v.manifest_json,
       v.revision, v.created_at,
       s.provenance_json,
       l.expression AS lic_expr, l.source AS lic_source, l.confidence AS lic_conf,
       l.approval AS lic_approval,
       q.tests_status, q.eval_suite, q.eval_score, q.last_verified_at,
       sec.security_status, sec.last_scanned_at, sec.scanner, sec.findings_count,
       sec.critical_count
FROM artifact_versions v
JOIN artifacts a ON a.id = v.artifact_id
LEFT JOIN artifact_sources s ON s.version_id = v.id
LEFT JOIN artifact_licenses l ON l.version_id = v.id
LEFT JOIN artifact_quality q ON q.version_id = v.id
LEFT JOIN artifact_security sec ON sec.version_id = v.id
"""


class RegistryStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._conn: sqlite3.Connection | None = None
        self._depth = 0
        self.fts_available = False

    # ------------------------------------------------------------------ connection
    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise NotInitializedError("registry store is not open")
        return self._conn

    def exists(self) -> bool:
        return self.db_path.exists()

    def open(self, *, create: bool = False) -> None:
        if self._conn is not None:
            return
        if not self.db_path.exists():
            if not create:
                raise NotInitializedError(
                    f"registry not initialized at {self.db_path.parent} "
                    "(run `ananke registry init`)"
                )
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(
            self.db_path, timeout=30.0, isolation_level=None, check_same_thread=False
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA synchronous=NORMAL")
        self._conn = conn
        try:
            self._migrate()
            self.fts_available = self.get_meta("fts") == "1"
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        if self._conn is not None:
            with contextlib.suppress(sqlite3.Error):
                self._conn.close()
            self._conn = None

    @contextlib.contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Short-lived write transaction (re-entrant: inner calls join the outer)."""
        conn = self.conn
        if self._depth == 0:
            conn.execute("BEGIN IMMEDIATE")
        self._depth += 1
        try:
            yield conn
        except BaseException:
            self._depth -= 1
            if self._depth == 0:
                conn.execute("ROLLBACK")
            raise
        else:
            self._depth -= 1
            if self._depth == 0:
                conn.execute("COMMIT")

    # ------------------------------------------------------------------ migrations
    def schema_version(self) -> int:
        conn = self.conn
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
        ).fetchone()
        if row is None:
            return 0
        got = conn.execute("SELECT MAX(version) AS v FROM schema_migrations").fetchone()
        return int(got["v"] or 0)

    def _migrate(self) -> None:
        current = self.schema_version()
        if current > SUPPORTED_SCHEMA:
            raise SchemaVersionError(
                f"registry schema v{current} is newer than supported v{SUPPORTED_SCHEMA}; "
                "upgrade ananke-plexus"
            )
        pending = [m for m in MIGRATIONS if m[0] > current]
        if not pending:
            return
        if current > 0 and self.db_path.exists():
            backup = self.db_path.with_name(f"{self.db_path.name}.bak-v{current}")
            with contextlib.suppress(OSError):
                self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                shutil.copy2(self.db_path, backup)
        for version, description, ddl in pending:
            with self.transaction() as conn:
                if self.schema_version() >= version:  # another process migrated first
                    continue
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS schema_migrations ("
                    "version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL, description TEXT)"
                )
                for statement in _split_sql(ddl):
                    conn.execute(statement)
                if version == 1:
                    try:
                        conn.execute(_FTS_DDL)
                        conn.execute("INSERT INTO registry_meta(key, value) VALUES('fts', '1')")
                    except sqlite3.OperationalError:
                        conn.execute("INSERT INTO registry_meta(key, value) VALUES('fts', '0')")
                    conn.execute(
                        "INSERT INTO registry_meta(key, value) VALUES('registry_id', ?)",
                        (uuid.uuid4().hex,),
                    )
                    conn.execute(
                        "INSERT INTO registry_meta(key, value) VALUES('schema', ?)",
                        (str(REGISTRY_SCHEMA_VERSION),),
                    )
                    conn.execute(
                        "INSERT INTO registry_meta(key, value) VALUES('created_at', ?)",
                        (now_iso(),),
                    )
                conn.execute(
                    "INSERT INTO schema_migrations(version, applied_at, description) VALUES(?,?,?)",
                    (version, now_iso(), description),
                )

    # ------------------------------------------------------------------ meta
    def get_meta(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM registry_meta WHERE key=?", (key,)).fetchone()
        return None if row is None else str(row["value"])

    def set_meta(self, key: str, value: str) -> None:
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO registry_meta(key, value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    # ------------------------------------------------------------------ artifacts
    def artifact_id(self, kind: ArtifactKind | str, namespace: str, name: str) -> int | None:
        row = self.conn.execute(
            "SELECT id FROM artifacts WHERE kind=? AND namespace=? AND name=?",
            (str(ArtifactKind(kind).value), namespace, name),
        ).fetchone()
        return None if row is None else int(row["id"])

    def list_artifacts(
        self, kind: ArtifactKind | str | None = None, namespace: str | None = None
    ) -> list[ArtifactSummary]:
        sql = (
            "SELECT a.kind, a.namespace, a.name, COUNT(v.id) AS n FROM artifacts a "
            "LEFT JOIN artifact_versions v ON v.artifact_id = a.id"
        )
        where: list[str] = []
        args: list[Any] = []
        if kind:
            where.append("a.kind=?")
            args.append(ArtifactKind(kind).value)
        if namespace:
            where.append("a.namespace=?")
            args.append(namespace)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " GROUP BY a.id ORDER BY a.kind, a.namespace, a.name"
        out: list[ArtifactSummary] = []
        for row in self.conn.execute(sql, args).fetchall():
            recs = self.versions_of(row["kind"], row["namespace"], row["name"])
            latest = recs[-1].version if recs else None
            out.append(
                ArtifactSummary(
                    ArtifactKind(row["kind"]), row["namespace"], row["name"], int(row["n"]), latest
                )
            )
        return out

    # ------------------------------------------------------------------ insert version
    def insert_version(self, ins: VersionInsert) -> int:
        """Insert an immutable version and all its derived rows. Caller wraps a transaction."""
        m = ins.manifest
        norm = str(normalize_version(ins.version))
        with self.transaction() as conn:
            aid = self.artifact_id(m.kind, m.namespace, m.name)
            if aid is None:
                cur = conn.execute(
                    "INSERT INTO artifacts(kind, namespace, name, created_at) VALUES(?,?,?,?)",
                    (m.kind.value, m.namespace, m.name, ins.created_at),
                )
                aid = int(cur.lastrowid or 0)
            existing = conn.execute(
                "SELECT payload_sha256 FROM artifact_versions WHERE artifact_id=? AND version=?",
                (aid, ins.version),
            ).fetchone()
            if existing is not None:
                raise VersionContentConflictError(
                    f"{m.uri}@{ins.version} already registered "
                    f"(digest {existing['payload_sha256'][:12]}…)"
                )
            cur = conn.execute(
                "INSERT INTO artifact_versions(artifact_id, version, normalized_version, "
                "lifecycle_status, lifecycle_message, replacement, channel, trust_status, summary, "
                "description, payload_blake3, payload_sha256, payload_size, manifest_json, "
                "revision, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,?)",
                (
                    aid,
                    ins.version,
                    norm,
                    ins.lifecycle.value,
                    m.lifecycle.message,
                    m.lifecycle.replacement,
                    ins.channel,
                    ins.trust.value,
                    m.summary,
                    m.description,
                    ins.digest_blake3 or "",
                    ins.digest_sha256,
                    ins.payload_size,
                    json.dumps(m.canonical_dict(), sort_keys=True),
                    ins.created_at,
                ),
            )
            vid = int(cur.lastrowid or 0)
            conn.executemany(
                "INSERT INTO artifact_tags(version_id, tag) VALUES(?,?)",
                [(vid, t) for t in m.metadata.tags],
            )
            conn.executemany(
                "INSERT INTO artifact_capabilities(version_id, capability) VALUES(?,?)",
                [(vid, c) for c in m.capabilities],
            )
            for dep in m.all_dependencies():
                conn.execute(
                    "INSERT INTO artifact_dependencies(version_id, dependency_type, "
                    "dependency_id, version_req, optional) VALUES(?,?,?,?,?)",
                    (
                        vid,
                        dep.type.value,
                        dep.id or dep.name or "",
                        dep.version,
                        int(dep.optional),
                    ),
                )
            conn.executemany(
                "INSERT INTO artifact_permissions(version_id, category, pattern) VALUES(?,?,?)",
                [(vid, c, p) for c, p in m.permissions.flatten()],
            )
            conn.executemany(
                "INSERT OR IGNORE INTO artifact_compatibility(version_id, dimension, value) "
                "VALUES(?,?,?)",
                [(vid, d, v) for d, v in m.compatibility.flatten()],
            )
            prov = ins.provenance
            conn.execute(
                "INSERT INTO artifact_sources(version_id, source_type, source_name, "
                "source_version, source_url, git_repository, git_commit, importer_id, "
                "importer_version, publisher, native_id, fingerprint, discovered_at, "
                "provenance_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    vid,
                    prov.source_type,
                    prov.source_name,
                    prov.source_version,
                    prov.source_url,
                    prov.git.repository if prov.git else None,
                    prov.git.commit if prov.git else None,
                    prov.importer.id if prov.importer else None,
                    prov.importer.version if prov.importer else None,
                    prov.publisher.name if prov.publisher else None,
                    prov.native_id,
                    prov.fingerprint,
                    prov.discovered_at,
                    prov.model_dump_json(exclude_none=True),
                ),
            )
            conn.execute(
                "INSERT INTO artifact_hashes(version_id, algorithm, digest, scope) "
                "VALUES(?,?,?,'payload')",
                (vid, "sha256", ins.digest_sha256),
            )
            if ins.digest_blake3:
                conn.execute(
                    "INSERT INTO artifact_hashes(version_id, algorithm, digest, scope) "
                    "VALUES(?,?,?,'payload')",
                    (vid, "blake3", ins.digest_blake3),
                )
            conn.execute(
                "INSERT INTO artifact_channels(version_id, channel, actor, reason, created_at) "
                "VALUES(?,?,?,?,?)",
                (vid, ins.channel, ins.actor, "registered", ins.created_at),
            )
            conn.execute(
                "INSERT INTO artifact_trust(version_id, status, actor, reason, evidence_json, "
                "created_at) VALUES(?,?,?,?,?,?)",
                (vid, ins.trust.value, ins.actor, "registered", None, ins.created_at),
            )
            conn.execute(
                "INSERT INTO artifact_licenses(version_id, expression, source, confidence, "
                "approval) VALUES(?,?,?,?,?)",
                (
                    vid,
                    ins.license.expression,
                    ins.license.source,
                    ins.license.confidence,
                    ins.license.approval,
                ),
            )
            q = ins.quality
            conn.execute(
                "INSERT INTO artifact_quality(version_id, tests_status, eval_suite, eval_score, "
                "last_verified_at) VALUES(?,?,?,?,?)",
                (vid, q.tests_status, q.evaluation_suite, q.evaluation_score, q.last_verified_at),
            )
            s = ins.security
            conn.execute(
                "INSERT INTO artifact_security(version_id, security_status, last_scanned_at, "
                "scanner, findings_count, critical_count) VALUES(?,?,?,?,?,?)",
                (vid, s.status, s.last_scanned_at, s.scanner, s.findings_count, s.critical_count),
            )
            conn.executemany(
                "INSERT INTO artifact_schemas(version_id, role, format, schema_json) "
                "VALUES(?,?,?,?)",
                [
                    (vid, role, fmt, json.dumps(sch, sort_keys=True))
                    for role, fmt, sch in ins.schemas
                ],
            )
            conn.executemany(
                "INSERT INTO artifact_files(version_id, path, size, sha256) VALUES(?,?,?,?)",
                [(vid, f.path, f.size, f.sha256) for f in ins.files],
            )
            conn.execute("DELETE FROM artifact_owners WHERE artifact_id=?", (aid,))
            owners = [("owner", o.team or o.user or "") for o in m.owners]
            owners += [("maintainer", who) for who in m.maintainers]
            conn.executemany(
                "INSERT OR IGNORE INTO artifact_owners(artifact_id, role, principal) VALUES(?,?,?)",
                [(aid, role, who) for role, who in owners if who],
            )
            if m.kind is ArtifactKind.AGENT:
                conn.execute(
                    "INSERT INTO agents(version_id, runtime_provider, instructions_ref, "
                    "eval_suite) VALUES(?,?,?,?)",
                    (
                        vid,
                        m.runtime.provider,
                        m.instructions.ref if m.instructions else None,
                        m.evaluation.suite if m.evaluation else None,
                    ),
                )
                conn.executemany(
                    "INSERT INTO agent_skills(agent_version_id, skill_ref, version_req, position) "
                    "VALUES(?,?,?,?)",
                    [(vid, s_.ref, s_.version, i) for i, s_ in enumerate(m.skills)],
                )
            self._index(vid, ins)
        return vid

    # ------------------------------------------------------------------ reads
    def _record(self, row: sqlite3.Row) -> VersionRecord:
        manifest = ArtifactManifest.model_validate(json.loads(row["manifest_json"]))
        prov = (
            Provenance.model_validate_json(row["provenance_json"])
            if row["provenance_json"]
            else Provenance()
        )
        return VersionRecord(
            db_id=int(row["vid"]),
            uri=artifact_uri(row["kind"], row["namespace"], row["name"]),
            kind=ArtifactKind(row["kind"]),
            namespace=row["namespace"],
            name=row["name"],
            version=row["version"],
            lifecycle=LifecycleStatus(row["lifecycle_status"]),
            channel=row["channel"],
            trust=TrustStatus(row["trust_status"]),
            summary=row["summary"] or "",
            description=row["description"] or "",
            digest_sha256=row["payload_sha256"],
            digest_blake3=row["payload_blake3"] or None,
            payload_size=int(row["payload_size"]),
            revision=int(row["revision"]),
            created_at=row["created_at"],
            manifest=manifest,
            provenance=prov,
            license=LicenseInfo(
                expression=row["lic_expr"],
                source=row["lic_source"],
                confidence=float(row["lic_conf"] if row["lic_conf"] is not None else 1.0),
                approval=row["lic_approval"] or "unknown",
            ),
            quality=Quality(
                tests_status=row["tests_status"] or "unknown",
                evaluation_suite=row["eval_suite"],
                evaluation_score=row["eval_score"],
                last_verified_at=row["last_verified_at"],
            ),
            security=Security(
                status=row["security_status"] or "unknown",
                last_scanned_at=row["last_scanned_at"],
                scanner=row["scanner"],
                findings_count=int(row["findings_count"] or 0),
                critical_count=int(row["critical_count"] or 0),
            ),
            lifecycle_message=row["lifecycle_message"],
            replacement=row["replacement"],
        )

    def version_id(self, kind: ArtifactKind | str, namespace: str, name: str, version: str) -> int:
        row = self.conn.execute(
            "SELECT v.id FROM artifact_versions v JOIN artifacts a ON a.id=v.artifact_id "
            "WHERE a.kind=? AND a.namespace=? AND a.name=? AND v.version=?",
            (ArtifactKind(kind).value, namespace, name, version),
        ).fetchone()
        if row is None:
            raise NotFoundError(f"{artifact_uri(kind, namespace, name)}@{version} not found")
        return int(row["id"])

    def get_record(
        self, kind: ArtifactKind | str, namespace: str, name: str, version: str
    ) -> VersionRecord | None:
        row = self.conn.execute(
            _VERSION_SELECT + " WHERE a.kind=? AND a.namespace=? AND a.name=? AND v.version=?",
            (ArtifactKind(kind).value, namespace, name, version),
        ).fetchone()
        return None if row is None else self._record(row)

    def record_by_id(self, vid: int) -> VersionRecord:
        row = self.conn.execute(_VERSION_SELECT + " WHERE v.id=?", (vid,)).fetchone()
        if row is None:
            raise NotFoundError(f"version id {vid} not found")
        return self._record(row)

    def versions_of(
        self, kind: ArtifactKind | str, namespace: str, name: str
    ) -> list[VersionRecord]:
        rows = self.conn.execute(
            _VERSION_SELECT + " WHERE a.kind=? AND a.namespace=? AND a.name=?",
            (ArtifactKind(kind).value, namespace, name),
        ).fetchall()
        recs = [self._record(r) for r in rows]
        recs.sort(key=lambda r: (r.semver.sort_key, r.version))
        return recs

    def list_records(
        self,
        kind: ArtifactKind | str | None = None,
        namespace: str | None = None,
        lifecycle: LifecycleStatus | None = None,
    ) -> list[VersionRecord]:
        where: list[str] = []
        args: list[Any] = []
        if kind:
            where.append("a.kind=?")
            args.append(ArtifactKind(kind).value)
        if namespace:
            where.append("a.namespace=?")
            args.append(namespace)
        if lifecycle:
            where.append("v.lifecycle_status=?")
            args.append(lifecycle.value)
        sql = _VERSION_SELECT + (" WHERE " + " AND ".join(where) if where else "")
        recs = [self._record(r) for r in self.conn.execute(sql, args).fetchall()]
        recs.sort(key=lambda r: (r.kind.value, r.namespace, r.name, r.semver.sort_key))
        return recs

    def count_versions(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) AS n FROM artifact_versions").fetchone()["n"])

    def dependents_of(self, artifact: str) -> list[tuple[VersionRecord, str | None]]:
        rows = self.conn.execute(
            "SELECT version_id, version_req FROM artifact_dependencies "
            "WHERE dependency_type='artifact' AND dependency_id=?",
            (artifact,),
        ).fetchall()
        return [(self.record_by_id(int(r["version_id"])), r["version_req"]) for r in rows]

    def dependencies_of(self, vid: int) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT dependency_type, dependency_id, version_req, optional "
            "FROM artifact_dependencies WHERE version_id=? ORDER BY id",
            (vid,),
        ).fetchall()
        return [dict(r) for r in rows]

    def schemas_of(self, vid: int) -> dict[str, dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT role, schema_json FROM artifact_schemas WHERE version_id=? ORDER BY role",
            (vid,),
        ).fetchall()
        return {r["role"]: json.loads(r["schema_json"]) for r in rows}

    def files_of(self, vid: int) -> list[FileEntry]:
        rows = self.conn.execute(
            "SELECT path, size, sha256 FROM artifact_files WHERE version_id=? ORDER BY path",
            (vid,),
        ).fetchall()
        return [FileEntry(r["path"], int(r["size"]), r["sha256"]) for r in rows]

    def owners_of(
        self, kind: ArtifactKind | str, namespace: str, name: str
    ) -> list[tuple[str, str]]:
        aid = self.artifact_id(kind, namespace, name)
        if aid is None:
            return []
        rows = self.conn.execute(
            "SELECT role, principal FROM artifact_owners WHERE artifact_id=? "
            "ORDER BY role, principal",
            (aid,),
        ).fetchall()
        return [(r["role"], r["principal"]) for r in rows]

    def capability_index(self) -> dict[str, list[int]]:
        out: dict[str, list[int]] = {}
        for r in self.conn.execute(
            "SELECT capability, version_id FROM artifact_capabilities ORDER BY capability"
        ).fetchall():
            out.setdefault(r["capability"], []).append(int(r["version_id"]))
        return out

    # ------------------------------------------------------------------ signatures
    def add_signature(
        self,
        vid: int,
        *,
        key_id: str,
        algorithm: str,
        signature: str,
        signer: str | None,
        created_at: str,
    ) -> None:
        """Store (or replace, per key) a signature. Verification is computed, never stored."""
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO artifact_signatures(version_id, key_id, algorithm, signature, "
                "signer, created_at) VALUES(?,?,?,?,?,?) ON CONFLICT(version_id, key_id) DO UPDATE "
                "SET algorithm=excluded.algorithm, signature=excluded.signature, "
                "signer=excluded.signer, created_at=excluded.created_at",
                (vid, key_id, algorithm, signature, signer, created_at),
            )

    def signatures_of(self, vid: int) -> list[dict[str, Any]]:
        return [
            dict(r)
            for r in self.conn.execute(
                "SELECT key_id, algorithm, signature, signer, created_at "
                "FROM artifact_signatures WHERE version_id=? ORDER BY key_id",
                (vid,),
            ).fetchall()
        ]

    def remove_signature(self, vid: int, key_id: str) -> bool:
        with self.transaction() as conn:
            cur = conn.execute(
                "DELETE FROM artifact_signatures WHERE version_id=? AND key_id=?", (vid, key_id)
            )
            return cur.rowcount > 0

    def trust_history(self, vid: int) -> list[dict[str, Any]]:
        return [
            dict(r)
            for r in self.conn.execute(
                "SELECT status, actor, reason, evidence_json, created_at FROM artifact_trust "
                "WHERE version_id=? ORDER BY id",
                (vid,),
            ).fetchall()
        ]

    def channel_history(self, vid: int) -> list[dict[str, Any]]:
        return [
            dict(r)
            for r in self.conn.execute(
                "SELECT channel, actor, reason, created_at FROM artifact_channels "
                "WHERE version_id=? ORDER BY id",
                (vid,),
            ).fetchall()
        ]

    def revisions_of(self, vid: int) -> list[dict[str, Any]]:
        return [
            dict(r)
            for r in self.conn.execute(
                "SELECT revision, change_json, actor, reason, created_at FROM artifact_revisions "
                "WHERE version_id=? ORDER BY revision",
                (vid,),
            ).fetchall()
        ]

    # ------------------------------------------------------------------ metadata updates
    def update_metadata(
        self,
        vid: int,
        changes: dict[str, Any],
        *,
        actor: str,
        reason: str | None,
        created_at: str,
    ) -> int:
        """Apply mutable-metadata changes and record a registry revision (spec §19)."""
        allowed = {
            "lifecycle",
            "lifecycle_message",
            "replacement",
            "channel",
            "trust",
            "summary",
            "description",
            "license",
            "quality",
            "security",
            "evidence",
        }
        unknown = set(changes) - allowed
        if unknown:
            raise RegistryError(f"cannot change immutable/unknown fields: {sorted(unknown)}")
        rec = self.record_by_id(vid)
        diff: dict[str, Any] = {}
        with self.transaction() as conn:
            sets: list[str] = []
            args: list[Any] = []

            def col(name: str, old: Any, new: Any, key: str) -> None:
                if new != old:
                    sets.append(f"{name}=?")
                    args.append(new)
                    diff[key] = {"from": old, "to": new}

            if "lifecycle" in changes:
                col(
                    "lifecycle_status", rec.lifecycle.value, changes["lifecycle"].value, "lifecycle"
                )
            if "lifecycle_message" in changes:
                col(
                    "lifecycle_message",
                    rec.lifecycle_message,
                    changes["lifecycle_message"],
                    "lifecycle_message",
                )
            if "replacement" in changes:
                col("replacement", rec.replacement, changes["replacement"], "replacement")
            if "channel" in changes:
                col("channel", rec.channel, changes["channel"], "channel")
                if changes["channel"] != rec.channel:
                    conn.execute(
                        "INSERT INTO artifact_channels(version_id, channel, actor, reason, "
                        "created_at) VALUES(?,?,?,?,?)",
                        (vid, changes["channel"], actor, reason, created_at),
                    )
            if "trust" in changes:
                col("trust_status", rec.trust.value, changes["trust"].value, "trust")
                if changes["trust"] != rec.trust:
                    conn.execute(
                        "INSERT INTO artifact_trust(version_id, status, actor, reason, "
                        "evidence_json, created_at) VALUES(?,?,?,?,?,?)",
                        (
                            vid,
                            changes["trust"].value,
                            actor,
                            reason,
                            json.dumps(changes.get("evidence"), sort_keys=True)
                            if changes.get("evidence") is not None
                            else None,
                            created_at,
                        ),
                    )
            if "summary" in changes:
                col("summary", rec.summary, changes["summary"], "summary")
            if "description" in changes:
                col("description", rec.description, changes["description"], "description")
            if sets:
                # column names come from fixed literals above, values are bound parameters
                sql = f"UPDATE artifact_versions SET {', '.join(sets)} WHERE id=?"  # noqa: S608
                conn.execute(sql, (*args, vid))
            if "license" in changes:
                lic: LicenseInfo = changes["license"]
                if lic != rec.license:
                    diff["license"] = {
                        "from": rec.license.model_dump(mode="json"),
                        "to": lic.model_dump(mode="json"),
                    }
                    conn.execute(
                        "UPDATE artifact_licenses SET expression=?, source=?, confidence=?, "
                        "approval=? WHERE version_id=?",
                        (lic.expression, lic.source, lic.confidence, lic.approval, vid),
                    )
            if "quality" in changes:
                qual: Quality = changes["quality"]
                if qual != rec.quality:
                    diff["quality"] = {
                        "from": rec.quality.model_dump(mode="json"),
                        "to": qual.model_dump(mode="json"),
                    }
                    conn.execute(
                        "UPDATE artifact_quality SET tests_status=?, eval_suite=?, eval_score=?, "
                        "last_verified_at=? WHERE version_id=?",
                        (
                            qual.tests_status,
                            qual.evaluation_suite,
                            qual.evaluation_score,
                            qual.last_verified_at,
                            vid,
                        ),
                    )
            if "security" in changes:
                sec: Security = changes["security"]
                if sec != rec.security:
                    diff["security"] = {
                        "from": rec.security.model_dump(mode="json"),
                        "to": sec.model_dump(mode="json"),
                    }
                    conn.execute(
                        "UPDATE artifact_security SET security_status=?, last_scanned_at=?, "
                        "scanner=?, findings_count=?, critical_count=? WHERE version_id=?",
                        (
                            sec.status,
                            sec.last_scanned_at,
                            sec.scanner,
                            sec.findings_count,
                            sec.critical_count,
                            vid,
                        ),
                    )
            if not diff:
                return rec.revision
            revision = rec.revision + 1
            conn.execute("UPDATE artifact_versions SET revision=? WHERE id=?", (revision, vid))
            conn.execute(
                "INSERT INTO artifact_revisions(version_id, revision, change_json, actor, reason, "
                "created_at) VALUES(?,?,?,?,?,?)",
                (vid, revision, json.dumps(diff, sort_keys=True), actor, reason, created_at),
            )
            if "summary" in diff or "description" in diff:
                self.reindex(vid)
            return revision

    def restore_state(
        self,
        vid: int,
        *,
        summary: str,
        description: str,
        revision: int,
        lifecycle_message: str | None,
        replacement: str | None,
        trust_history: list[dict[str, Any]],
        channel_history: list[dict[str, Any]],
        revisions: list[dict[str, Any]],
        quality: Quality,
        security: Security,
        license: LicenseInfo,
    ) -> None:
        """Import path only: replay exported mutable state (histories, revision counter)."""
        with self.transaction() as conn:
            conn.execute(
                "UPDATE artifact_versions SET summary=?, description=?, revision=?, "
                "lifecycle_message=?, replacement=? WHERE id=?",
                (summary, description, revision, lifecycle_message, replacement, vid),
            )
            if trust_history:
                conn.execute("DELETE FROM artifact_trust WHERE version_id=?", (vid,))
                conn.executemany(
                    "INSERT INTO artifact_trust(version_id, status, actor, reason, evidence_json, "
                    "created_at) VALUES(?,?,?,?,?,?)",
                    [
                        (
                            vid,
                            t["status"],
                            t.get("actor"),
                            t.get("reason"),
                            t.get("evidence_json"),
                            t["created_at"],
                        )
                        for t in trust_history
                    ],
                )
            if channel_history:
                conn.execute("DELETE FROM artifact_channels WHERE version_id=?", (vid,))
                conn.executemany(
                    "INSERT INTO artifact_channels(version_id, channel, actor, reason, created_at) "
                    "VALUES(?,?,?,?,?)",
                    [
                        (vid, c["channel"], c.get("actor"), c.get("reason"), c["created_at"])
                        for c in channel_history
                    ],
                )
            conn.executemany(
                "INSERT INTO artifact_revisions(version_id, revision, change_json, actor, reason, "
                "created_at) VALUES(?,?,?,?,?,?)",
                [
                    (
                        vid,
                        r["revision"],
                        r["change_json"],
                        r.get("actor"),
                        r.get("reason"),
                        r["created_at"],
                    )
                    for r in revisions
                ],
            )
            conn.execute(
                "UPDATE artifact_licenses SET expression=?, source=?, confidence=?, approval=? "
                "WHERE version_id=?",
                (license.expression, license.source, license.confidence, license.approval, vid),
            )
            conn.execute(
                "UPDATE artifact_quality SET tests_status=?, eval_suite=?, eval_score=?, "
                "last_verified_at=? WHERE version_id=?",
                (
                    quality.tests_status,
                    quality.evaluation_suite,
                    quality.evaluation_score,
                    quality.last_verified_at,
                    vid,
                ),
            )
            conn.execute(
                "UPDATE artifact_security SET security_status=?, last_scanned_at=?, scanner=?, "
                "findings_count=?, critical_count=? WHERE version_id=?",
                (
                    security.status,
                    security.last_scanned_at,
                    security.scanner,
                    security.findings_count,
                    security.critical_count,
                    vid,
                ),
            )
            self.reindex(vid)

    def event_ids(self) -> set[str]:
        return {
            str(r["event_id"])
            for r in self.conn.execute("SELECT event_id FROM registry_events").fetchall()
        }

    def delete_version(self, vid: int) -> None:
        with self.transaction() as conn:
            self._unindex(vid)
            conn.execute("DELETE FROM artifact_versions WHERE id=?", (vid,))
            conn.execute(
                "DELETE FROM artifacts WHERE id NOT IN (SELECT DISTINCT artifact_id "
                "FROM artifact_versions)"
            )

    # ------------------------------------------------------------------ aliases
    def set_alias(
        self, alias: str, kind: ArtifactKind, namespace: str, name: str, now: str
    ) -> None:
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO artifact_aliases(alias, kind, namespace, name, created_at) "
                "VALUES(?,?,?,?,?) ON CONFLICT(alias) DO UPDATE SET kind=excluded.kind, "
                "namespace=excluded.namespace, name=excluded.name",
                (alias, kind.value, namespace, name, now),
            )

    def get_alias(self, alias: str) -> tuple[ArtifactKind, str, str] | None:
        row = self.conn.execute(
            "SELECT kind, namespace, name FROM artifact_aliases WHERE alias=?", (alias,)
        ).fetchone()
        return None if row is None else (ArtifactKind(row["kind"]), row["namespace"], row["name"])

    def list_aliases(self) -> list[dict[str, str]]:
        return [
            dict(r)
            for r in self.conn.execute(
                "SELECT alias, kind, namespace, name, created_at FROM artifact_aliases "
                "ORDER BY alias"
            ).fetchall()
        ]

    def list_aliases_semantic(self) -> list[dict[str, str]]:
        return [
            dict(r)
            for r in self.conn.execute(
                "SELECT alias, kind, namespace, name FROM artifact_aliases ORDER BY alias"
            ).fetchall()
        ]

    def delete_alias(self, alias: str) -> bool:
        with self.transaction() as conn:
            return conn.execute("DELETE FROM artifact_aliases WHERE alias=?", (alias,)).rowcount > 0

    # ------------------------------------------------------------------ events
    def append_event(
        self,
        event_type: str,
        artifact: str | None,
        actor: str,
        payload: dict[str, Any],
        *,
        event_id: str | None = None,
        created_at: str | None = None,
    ) -> int:
        with self.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO registry_events(event_id, event_type, artifact_uri, actor, "
                "payload_json, created_at) VALUES(?,?,?,?,?,?)",
                (
                    event_id or uuid.uuid4().hex,
                    event_type,
                    artifact,
                    actor,
                    json.dumps(payload, sort_keys=True, default=str),
                    created_at or now_iso(),
                ),
            )
            return int(cur.lastrowid or 0)

    def list_events(
        self, *, limit: int = 100, since_seq: int = 0, type_prefix: str | None = None
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM registry_events WHERE seq > ?"
        args: list[Any] = [since_seq]
        if type_prefix:
            sql += " AND event_type LIKE ?"
            args.append(type_prefix + "%")
        sql += " ORDER BY seq LIMIT ?"
        args.append(limit)
        out = []
        for r in self.conn.execute(sql, args).fetchall():
            d = dict(r)
            d["payload"] = json.loads(d.pop("payload_json"))
            out.append(d)
        return out

    # ------------------------------------------------------------------ snapshots / locks
    def snapshot_id(self) -> str:
        """Deterministic id of the resolvable registry state (spec §52)."""
        from ananke.plexus.registry.hashing import canonical_json, sha256_hex

        rows = self.conn.execute(
            "SELECT a.kind, a.namespace, a.name, v.version, v.payload_sha256, "
            "v.lifecycle_status, v.trust_status, v.channel, v.revision "
            "FROM artifact_versions v JOIN artifacts a ON a.id=v.artifact_id "
            "WHERE v.lifecycle_status != 'archived' "
            "ORDER BY a.kind, a.namespace, a.name, v.version"
        ).fetchall()
        return "sha256:" + sha256_hex(canonical_json([list(r) for r in rows]))

    def save_snapshot(self, snapshot: str, count: int, note: str | None, now: str) -> None:
        with self.transaction() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO registry_snapshots(id, created_at, version_count, note) "
                "VALUES(?,?,?,?)",
                (snapshot, now, count, note),
            )

    def list_snapshots(self) -> list[dict[str, Any]]:
        return [
            dict(r)
            for r in self.conn.execute(
                "SELECT id, created_at, version_count, note FROM registry_snapshots "
                "ORDER BY created_at, id"
            ).fetchall()
        ]

    def record_lock(
        self,
        path: str,
        lock_hash: str,
        snapshot: str | None,
        digests: list[str],
        actor: str,
        now: str,
    ) -> None:
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO locks(path, lock_hash, snapshot_id, digests_json, created_at, actor) "
                "VALUES(?,?,?,?,?,?)",
                (path, lock_hash, snapshot, json.dumps(sorted(set(digests))), now, actor),
            )

    def list_locks(self) -> list[dict[str, Any]]:
        out = []
        for r in self.conn.execute("SELECT * FROM locks ORDER BY id").fetchall():
            d = dict(r)
            d["digests"] = json.loads(d.pop("digests_json"))
            out.append(d)
        return out

    def locked_digests(self) -> set[str]:
        out: set[str] = set()
        for lock in self.list_locks():
            out.update(lock["digests"])
        return out

    # ------------------------------------------------------------------ activations
    def upsert_activation(
        self,
        project: str,
        rec: VersionRecord,
        state: str,
        path: str | None,
        now: str,
    ) -> None:
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO activations(project, kind, namespace, name, version, digest, state, "
                "path, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(project, kind, namespace, name) DO UPDATE SET version=excluded.version, "
                "digest=excluded.digest, state=excluded.state, path=excluded.path, "
                "updated_at=excluded.updated_at",
                (
                    project,
                    rec.kind.value,
                    rec.namespace,
                    rec.name,
                    rec.version,
                    rec.digest_sha256,
                    state,
                    path,
                    now,
                    now,
                ),
            )

    def delete_activation(
        self, project: str, kind: ArtifactKind, namespace: str, name: str
    ) -> bool:
        with self.transaction() as conn:
            return (
                conn.execute(
                    "DELETE FROM activations WHERE project=? AND kind=? AND namespace=? AND name=?",
                    (project, kind.value, namespace, name),
                ).rowcount
                > 0
            )

    def list_activations(self, project: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM activations"
        args: list[Any] = []
        if project is not None:
            sql += " WHERE project=?"
            args.append(project)
        sql += " ORDER BY project, kind, namespace, name"
        return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    # ------------------------------------------------------------------ usage
    def record_usage(
        self, event: str, uri: str, version: str | None, project: str | None, now: str
    ) -> None:
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO usage_events(event, uri, version, project, created_at) "
                "VALUES(?,?,?,?,?)",
                (event, uri, version, project, now),
            )

    def usage_counts(self) -> dict[str, int]:
        return {
            r["uri"]: int(r["n"])
            for r in self.conn.execute(
                "SELECT uri, COUNT(*) AS n FROM usage_events GROUP BY uri"
            ).fetchall()
        }

    # ------------------------------------------------------------------ search index
    def _index_row(self, vid: int, ins: VersionInsert | None = None) -> tuple[Any, ...]:
        if ins is not None:
            m = ins.manifest
            summary, description = m.summary, m.description
        else:
            rec = self.record_by_id(vid)
            m = rec.manifest
            summary, description = rec.summary, rec.description
        tools = " ".join(f"{t.name} {t.description}" for t in m.tools)
        return (
            m.uri,
            vid,
            m.name,
            summary,
            description,
            " ".join(m.metadata.tags),
            " ".join(m.capabilities),
            tools,
            m.namespace,
        )

    def _index(self, vid: int, ins: VersionInsert) -> None:
        if not self.fts_available:
            return
        self.conn.execute(
            "INSERT INTO artifact_fts(uri, version_id, name, summary, description, tags, "
            "capabilities, tools, namespace) VALUES(?,?,?,?,?,?,?,?,?)",
            self._index_row(vid, ins),
        )

    def _unindex(self, vid: int) -> None:
        if self.fts_available:
            self.conn.execute("DELETE FROM artifact_fts WHERE version_id=?", (vid,))

    def reindex(self, vid: int) -> None:
        if not self.fts_available:
            return
        self._unindex(vid)
        self.conn.execute(
            "INSERT INTO artifact_fts(uri, version_id, name, summary, description, tags, "
            "capabilities, tools, namespace) VALUES(?,?,?,?,?,?,?,?,?)",
            self._index_row(vid),
        )

    def rebuild_index(self) -> int:
        """Rebuild the FTS projection from authoritative rows. Returns rows indexed."""
        if not self.fts_available:
            return 0
        with self.transaction() as conn:
            conn.execute("DELETE FROM artifact_fts")
            ids = [
                int(r["id"]) for r in conn.execute("SELECT id FROM artifact_versions").fetchall()
            ]
            for vid in ids:
                conn.execute(
                    "INSERT INTO artifact_fts(uri, version_id, name, summary, description, tags, "
                    "capabilities, tools, namespace) VALUES(?,?,?,?,?,?,?,?,?)",
                    self._index_row(vid),
                )
        return len(ids)

    def index_count(self) -> int:
        if not self.fts_available:
            return 0
        return int(self.conn.execute("SELECT COUNT(*) AS n FROM artifact_fts").fetchone()["n"])

    def fts_match(self, match: str, limit: int) -> list[tuple[int, float]]:
        rows = self.conn.execute(
            "SELECT version_id, bm25(artifact_fts, 0, 0, 10.0, 5.0, 1.0, 4.0, 6.0, 2.0, 1.0) AS rank "
            "FROM artifact_fts WHERE artifact_fts MATCH ? ORDER BY rank LIMIT ?",
            (match, limit),
        ).fetchall()
        return [(int(r["version_id"]), float(r["rank"])) for r in rows]

    # ------------------------------------------------------------------ integrity
    def integrity_check(self) -> list[str]:
        rows = self.conn.execute("PRAGMA integrity_check").fetchall()
        msgs = [str(r[0]) for r in rows]
        return [] if msgs == ["ok"] else msgs

    def foreign_key_violations(self) -> list[str]:
        return [
            f"{r['table']} rowid={r['rowid']} -> {r['parent']}"
            for r in self.conn.execute("PRAGMA foreign_key_check").fetchall()
        ]

    def journal_mode(self) -> str:
        return str(self.conn.execute("PRAGMA journal_mode").fetchone()[0])

    def wal_checkpoint(self) -> None:
        with contextlib.suppress(sqlite3.Error):
            self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    def backup_to(self, target: Path) -> None:
        dest = sqlite3.connect(target)
        try:
            self.conn.backup(dest)
        finally:
            dest.close()


def _split_sql(ddl: str) -> list[str]:
    """Split a DDL script on top-level ``;`` (triggers contain inner ``;`` inside BEGIN…END)."""
    statements: list[str] = []
    buf: list[str] = []
    in_trigger = False
    for line in ddl.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("--"):
            continue
        buf.append(line)
        if re.match(r"(?i)^CREATE TRIGGER", stripped):
            in_trigger = True
        ends = stripped.endswith(";")
        if in_trigger:
            if re.search(r"(?i)\bEND;\s*$", stripped):
                statements.append("\n".join(buf).rstrip().rstrip(";"))
                buf, in_trigger = [], False
        elif ends:
            statements.append("\n".join(buf).rstrip().rstrip(";"))
            buf = []
    if buf:
        statements.append("\n".join(buf).rstrip().rstrip(";"))
    return [s for s in statements if s.strip()]
