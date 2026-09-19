"""Registry verification and doctor (spec §105, §136)."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

from ananke.plexus.registry.cache import KVCache
from ananke.plexus.registry.errors import RegistryError
from ananke.plexus.registry.hashing import blake3_available, canonical_json
from ananke.plexus.registry.lockfile import load_lock, verify_lock
from ananke.plexus.registry.models import artifact_uri, parse_ref
from ananke.plexus.registry.payload import MANIFEST_FILE, unpack_files
from ananke.plexus.registry.semver import Version

if TYPE_CHECKING:
    from ananke.plexus.registry.registry import Registry

Severity = Literal["error", "warning", "info"]


class Check(BaseModel):
    name: str
    ok: bool
    severity: Severity = "error"
    detail: str = ""


class VerifyReport(BaseModel):
    checks: list[Check] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.checks if c.severity == "error")

    @property
    def failures(self) -> list[Check]:
        return [c for c in self.checks if not c.ok]


def _cycles(edges: dict[str, set[str]]) -> list[list[str]]:
    found: list[list[str]] = []
    state: dict[str, int] = {}

    def visit(node: str, trail: list[str]) -> None:
        if state.get(node) == 2:
            return
        if state.get(node) == 1:
            found.append([*trail[trail.index(node) :], node])
            return
        state[node] = 1
        for nxt in sorted(edges.get(node, ())):
            visit(nxt, [*trail, node])
        state[node] = 2

    for n in sorted(edges):
        visit(n, [])
    return found


def verify(
    registry: Registry, *, deep: bool = True, lock_paths: list[Path] | None = None
) -> VerifyReport:
    """Integrity checks over SQLite, blobs, graph, aliases, index and lockfiles."""
    r = VerifyReport()
    store = registry.store

    def add(name: str, ok: bool, detail: str = "", severity: Severity = "error") -> None:
        r.checks.append(Check(name=name, ok=ok, severity=severity, detail=detail))

    problems = store.integrity_check()
    add("sqlite-integrity", not problems, "; ".join(problems[:3]) or "ok")
    fk = store.foreign_key_violations()
    add("foreign-keys", not fk, "; ".join(fk[:3]) or "ok")

    records = store.list_records()
    bad_blobs: list[str] = []
    bad_manifest: list[str] = []
    for rec in records:
        if not registry.cas.exists(rec.digest_sha256):
            bad_blobs.append(f"{rec.version_uri}: blob missing")
            continue
        if deep:
            try:
                payload = registry.cas.get(rec.digest_sha256)
            except RegistryError as exc:
                bad_blobs.append(f"{rec.version_uri}: {exc}")
                continue
            try:
                embedded = unpack_files(payload).get(MANIFEST_FILE)
            except RegistryError as exc:
                bad_blobs.append(f"{rec.version_uri}: {exc}")
                continue
            if embedded is None or embedded.rstrip(b"\n") != canonical_json(
                rec.manifest.canonical_dict()
            ):
                bad_manifest.append(rec.version_uri)
    add(
        "blobs",
        not bad_blobs,
        "; ".join(bad_blobs[:3])
        or f"{len(records)} blob(s) present" + (" and hash-verified" if deep else ""),
    )
    if deep:
        add(
            "embedded-manifests",
            not bad_manifest,
            ", ".join(bad_manifest[:3]) or "payload manifests match records",
        )

    invalid = []
    seen: dict[tuple[str, str], str] = {}
    dupes = []
    for rec in records:
        try:
            v = Version.parse(rec.version)
        except RegistryError:
            invalid.append(rec.version_uri)
            continue
        key = (rec.uri, str(v.precedence_key))
        if key in seen and seen[key] != rec.version:
            dupes.append(f"{rec.uri}: {seen[key]} vs {rec.version}")
        seen[key] = rec.version
    add("semver", not invalid, ", ".join(invalid[:3]) or "all versions are valid SemVer")
    add("duplicate-versions", not dupes, "; ".join(dupes[:3]) or "no duplicates")

    edges: dict[str, set[str]] = {}
    unknown: set[str] = set()
    for rec in records:
        for dep in rec.manifest.artifact_dependencies():
            if not dep.id:
                continue
            edges.setdefault(rec.uri, set()).add(dep.id)
            ref = parse_ref(dep.id)
            if (
                ref.kind
                and ref.namespace
                and store.artifact_id(ref.kind, ref.namespace, ref.name) is None
            ):
                unknown.add(f"{rec.uri} -> {dep.id}")
    cyc = _cycles(edges)
    add("dependency-cycles", not cyc, " ; ".join(" -> ".join(c) for c in cyc[:2]) or "acyclic")
    add(
        "dependency-targets",
        not unknown,
        ", ".join(sorted(unknown)[:3]) or "all dependency artifacts registered",
        "warning",
    )

    dangling = []
    for al in store.list_aliases():
        if store.artifact_id(al["kind"], al["namespace"], al["name"]) is None:
            dangling.append(al["alias"])
    add("aliases", not dangling, ", ".join(dangling) or "no dangling aliases")

    if store.fts_available:
        n, idx = store.count_versions(), store.index_count()
        add(
            "search-index",
            n == idx,
            f"{idx} indexed vs {n} versions"
            + ("" if n == idx else " (run `ananke registry rebuild-index`)"),
        )
    else:
        add("search-index", True, "FTS5 unavailable; using Python scorer", "info")

    lock_files = list(lock_paths or [])
    if registry.project_root is not None:
        default = registry.project_root / "ananke.lock"
        if default.is_file() and default not in lock_files:
            lock_files.append(default)
    for lock_record in store.list_locks():
        p = Path(lock_record["path"])
        if p.is_file() and p not in lock_files:
            lock_files.append(p)
    lock_problems: list[str] = []
    for lf in lock_files:
        try:
            for c in verify_lock(registry, load_lock(lf)):
                if not c.ok:
                    lock_problems.append(f"{lf.name}: {c.id}: {c.detail}")
        except RegistryError as exc:
            lock_problems.append(f"{lf.name}: {exc}")
    add(
        "lockfiles",
        not lock_problems,
        "; ".join(lock_problems[:3]) or f"{len(lock_files)} lockfile(s) consistent",
    )

    orphans = sorted(
        set(registry.cas.iter_digests())
        - {rec.digest_sha256 for rec in records}
        - store.locked_digests()
    )
    add(
        "orphan-blobs",
        not orphans,
        f"{len(orphans)} unreferenced blob(s) (run `ananke registry gc`)" if orphans else "none",
        "info",
    )
    return r


def doctor(registry: Registry) -> VerifyReport:
    """Operational health: quick verify + WAL, cache, resolver, templates, importers, accelerators."""
    report = verify(registry, deep=False)

    def add(name: str, ok: bool, detail: str = "", severity: Severity = "error") -> None:
        report.checks.append(Check(name=name, ok=ok, severity=severity, detail=detail))

    mode = registry.store.journal_mode()
    add("wal-mode", mode == "wal", f"journal_mode={mode}", "warning")
    schema = registry.store.schema_version()
    add("schema-version", schema >= 1, f"schema v{schema}")
    try:
        _ = registry.policy
        add("policy", True, "policy loaded")
    except RegistryError as exc:
        add("policy", False, str(exc))

    with tempfile.TemporaryDirectory(prefix="ananke-cache-check-") as tmp:
        cache = KVCache(Path(tmp) / "c.db")
        cache.set("k", "v")
        add(
            "cache-rebuildable",
            cache.get("k") == "v",
            "derived cache round-trips (never authoritative)",
        )

    try:
        from ananke.plexus.registry.resolver import Resolver

        for art in registry.store.list_artifacts():
            Resolver(registry).resolve(f"{art.namespace}/{art.name}", kind=art.kind)
        add(
            "resolver",
            True,
            f"resolved {len(registry.store.list_artifacts())} artifact(s) without errors",
        )
    except Exception as exc:
        add("resolver", False, f"resolver raised: {exc}")

    try:
        from ananke.plexus.registry.docsgen.model import RegistryDocumentationModel
        from ananke.plexus.registry.docsgen.render import Site

        site = Site(RegistryDocumentationModel())
        for spec in site.pages():
            site.render_body(spec)
        add("docs-templates", True, "templates render")
    except Exception as exc:
        add("docs-templates", False, str(exc))

    from ananke.plexus.registry.importers.registry import default_registry

    rows = default_registry().availability()
    broken = [r["id"] for r in rows if not r.get("available", True)]
    add(
        "importers",
        not broken,
        f"{len(rows) - len(broken)} available"
        + (f"; failed plugins: {', '.join(broken)}" if broken else ""),
        "warning",
    )

    from ananke.plexus.registry.compression import zstd_available

    try:
        import duckdb  # noqa: F401

        has_duck = True
    except ImportError:
        has_duck = False
    try:
        import jsonschema  # noqa: F401

        has_js = True
    except ImportError:
        has_js = False
    add(
        "accelerators",
        True,
        f"fts5={'yes' if registry.store.fts_available else 'no'}, blake3={'yes' if blake3_available() else 'no'}, "
        f"zstd={'yes' if zstd_available() else 'no'}, duckdb={'yes' if has_duck else 'no'}, jsonschema={'yes' if has_js else 'no'} "
        "(all optional; the registry works without them)",
        "info",
    )
    return report


__all__ = ["Check", "VerifyReport", "artifact_uri", "doctor", "verify"]
