"""The ``learn`` workflow (spec §41, §42, §108, §118, §147, §148, §156).

    source → probe → inspect → normalize → compare existing → validate → register

``learn`` never silently merges by name: it matches identity through an explicit
``ananke_id``, then source package + native identifier, then aliases; a bare name match
is only ever a low-confidence *suggestion*.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Field

from ananke.plexus.registry.cache import KVCache
from ananke.plexus.registry.diff import VersionDiff, diff_manifests
from ananke.plexus.registry.errors import (
    ImporterError,
    ManifestError,
    PolicyViolationError,
    RegistryError,
    SecretDetectedError,
    VersionContentConflictError,
)
from ananke.plexus.registry.importers.base import (
    Candidate,
    ImportedArtifact,
    InspectionContext,
    finalize,
    parse_source,
)
from ananke.plexus.registry.importers.registry import ImporterRegistry, default_registry
from ananke.plexus.registry.models import ArtifactKind, Diagnostic, VersionRecord
from ananke.plexus.registry.registry import RegisterResult

if TYPE_CHECKING:
    from ananke.plexus.registry.registry import Registry

Action = Literal[
    "registered", "unchanged", "conflict", "review", "denied", "dry-run", "incomplete", "skipped"
]


class LearnCandidate(BaseModel):
    source: str
    importer: str
    action: Action
    uri: str | None = None
    version: str | None = None
    message: str = ""
    diagnostics: list[Diagnostic] = Field(default_factory=list)
    diff: VersionDiff | None = None
    suggested_version: str | None = None
    identity_notes: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)
    permission_concerns: list[str] = Field(default_factory=list)
    dynamic: bool = False
    result: RegisterResult | None = None


class LearnReport(BaseModel):
    source: str
    candidates: list[LearnCandidate] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(
            c.action in {"registered", "unchanged", "dry-run", "skipped"} for c in self.candidates
        )

    def registered(self) -> list[LearnCandidate]:
        return [c for c in self.candidates if c.action == "registered"]


def permission_concerns(imported: ImportedArtifact) -> list[str]:
    perms = imported.manifest.permissions
    out: list[str] = []
    if perms.network:
        out.append(f"network access: {', '.join(perms.network)}")
    if any(p in {"**", "*"} for p in perms.filesystem.read):
        out.append("unrestricted filesystem read")
    if any(p in {"**", "*"} for p in perms.filesystem.write):
        out.append("unrestricted filesystem write")
    elif perms.filesystem.write:
        out.append(f"filesystem write: {', '.join(perms.filesystem.write)}")
    if perms.shell.allow:
        out.append(f"shell execution: {', '.join(perms.shell.allow)}")
    return out


def _identity_notes(registry: Registry, imported: ImportedArtifact) -> tuple[list[str], bool]:
    """Return (notes, ambiguous). Ambiguous means "looks like another artifact — human decides"."""
    m = imported.manifest
    notes: list[str] = []
    ambiguous = False
    store = registry.store
    own = (m.kind, m.namespace, m.name)
    # 1. explicit ananke_id
    if m.ananke_id:
        for rec in store.list_records(m.kind):
            if rec.manifest.ananke_id == m.ananke_id and (rec.kind, rec.namespace, rec.name) != own:
                notes.append(f"ananke_id {m.ananke_id} already belongs to {rec.uri}")
                ambiguous = True
                break
    # 2. source package + native identifier
    prov = imported.provenance
    if prov.native_id and prov.source_name:
        row = store.conn.execute(
            "SELECT a.kind, a.namespace, a.name FROM artifact_sources s "
            "JOIN artifact_versions v ON v.id = s.version_id JOIN artifacts a ON a.id = v.artifact_id "
            "WHERE s.source_type=? AND s.source_name=? AND s.native_id=? LIMIT 1",
            (prov.source_type, prov.source_name, prov.native_id),
        ).fetchone()
        if row is not None and (row["kind"], row["namespace"], row["name"]) != (
            m.kind.value,
            m.namespace,
            m.name,
        ):
            notes.append(
                f"same source identifier ({prov.source_name}:{prov.native_id}) was previously "
                f"registered as {row['namespace']}/{row['name']}"
            )
            ambiguous = True
    # 3. alias table
    alias = store.get_alias(m.name)
    if alias is not None and alias[1:] != (m.namespace, m.name):
        notes.append(f"name {m.name!r} is an alias for {alias[1]}/{alias[2]}")
    # 4. low-confidence name-only suggestion
    for art in store.list_artifacts(m.kind):
        if art.name == m.name and art.namespace != m.namespace:
            notes.append(
                f"low confidence: same name exists in namespace {art.namespace!r} (not merged)"
            )
    return notes, ambiguous


def _apply_overrides(**values: Any) -> dict[str, Any]:
    return {k: v for k, v in values.items() if v not in (None, "")}


def learn(
    registry: Registry,
    source: str,
    *,
    kind: ArtifactKind | str | None = None,
    namespace: str | None = None,
    name: str | None = None,
    version: str | None = None,
    license: str | None = None,
    channel: str | None = None,
    allow_dynamic: bool = False,
    allow_network: bool = False,
    dry_run: bool = False,
    register: bool = True,
    force: bool = False,
    plugin: str | None = None,
    plugin_paths: list[str] | None = None,
    overrides: dict[str, Any] | None = None,
    importers: ImporterRegistry | None = None,
) -> LearnReport:
    """Discover ``source`` and (unless ``dry_run``/``register=False``) register new versions."""
    ctx = InspectionContext(
        policy=registry.policy,
        allow_dynamic=allow_dynamic,
        allow_network=allow_network,
        namespace=namespace,
        kind=ArtifactKind(kind) if kind else None,
        plugin_paths=plugin_paths or [],
        plugin=plugin,
        project_root=registry.project_root,
    )
    report = LearnReport(source=source)
    reg = importers or default_registry()
    src = parse_source(source)
    cache = KVCache(registry.root / "cache.db")
    plan = reg.plan(src, ctx)
    registry.emit(
        "artifact.discovered", None, {"source": source, "importers": [i.id for i, _ in plan]}
    )

    merged_overrides = _apply_overrides(
        name=name,
        namespace=namespace,
        license={"expression": license} if license else None,
        **(overrides or {}),
    )
    for importer, sub in plan:
        try:
            candidates: list[Candidate] = importer.inspect(sub, ctx)
        except PolicyViolationError as exc:
            report.candidates.append(
                LearnCandidate(
                    source=sub.raw, importer=importer.id, action="denied", message=str(exc)
                )
            )
            continue
        except RegistryError as exc:
            report.candidates.append(
                LearnCandidate(
                    source=sub.raw, importer=importer.id, action="incomplete", message=str(exc)
                )
            )
            continue
        for cand in candidates:
            report.candidates.append(
                _process(
                    registry,
                    importer.id,
                    cand,
                    ctx,
                    cache,
                    merged_overrides,
                    version=version,
                    channel=channel,
                    dry_run=dry_run,
                    register=register,
                    force=force,
                )
            )
    return report


def _process(
    registry: Registry,
    importer_id: str,
    cand: Candidate,
    ctx: InspectionContext,
    cache: KVCache,
    overrides: dict[str, Any],
    *,
    version: str | None,
    channel: str | None,
    dry_run: bool,
    register: bool,
    force: bool,
) -> LearnCandidate:
    base = LearnCandidate(
        source=cand.source,
        importer=importer_id,
        action="incomplete",
        dynamic=cand.dynamic,
        diagnostics=list(cand.diagnostics),
        missing=cand.missing_required(),
    )
    ver_override = {"version": version} if version and version != "auto" else {}
    try:
        imported = finalize(cand, overrides={**overrides, **ver_override}, ctx=ctx)
    except ManifestError as exc:
        base.message = str(exc)
        base.missing = [
            f for f in cand.missing_required() if f not in {**overrides, **ver_override}
        ]
        return base
    m = imported.manifest
    base.uri, base.diagnostics = m.uri, imported.diagnostics
    base.version = m.version
    base.permission_concerns = permission_concerns(imported)

    fp_key = f"fp:{m.uri}:{cand.provenance.source_type}:{cand.provenance.source_name}"
    fingerprint = cand.provenance.fingerprint
    if fingerprint and not force and not dry_run and cache.get(fp_key) == fingerprint:
        existing = registry.store.versions_of(m.kind, m.namespace, m.name)
        if existing:
            base.action, base.message = (
                "skipped",
                "source unchanged since last learn (fingerprint match)",
            )
            base.version = existing[-1].version
            return base

    notes, ambiguous = _identity_notes(registry, imported)
    base.identity_notes = notes
    existing_versions = registry.store.versions_of(m.kind, m.namespace, m.name)
    latest: VersionRecord | None = existing_versions[-1] if existing_versions else None
    if latest is not None:
        base.diff = diff_manifests(
            latest.manifest,
            m,
            old_files=registry.files(latest),
            new_files=imported.files,
            with_text=False,
        )
        base.suggested_version = (
            None
            if base.diff.suggested_bump == "NONE"
            else _bumped(latest.version, base.diff.suggested_bump)
        )
    if ambiguous:
        base.action = "review"
        base.message = (
            "identity is ambiguous; resolve with an explicit ananke_id/namespace or alias"
        )
        return base
    try:
        probe = registry.register(imported, version=version, channel=channel, dry_run=True)
    except VersionContentConflictError as exc:
        base.action = "conflict"
        base.message = str(exc)
        return base
    except (PolicyViolationError, SecretDetectedError, ManifestError, RegistryError) as exc:
        base.action = "denied"
        base.message = str(exc)
        return base
    base.diagnostics = probe.diagnostics or base.diagnostics
    base.version = probe.version
    if probe.already_registered:
        base.action, base.message = "unchanged", "already registered with identical content"
        base.result = probe
        if fingerprint:
            cache.set(fp_key, fingerprint)
        return base
    if dry_run or not register:
        base.action, base.message, base.result = "dry-run", "would register", probe
        return base
    result = registry.register(imported, version=version, channel=channel)
    base.action, base.result, base.version = "registered", result, result.version
    base.message = f"registered {result.version_uri}"
    registry.emit("artifact.imported", m.uri, {"version": result.version, "importer": importer_id})
    if fingerprint:
        cache.set(fp_key, fingerprint)
    _refresh_docs(registry)
    return base


def _bumped(version: str, bump: str) -> str:
    from ananke.plexus.registry.diff import apply_bump

    return apply_bump(version, bump)  # type: ignore[arg-type]


def _refresh_docs(registry: Registry) -> None:
    """Incremental docs rebuild after a registry event — only if docs were built before."""
    docs = registry.root / "docs"
    if (docs / ".build-cache.json").exists():
        try:
            from ananke.plexus.registry.docsgen.builder import build_site

            build_site(registry)
        except Exception:  # noqa: S110 - docs are a projection; never fail a registration
            pass


def inspect_source(registry: Registry, source: str, **kwargs: Any) -> LearnReport:
    """Pre-registration report (spec §108): discover everything, write nothing."""
    kwargs.pop("dry_run", None)
    kwargs.pop("register", None)
    return learn(registry, source, dry_run=True, register=False, **kwargs)


__all__ = [
    "ImporterError",
    "LearnCandidate",
    "LearnReport",
    "inspect_source",
    "learn",
    "permission_concerns",
]
