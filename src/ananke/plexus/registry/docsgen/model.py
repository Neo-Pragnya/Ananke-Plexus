"""Stable documentation model (spec §110): templates never query SQLite directly.

``DB → RegistryDocumentationModel → renderer → HTML``. The model is deterministic for a
given registry state (no wall-clock values), which makes generated sites snapshot-testable.
"""

from __future__ import annotations

import json
from collections import Counter
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from ananke.plexus.registry.diff import VersionDiff, diff_manifests
from ananke.plexus.registry.errors import RegistryError
from ananke.plexus.registry.models import (
    TRUST_RANK,
    ArtifactKind,
    LifecycleStatus,
    TrustStatus,
    VersionRecord,
    artifact_uri,
    parse_ref,
)
from ananke.plexus.registry.quality import badges
from ananke.plexus.registry.semver import VersionReq
from ananke.plexus.registry.signing import SignatureStatus

if TYPE_CHECKING:
    from ananke.plexus.registry.registry import Registry

MODEL_VERSION = 1
_README_NAMES = ("README.md", "SKILL.md", "AGENT.md", "README.markdown", "README.txt")
_INACTIVE = {LifecycleStatus.YANKED, LifecycleStatus.QUARANTINED, LifecycleStatus.ARCHIVED}
_FIXED_MATRIX_COLUMNS = ["network", "filesystem.write", "shell.execute"]


class DocTool(BaseModel):
    name: str
    description: str = ""
    input_schema: str | None = None
    output_schema: str | None = None


class DocDependency(BaseModel):
    type: str
    label: str
    version: str | None = None
    optional: bool = False
    target_uri: str | None = None
    resolved_version: str | None = None


class DocChange(BaseModel):
    from_version: str
    to_version: str
    suggested_bump: str
    breaking: bool
    reasons: list[str] = Field(default_factory=list)


class DocVersion(BaseModel):
    version: str
    ref: str
    uri: str
    lifecycle: str
    channel: str
    trust: str
    summary: str = ""
    description: str = ""
    digest: str
    created_at: str
    revision: int = 0
    badges: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    runtimes: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    tools: list[DocTool] = Field(default_factory=list)
    inputs: str | None = None
    outputs: str | None = None
    permissions: list[tuple[str, str]] = Field(default_factory=list)
    dependencies: list[DocDependency] = Field(default_factory=list)
    compatibility: list[tuple[str, str]] = Field(default_factory=list)
    license: str | None = None
    license_approval: str = "unknown"
    provenance: dict[str, Any] = Field(default_factory=dict)
    quality: dict[str, Any] = Field(default_factory=dict)
    security: dict[str, Any] = Field(default_factory=dict)
    owners: list[str] = Field(default_factory=list)
    maintainers: list[str] = Field(default_factory=list)
    files: list[tuple[str, int]] = Field(default_factory=list)
    test_files: list[str] = Field(default_factory=list)
    readme: str = ""
    instructions_ref: str | None = None
    model_requirements: dict[str, Any] = Field(default_factory=dict)
    skills: list[str] = Field(default_factory=list)
    policies: list[str] = Field(default_factory=list)
    eval_suite: str | None = None
    lifecycle_message: str | None = None
    replacement: str | None = None
    changes_from_previous: DocChange | None = None

    @property
    def page(self) -> str:
        return self.version.replace("+", "_")


class DocArtifact(BaseModel):
    kind: str
    namespace: str
    name: str
    uri: str
    default_version: str
    latest_approved: str | None = None
    latest_stable: str | None = None
    latest: str
    versions: list[DocVersion]
    used_by: list[str] = Field(default_factory=list)
    related: list[str] = Field(default_factory=list)

    @property
    def title(self) -> str:
        return f"{self.namespace}/{self.name}"

    def version(self, v: str) -> DocVersion:
        return next(x for x in self.versions if x.version == v)


class DocComparison(BaseModel):
    uri: str
    from_version: str
    to_version: str
    diff: VersionDiff


class MatrixRow(BaseModel):
    uri: str
    title: str
    kind: str
    version: str
    cells: list[bool]


class RegistryDocumentationModel(BaseModel):
    model_version: int = MODEL_VERSION
    registry_id: str = ""
    snapshot: str = ""
    counts: dict[str, int] = Field(default_factory=dict)
    lifecycle_counts: dict[str, int] = Field(default_factory=dict)
    trust_breakdown: dict[str, int] = Field(default_factory=dict)
    license_breakdown: dict[str, int] = Field(default_factory=dict)
    frameworks: dict[str, list[str]] = Field(default_factory=dict)
    publishers: dict[str, list[str]] = Field(default_factory=dict)
    capabilities: dict[str, list[str]] = Field(default_factory=dict)
    capability_columns: list[str] = Field(default_factory=list)
    capability_matrix: list[MatrixRow] = Field(default_factory=list)
    latest_changes: list[tuple[str, str, str]] = Field(default_factory=list)
    artifacts: list[DocArtifact] = Field(default_factory=list)
    comparisons: list[DocComparison] = Field(default_factory=list)
    search_index: list[dict[str, Any]] = Field(default_factory=list)
    kinds_present: list[str] = Field(default_factory=list)

    def artifact(self, uri: str) -> DocArtifact | None:
        return next((a for a in self.artifacts if a.uri == uri), None)


def _pretty(schema: dict[str, Any] | None) -> str | None:
    return None if schema is None else json.dumps(schema, indent=2, sort_keys=True)


def _readme(files: dict[str, bytes]) -> str:
    for name in _README_NAMES:
        if name in files:
            return files[name].decode("utf-8", errors="replace")
    return ""


def _owner_names(rec: VersionRecord) -> tuple[list[str], list[str]]:
    owners = sorted({o.team or o.user or "" for o in rec.manifest.owners} - {""})
    return owners, sorted(rec.manifest.maintainers)


def _provenance(rec: VersionRecord, sigs: list[SignatureStatus]) -> dict[str, Any]:
    p = rec.provenance
    out: dict[str, Any] = {
        "source_type": p.source_type,
        "source_name": p.source_name,
        "source_version": p.source_version,
        "source_url": p.source_url,
        "native_id": p.native_id,
        "native_version": p.native_version,
        "discovered_at": p.discovered_at,
        "importer": f"{p.importer.id} {p.importer.version}" if p.importer else None,
        "publisher": p.publisher.name if p.publisher else None,
        "git_repository": p.git.repository if p.git else None,
        "git_commit": p.git.commit if p.git else None,
        "signatures": "; ".join(
            f"{s.algorithm} {s.key_id} by {s.signer or 'unknown'} "
            f"({'verified' if s.verified else 'unverified: ' + s.reason})"
            for s in sigs
        )
        or None,
        "payload_sha256": rec.digest_sha256,
        "payload_blake3": rec.digest_blake3,
    }
    return {k: v for k, v in out.items() if v}


def _framework_of(rec: VersionRecord) -> list[str]:
    names = set(rec.manifest.runtime.supported)
    st = rec.provenance.source_type
    if st.startswith(("framework:", "dynamic:")):
        names.add(st.split(":", 1)[1])
    return sorted(names)


def _highest(recs: list[VersionRecord]) -> VersionRecord | None:
    return max(recs, key=lambda r: (r.semver.sort_key, r.version)) if recs else None


def build_documentation_model(
    registry: Registry, *, with_diffs: bool = True
) -> RegistryDocumentationModel:
    store = registry.store
    all_records = store.list_records()
    model = RegistryDocumentationModel(
        registry_id=registry.registry_id, snapshot=registry.snapshot_id()
    )
    by_key: dict[tuple[str, str, str], list[VersionRecord]] = {}
    for rec in all_records:
        by_key.setdefault(rec.key, []).append(rec)

    kind_counts: Counter[str] = Counter()
    life: Counter[str] = Counter()
    trust: Counter[str] = Counter()
    lic: Counter[str] = Counter()
    file_cache: dict[str, dict[str, bytes]] = {}

    def files_of(rec: VersionRecord) -> dict[str, bytes]:
        if rec.digest_sha256 not in file_cache:
            try:
                file_cache[rec.digest_sha256] = registry.files(rec)
            except RegistryError:
                file_cache[rec.digest_sha256] = {}
        return file_cache[rec.digest_sha256]

    def resolve_dep_version(uri: str, req: str | None) -> tuple[str | None, str | None]:
        ref = parse_ref(uri)
        if ref.kind is None or ref.namespace is None:
            return None, None
        versions = [
            v
            for v in by_key.get((ref.kind.value, ref.namespace, ref.name), [])
            if v.lifecycle not in _INACTIVE
        ]
        vr = VersionReq.parse(req)
        matching = [v for v in versions if vr.matches(v.semver)]
        best = _highest(matching)
        return (uri, best.version) if best else (uri if versions else None, None)

    used_by: dict[str, set[str]] = {}
    for rec in all_records:
        for dep in rec.manifest.artifact_dependencies():
            if dep.id:
                used_by.setdefault(dep.id, set()).add(f"{rec.namespace}/{rec.name}")

    caps_index: dict[str, set[str]] = {}
    for key, recs in sorted(by_key.items()):
        recs.sort(key=lambda r: (r.semver.sort_key, r.version))
        kind_counts[key[0]] += 1
        doc_versions: list[DocVersion] = []
        prev: VersionRecord | None = None
        for rec in recs:
            life[rec.lifecycle.value] += 1
            trust[rec.trust.value] += 1
            lic[rec.license.expression or "unknown"] += 1
            files = files_of(rec)
            sigs = registry.signatures(rec)
            m = rec.manifest
            deps: list[DocDependency] = []
            for d in m.all_dependencies():
                if d.artifact_ref is not None and d.id:
                    target, resolved = resolve_dep_version(d.id, d.version)
                    ref = d.artifact_ref
                    deps.append(
                        DocDependency(
                            type=d.type.value,
                            label=f"{ref.namespace}/{ref.name}",
                            version=d.version,
                            optional=d.optional,
                            target_uri=target,
                            resolved_version=resolved,
                        )
                    )
                else:
                    deps.append(
                        DocDependency(
                            type=d.type.value,
                            label=d.name or d.id or "",
                            version=d.version,
                            optional=d.optional,
                        )
                    )
            change: DocChange | None = None
            if with_diffs and prev is not None:
                diff = diff_manifests(
                    prev.manifest, m, old_files=files_of(prev), new_files=files, with_text=True
                )
                model.comparisons.append(
                    DocComparison(
                        uri=rec.uri, from_version=prev.version, to_version=rec.version, diff=diff
                    )
                )
                change = DocChange(
                    from_version=prev.version,
                    to_version=rec.version,
                    suggested_bump=diff.suggested_bump,
                    breaking=diff.breaking,
                    reasons=diff.reasons[:8],
                )
            owners, maintainers = _owner_names(rec)
            doc_versions.append(
                DocVersion(
                    version=rec.version,
                    ref=f"{rec.namespace}/{rec.name}@{rec.version}",
                    uri=rec.uri,
                    lifecycle=rec.lifecycle.value,
                    channel=rec.channel,
                    trust=rec.trust.value,
                    summary=rec.summary,
                    description=rec.description,
                    digest=rec.digest,
                    created_at=rec.created_at,
                    revision=rec.revision,
                    badges=badges(rec, bool(sigs) and any(s.verified for s in sigs)),
                    capabilities=list(m.capabilities),
                    runtimes=sorted(set(m.runtime.supported)),
                    tags=list(m.metadata.tags),
                    tools=[
                        DocTool(
                            name=t.name,
                            description=t.description,
                            input_schema=_pretty(t.input_schema),
                            output_schema=_pretty(t.output_schema),
                        )
                        for t in m.tools
                    ],
                    inputs=_pretty(m.inputs.json_schema) if m.inputs else None,
                    outputs=_pretty(m.outputs.json_schema) if m.outputs else None,
                    permissions=m.permissions.flatten(),
                    dependencies=deps,
                    compatibility=m.compatibility.flatten(),
                    license=rec.license.expression,
                    license_approval=rec.license.approval,
                    provenance=_provenance(rec, sigs),
                    quality=rec.quality.model_dump(mode="json", exclude_none=True),
                    security=rec.security.model_dump(mode="json", exclude_none=True),
                    owners=owners,
                    maintainers=maintainers,
                    files=sorted(
                        (p, len(b)) for p, b in files.items() if p != "ananke.registry.json"
                    ),
                    test_files=sorted(p for p in files if p.startswith("tests/")),
                    readme=_readme(files),
                    instructions_ref=m.instructions.ref if m.instructions else None,
                    model_requirements=dict(m.model_requirements),
                    skills=[f"{s.ref}@{s.version}" for s in m.skills],
                    policies=list(m.policies),
                    eval_suite=m.evaluation.suite if m.evaluation else None,
                    lifecycle_message=rec.lifecycle_message,
                    replacement=rec.replacement,
                    changes_from_previous=change,
                )
            )
            prev = rec

        active = [r for r in recs if r.lifecycle not in _INACTIVE]
        approved = _highest([r for r in active if r.trust is TrustStatus.APPROVED])
        stable = _highest([r for r in active if r.channel == "stable"])
        latest = _highest(active) or recs[-1]
        default = approved or stable or latest
        uri = artifact_uri(*key)
        for r in recs:
            for cap in r.manifest.capabilities:
                caps_index.setdefault(cap, set()).add(uri)
        model.artifacts.append(
            DocArtifact(
                kind=key[0],
                namespace=key[1],
                name=key[2],
                uri=uri,
                default_version=default.version,
                latest_approved=approved.version if approved else None,
                latest_stable=stable.version if stable else None,
                latest=latest.version,
                versions=doc_versions,
                used_by=sorted(used_by.get(uri, set()) - {f"{key[1]}/{key[2]}"}),
            )
        )

    # related artifacts (shared capabilities)
    caps_of = {a.uri: set(a.version(a.default_version).capabilities) for a in model.artifacts}
    for art in model.artifacts:
        scored = sorted(
            (-len(caps_of[art.uri] & caps_of[o.uri]), o.uri)
            for o in model.artifacts
            if o.uri != art.uri and caps_of[art.uri] & caps_of[o.uri]
        )
        art.related = [uri for _, uri in scored[:5]]

    model.counts = {
        **{k: v for k, v in sorted(kind_counts.items())},
        "versions": len(all_records),
        "artifacts": len(model.artifacts),
        "active": life.get("active", 0),
        "deprecated": life.get("deprecated", 0),
        "yanked": life.get("yanked", 0),
        "quarantined": life.get("quarantined", 0),
    }
    model.lifecycle_counts = dict(sorted(life.items()))
    model.trust_breakdown = dict(
        sorted(trust.items(), key=lambda kv: (-TRUST_RANK[TrustStatus(kv[0])], kv[0]))
    )
    model.license_breakdown = dict(sorted(lic.items()))
    model.kinds_present = sorted(kind_counts)
    model.capabilities = {c: sorted(u) for c, u in sorted(caps_index.items())}

    frameworks: dict[str, set[str]] = {}
    publishers: dict[str, set[str]] = {}
    for rec in all_records:
        for fw in _framework_of(rec):
            frameworks.setdefault(fw, set()).add(rec.uri)
        who = rec.provenance.publisher.name if rec.provenance.publisher else "unknown"
        publishers.setdefault(who, set()).add(rec.uri)
    model.frameworks = {k: sorted(v) for k, v in sorted(frameworks.items())}
    model.publishers = {k: sorted(v) for k, v in sorted(publishers.items())}

    # capability matrix over each artifact's default version
    freq: Counter[str] = Counter()
    for art in model.artifacts:
        freq.update(art.version(art.default_version).capabilities)
    columns = [c for c, _ in freq.most_common(20)]
    for extra in _FIXED_MATRIX_COLUMNS:
        if extra not in columns:
            columns.append(extra)
    model.capability_columns = sorted(columns)
    for art in model.artifacts:
        default_rec = store.get_record(art.kind, art.namespace, art.name, art.default_version)
        implied = default_rec.manifest.permissions.implied_capabilities() if default_rec else set()
        dv = art.version(art.default_version)
        have = set(dv.capabilities) | implied
        if default_rec and default_rec.manifest.permissions.network:
            have.add("network")
        model.capability_matrix.append(
            MatrixRow(
                uri=art.uri,
                title=art.title,
                kind=art.kind,
                version=art.default_version,
                cells=[c in have for c in model.capability_columns],
            )
        )

    newest = sorted(all_records, key=lambda r: (r.created_at, r.uri, r.version), reverse=True)[:15]
    model.latest_changes = [(r.uri, r.version, r.created_at) for r in newest]

    for rec in all_records:
        model.search_index.append(
            {
                "uri": rec.version_uri,
                "kind": rec.kind.value,
                "name": f"{rec.namespace}/{rec.name}",
                "version": rec.version,
                "summary": rec.summary,
                "tags": list(rec.manifest.metadata.tags),
                "capabilities": list(rec.manifest.capabilities),
                "runtime": sorted(set(rec.manifest.runtime.supported)),
                "trust": rec.trust.value,
                "license": rec.license.expression or "",
                "lifecycle": rec.lifecycle.value,
                "channel": rec.channel,
                "page": f"{rec.kind.plural}/{rec.namespace}/{rec.name}/{rec.version.replace('+', '_')}.html",
            }
        )
    model.search_index.sort(key=lambda e: (e["name"], e["version"]))
    return model


__all__ = [
    "ArtifactKind",
    "DocArtifact",
    "DocChange",
    "DocComparison",
    "DocDependency",
    "DocTool",
    "DocVersion",
    "MatrixRow",
    "RegistryDocumentationModel",
    "build_documentation_model",
]
