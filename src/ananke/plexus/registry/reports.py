"""Registry reports and analytics export (spec §92, §121, §144-§146).

Reports are computed from the authoritative SQLite data with pure Python. The optional
DuckDB projection (``analytics build``) is only a convenience for ad-hoc SQL; everything
here works without it.
"""

from __future__ import annotations

import csv
import io
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from ananke.plexus.registry.errors import RegistryError
from ananke.plexus.registry.models import LifecycleStatus, VersionRecord

if TYPE_CHECKING:
    from ananke.plexus.registry.registry import Registry

REPORTS = (
    "inventory",
    "licenses",
    "trust",
    "stale",
    "deprecated",
    "unused",
    "security",
    "compatibility",
)


class ReportTable(BaseModel):
    name: str
    title: str
    columns: list[str]
    rows: list[list[str]] = Field(default_factory=list)
    note: str = ""

    def to_csv(self) -> str:
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator="\n")
        w.writerow(self.columns)
        w.writerows(self.rows)
        return buf.getvalue()

    def to_json(self) -> str:
        return json.dumps(
            {
                "name": self.name,
                "columns": self.columns,
                "rows": [dict(zip(self.columns, r, strict=True)) for r in self.rows],
                "note": self.note,
            },
            indent=2,
        )


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _row(rec: VersionRecord) -> list[str]:
    return [
        rec.uri.replace("ananke://", ""),
        rec.version,
        rec.lifecycle.value,
        rec.channel,
        rec.trust.value,
    ]


def report(registry: Registry, name: str, *, stale_days: int = 90) -> ReportTable:
    if name not in REPORTS:
        raise RegistryError(f"unknown report {name!r}; choose from: {', '.join(REPORTS)}")
    recs = registry.store.list_records()
    if name == "inventory":
        return ReportTable(
            name=name,
            title="Registry inventory",
            columns=["artifact", "version", "lifecycle", "channel", "trust", "license", "digest"],
            rows=[[*_row(r), r.license.expression or "unknown", r.digest[:19]] for r in recs],
        )
    if name == "licenses":
        agg: dict[tuple[str, str], int] = {}
        for r in recs:
            k = (r.license.expression or "unknown", r.license.approval)
            agg[k] = agg.get(k, 0) + 1
        return ReportTable(
            name=name,
            title="Licenses in the registry",
            columns=["license", "approval", "versions"],
            rows=[[a, b, str(n)] for (a, b), n in sorted(agg.items())],
        )
    if name == "trust":
        agg2: dict[str, int] = {}
        for r in recs:
            agg2[r.trust.value] = agg2.get(r.trust.value, 0) + 1
        return ReportTable(
            name=name,
            title="Trust breakdown",
            columns=["trust", "versions"],
            rows=[[k, str(v)] for k, v in sorted(agg2.items())],
        )
    if name == "stale":
        cutoff = _parse(registry.now()) or datetime.now(UTC)
        limit = cutoff - timedelta(days=stale_days)
        rows = []
        for r in recs:
            if r.lifecycle in {LifecycleStatus.ARCHIVED, LifecycleStatus.QUARANTINED}:
                continue
            verified = _parse(r.quality.last_verified_at)
            if verified is None or verified < limit:
                rows.append([*_row(r)[:2], r.quality.last_verified_at or "never"])
        return ReportTable(
            name=name,
            title=f"Not verified in {stale_days} days",
            columns=["artifact", "version", "last verified"],
            rows=rows,
        )
    if name == "deprecated":
        rows = []
        for r in recs:
            if r.lifecycle in {LifecycleStatus.DEPRECATED, LifecycleStatus.YANKED}:
                dependents = sorted(
                    {
                        f"{d.namespace}/{d.name}@{d.version}"
                        for d, _ in registry.store.dependents_of(r.uri)
                    }
                )
                rows.append([*_row(r)[:3], r.replacement or "", ", ".join(dependents) or "none"])
        return ReportTable(
            name=name,
            title="Deprecated/yanked versions and their dependents",
            columns=["artifact", "version", "lifecycle", "replacement", "dependents"],
            rows=rows,
        )
    if name == "unused":
        usage = registry.store.usage_counts()
        activated = {
            (a["kind"], a["namespace"], a["name"]) for a in registry.store.list_activations()
        }
        rows = []
        for a in registry.store.list_artifacts():
            uri = a.uri
            if (
                usage.get(uri)
                or (a.kind.value, a.namespace, a.name) in activated
                or registry.store.dependents_of(uri)
            ):
                continue
            rows.append(
                [uri.replace("ananke://", ""), a.latest_version or "", str(a.version_count)]
            )
        note = (
            ""
            if registry.policy.telemetry.local_usage
            else "local usage telemetry is disabled; this report only considers dependents and activations"
        )
        return ReportTable(
            name=name,
            title="Artifacts never resolved, activated or depended on",
            columns=["artifact", "latest", "versions"],
            rows=rows,
            note=note,
        )
    if name == "security":
        rows = []
        for r in recs:
            s = r.security
            if s.status != "clean" or s.critical_count or s.findings_count:
                rows.append(
                    [
                        *_row(r)[:2],
                        s.status,
                        str(s.findings_count),
                        str(s.critical_count),
                        s.scanner or "",
                        s.last_scanned_at or "never",
                    ]
                )
        return ReportTable(
            name=name,
            title="Security status",
            columns=["artifact", "version", "status", "findings", "critical", "scanner", "scanned"],
            rows=rows,
        )
    rows = []
    for r in recs:
        c = r.manifest.compatibility
        rows.append(
            [
                *_row(r)[:2],
                c.ananke or "*",
                c.python or "*",
                ",".join(sorted(r.manifest.runtime.supported)) or "any",
                ",".join(c.operating_systems) or "any",
                ",".join(c.architecture) or "any",
            ]
        )
    return ReportTable(
        name=name,
        title="Compatibility",
        columns=["artifact", "version", "ananke", "python", "runtimes", "os", "arch"],
        rows=rows,
    )


class AnalyticsResult(BaseModel):
    directory: str
    files: list[str]
    duckdb: str | None = None
    note: str = ""


def build_analytics(registry: Registry, out_dir: Path | None = None) -> AnalyticsResult:
    """Disposable analytical projection: JSONL tables always; ``registry.duckdb`` if DuckDB exists."""
    out = out_dir or (registry.root / "analytics")
    out.mkdir(parents=True, exist_ok=True)
    tables = analytics_tables(registry)
    files: list[str] = []
    for name, rows in tables.items():
        p = out / f"{name}.jsonl"
        p.write_text(
            "".join(json.dumps(r, sort_keys=True, default=str) + "\n" for r in rows),
            encoding="utf-8",
        )
        files.append(p.name)
    return _finish_analytics(out, tables, files)


def analytics_tables(registry: Registry) -> dict[str, list[dict[str, Any]]]:
    """Flat, engine-neutral projections of the authoritative registry (shared by every consumer)."""
    from ananke.plexus.registry.semver import VersionReq, normalize_version

    tables: dict[str, list[dict[str, Any]]] = {
        "registry_versions": [],
        "registry_dependencies": [],
        "registry_events": [],
        "registry_permissions": [],
        "registry_runtimes": [],
        "registry_locks": [],
    }
    records = registry.store.list_records()
    by_artifact: dict[str, list[VersionRecord]] = {}
    for r in records:
        by_artifact.setdefault(f"ananke://{r.kind.value}/{r.namespace}/{r.name}", []).append(r)
    for rec in records:
        vid = registry.store.version_id(rec.kind, rec.namespace, rec.name, rec.version)
        tables["registry_versions"].append(
            {
                "uri": rec.version_uri,
                "digest": rec.digest_sha256,
                "kind": rec.kind.value,
                "namespace": rec.namespace,
                "name": rec.name,
                "version": rec.version,
                "lifecycle": rec.lifecycle.value,
                "channel": rec.channel,
                "trust": rec.trust.value,
                "license": rec.license.expression or "unknown",
                "framework": ",".join(sorted(rec.manifest.runtime.supported)) or "unspecified",
                "publisher": rec.provenance.publisher.name
                if rec.provenance.publisher
                else "unknown",
                "created_at": rec.created_at,
                "last_verified_at": rec.quality.last_verified_at or "",
                "tests_status": rec.quality.tests_status,
                "security_status": rec.security.status,
            }
        )
        for d in registry.store.dependencies_of(vid):
            resolves_to = ""
            candidates = by_artifact.get(str(d["dependency_id"]), [])
            if candidates:
                try:
                    req = VersionReq.parse(d["version_req"])
                    ok = [c for c in candidates if req.matches(normalize_version(c.version))]
                except RegistryError:
                    ok = []
                if ok:
                    resolves_to = max(ok, key=lambda c: c.semver.sort_key).version
            tables["registry_dependencies"].append(
                {"from": rec.version_uri, **d, "resolves_to": resolves_to}
            )
        runtimes = sorted(set(rec.manifest.runtime.supported)) or ["unspecified"]
        for rt in runtimes:
            tables["registry_runtimes"].append({"uri": rec.version_uri, "runtime": rt})
        for cat, pat in rec.manifest.permissions.flatten():
            tables["registry_permissions"].append(
                {"uri": rec.version_uri, "category": cat, "pattern": pat}
            )
    tables["registry_events"] = registry.store.list_events(limit=1_000_000)
    for lock in registry.store.list_locks():
        for digest in lock["digests"]:
            tables["registry_locks"].append(
                {
                    "digest": digest,
                    "lock_path": lock["path"],
                    "lock_hash": lock["lock_hash"],
                    "created_at": lock["created_at"],
                }
            )
    return tables


def _finish_analytics(
    out: Path, tables: dict[str, list[dict[str, Any]]], files: list[str]
) -> AnalyticsResult:
    try:
        import duckdb
    except ImportError:
        return AnalyticsResult(
            directory=str(out),
            files=files,
            note="duckdb not installed; wrote JSONL projections (pip install ananke-plexus[registry-analytics] for registry.duckdb)",
        )
    db = out / "registry.duckdb"
    db.unlink(missing_ok=True)
    con = duckdb.connect(str(db))
    try:
        for name, rows in tables.items():
            if rows:
                # table names are the fixed keys of ``tables`` above
                sql = f"CREATE TABLE {name} AS SELECT * FROM read_json_auto(?)"  # noqa: S608
                con.execute(sql, [str(out / f"{name}.jsonl")])
    finally:
        con.close()
    return AnalyticsResult(directory=str(out), files=[*files, db.name], duckdb=str(db))


# --------------------------------------------------------------------------- analytics questions

_QUESTION_TABLES: dict[str, list[str]] = {
    "registry_versions": [
        "uri",
        "digest",
        "kind",
        "namespace",
        "name",
        "version",
        "lifecycle",
        "channel",
        "trust",
        "license",
        "created_at",
        "last_verified_at",
        "tests_status",
        "security_status",
    ],
    "registry_dependencies": [
        "from",
        "dependency_type",
        "dependency_id",
        "version_req",
        "optional",
        "resolves_to",
    ],
    "registry_permissions": ["uri", "category", "pattern"],
    "registry_runtimes": ["uri", "runtime"],
    "registry_locks": ["digest", "lock_path", "lock_hash", "created_at"],
}


class Question(BaseModel):
    name: str
    title: str
    sql: str
    params: list[str] = Field(default_factory=list)  # names of caller-supplied parameters


# Portable SQL: identical text runs on DuckDB and on SQLite, so results never depend on which
# engine happened to be installed (spec §146).
QUESTIONS: dict[str, Question] = {
    q.name: q
    for q in [
        Question(
            name="approved-skills-per-runtime",
            title="Approved active skills per runtime",
            sql=(
                "SELECT r.runtime AS runtime, COUNT(DISTINCT v.uri) AS approved_skills "
                "FROM registry_versions v JOIN registry_runtimes r ON r.uri = v.uri "
                "WHERE v.kind = 'skill' AND v.trust = 'approved' AND v.lifecycle = 'active' "
                "GROUP BY r.runtime ORDER BY approved_skills DESC, runtime"
            ),
        ),
        Question(
            name="agents-on-deprecated",
            title="Agents whose dependencies resolve to deprecated or yanked versions",
            sql=(
                "SELECT a.uri AS agent, d.dependency_id AS dependency, d.resolves_to AS version, "
                "dep.lifecycle AS lifecycle "
                'FROM registry_versions a JOIN registry_dependencies d ON d."from" = a.uri '
                "JOIN registry_versions dep ON dep.uri = d.dependency_id || '@' || d.resolves_to "
                "WHERE a.kind = 'agent' AND dep.lifecycle IN ('deprecated', 'yanked') "
                "ORDER BY agent, dependency"
            ),
        ),
        Question(
            name="network-skills",
            title="Skills that request network permissions",
            sql=(
                "SELECT v.uri AS skill, p.pattern AS network_access "
                "FROM registry_permissions p JOIN registry_versions v ON v.uri = p.uri "
                "WHERE p.category = 'network' AND v.kind = 'skill' ORDER BY skill, network_access"
            ),
        ),
        Question(
            name="not-verified-recently",
            title="Versions never verified, or not verified since the cutoff (--days, default 90)",
            sql=(
                "SELECT uri, trust, COALESCE(NULLIF(last_verified_at, ''), 'never') AS last_verified "
                "FROM registry_versions "
                "WHERE lifecycle = 'active' AND (last_verified_at = '' OR last_verified_at < ?) "
                "ORDER BY uri"
            ),
            params=["cutoff"],
        ),
        Question(
            name="locked-versions",
            title="Versions pinned by project lockfiles",
            sql=(
                "SELECT v.uri AS version, l.lock_path AS lockfile, l.created_at AS locked_at "
                "FROM registry_locks l JOIN registry_versions v ON v.digest = l.digest "
                "ORDER BY version, lockfile"
            ),
        ),
        Question(
            name="bundle-licenses",
            title="Licenses of the members of active bundles",
            sql=(
                "SELECT DISTINCT b.uri AS bundle, m.license AS license "
                'FROM registry_versions b JOIN registry_dependencies d ON d."from" = b.uri '
                "JOIN registry_versions m ON m.uri = d.dependency_id || '@' || d.resolves_to "
                "WHERE b.kind = 'bundle' AND b.lifecycle = 'active' ORDER BY bundle, license"
            ),
        ),
    ]
}


class QuestionResult(BaseModel):
    name: str
    title: str
    engine: str
    columns: list[str]
    rows: list[list[Any]]


def _connect(engine: str) -> Any:
    if engine == "duckdb":
        import duckdb

        return duckdb.connect(":memory:")
    import sqlite3

    return sqlite3.connect(":memory:")


def answer_question(
    registry: Registry,
    name: str,
    *,
    days: int = 90,
    engine: str = "auto",
) -> QuestionResult:
    """Run a named analytics question against a fresh in-memory projection of the registry.

    ``engine`` is ``auto`` (DuckDB when installed, else SQLite), ``duckdb`` or ``sqlite``. The
    projection is rebuilt every call, so answers can never be stale.
    """
    question = QUESTIONS.get(name)
    if question is None:
        raise RegistryError(
            f"unknown analytics question {name!r}; choose from: {', '.join(QUESTIONS)}",
            code="UNKNOWN_QUESTION",
        )
    if days < 0:
        raise RegistryError("--days must be >= 0")
    if engine not in {"auto", "duckdb", "sqlite"}:
        raise RegistryError("engine must be auto, duckdb or sqlite")
    used = engine
    if engine == "auto":
        try:
            import duckdb

            used = "duckdb"
        except ImportError:
            used = "sqlite"
    elif engine == "duckdb":
        try:
            import duckdb  # noqa: F401
        except ImportError as exc:
            raise RegistryError(
                "duckdb is not installed (pip install ananke-plexus[registry-analytics])",
                code="ANALYTICS_UNAVAILABLE",
            ) from exc

    tables = analytics_tables(registry)
    con = _connect(used)
    try:
        for table, columns in _QUESTION_TABLES.items():
            cols = ", ".join(
                f'"{c}" VARCHAR' if used == "duckdb" else f'"{c}" TEXT' for c in columns
            )
            con.execute(f"CREATE TABLE {table} ({cols})")
            marks = ", ".join("?" for _ in columns)
            rows = [
                [None if r.get(c) is None else str(r.get(c)) for c in columns]
                for r in tables[table]
            ]
            if rows:
                con.executemany(f"INSERT INTO {table} VALUES ({marks})", rows)  # noqa: S608
        params: list[str] = []
        if "cutoff" in question.params:
            params.append((datetime.now(UTC) - timedelta(days=days)).isoformat(timespec="seconds"))
        cur = con.execute(question.sql, params)
        columns = [d[0] for d in cur.description]
        data = [list(row) for row in cur.fetchall()]
    finally:
        con.close()
    return QuestionResult(
        name=question.name, title=question.title, engine=used, columns=columns, rows=data
    )
