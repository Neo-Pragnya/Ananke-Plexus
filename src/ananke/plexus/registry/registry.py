"""The Registry service facade (spec §62, §65, §66).

Owns registration, immutability enforcement, lifecycle/trust/channel metadata, aliases and
the audit trail. Resolution, search, docs, import/export etc. live in sibling modules and
are exposed here as thin delegating methods.
"""

from __future__ import annotations

import base64
import getpass
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from ananke.plexus.events.bus import EventBus
from ananke.plexus.registry.cas import ContentStore
from ananke.plexus.registry.diff import (
    Bump,
    VersionDiff,
    apply_bump,
    diff_manifests,
)
from ananke.plexus.registry.errors import (
    InvalidIdentityError,
    InvalidRequirementError,
    ManifestError,
    NotFoundError,
    PolicyViolationError,
    RegistryError,
    SecretDetectedError,
    VersionContentConflictError,
)
from ananke.plexus.registry.events import publish
from ananke.plexus.registry.hashing import canonical_json, digest_pair
from ananke.plexus.registry.importers.base import ImportedArtifact
from ananke.plexus.registry.jsonschema_util import check_json_schema
from ananke.plexus.registry.models import (
    DEFAULT_CHANNEL,
    KNOWN_CAPABILITIES,
    TRUST_TRANSITIONS,
    ArtifactKind,
    ArtifactManifest,
    ArtifactRef,
    Diagnostic,
    LicenseInfo,
    LifecycleStatus,
    Provenance,
    Quality,
    Security,
    TrustStatus,
    VersionRecord,
    artifact_uri,
    parse_ref,
    validate_channel,
)
from ananke.plexus.registry.payload import (
    MANIFEST_FILE,
    list_payload,
    pack_files,
    safe_relpath,
    unpack_files,
)
from ananke.plexus.registry.policy import (
    RegistryPolicy,
    evaluate_license,
    load_policy,
)
from ananke.plexus.registry.secrets import redact_files, scan_files
from ananke.plexus.registry.semver import VersionReq, normalize_version
from ananke.plexus.registry.signing import (
    SignatureInvalidError,
    SignatureStatus,
    check_signature,
    key_id_for,
    sign_message,
    signing_message,
    validate_key_id,
)
from ananke.plexus.registry.store import ArtifactSummary, RegistryStore, VersionInsert, now_iso

if TYPE_CHECKING:
    from ananke.plexus.registry.views import KindView

DB_NAME = "registry.db"
_ALIAS_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,62}$")
_INITIAL_TRUST = {TrustStatus.UNKNOWN, TrustStatus.DISCOVERED, TrustStatus.RESTRICTED}


class RegisterResult(BaseModel):
    uri: str
    version: str
    digest: str
    created: bool = False
    already_registered: bool = False
    dry_run: bool = False
    diagnostics: list[Diagnostic] = Field(default_factory=list)
    suggested_bump: str | None = None
    previous_version: str | None = None

    @property
    def version_uri(self) -> str:
        return f"{self.uri}@{self.version}"


@dataclass
class _Prepared:
    manifest: ArtifactManifest
    version: str
    files: dict[str, bytes]
    payload: bytes
    sha256: str
    blake3: str | None
    provenance: Provenance
    license: LicenseInfo
    diagnostics: list[Diagnostic]
    schemas: list[tuple[str, str, dict[str, Any]]] = field(default_factory=list)
    previous: VersionRecord | None = None
    suggested: Bump | None = None


def default_actor() -> str:
    try:
        return getpass.getuser()
    except Exception:  # pragma: no cover - environments without a passwd entry
        return "local"


class Registry:
    def __init__(
        self,
        root: Path,
        *,
        policy: RegistryPolicy | None = None,
        bus: EventBus | None = None,
        actor: str | None = None,
        clock: Callable[[], str] | None = None,
        project_root: Path | None = None,
    ) -> None:
        self.root = root
        self.project_root = project_root
        self.store = RegistryStore(root / DB_NAME)
        self.cas = ContentStore(root)
        self._policy = policy
        self.bus = bus
        self.actor = actor or default_actor()
        self._clock = clock or now_iso

    # ------------------------------------------------------------------ construction
    @classmethod
    def open(cls, root: Path | str, *, create: bool = False, **kwargs: Any) -> Registry:
        reg = cls(Path(root), **kwargs)
        reg.store.open(create=create)
        if create:
            reg.cas.ensure()
        return reg

    @classmethod
    def for_project(
        cls, project_root: Path | str, *, create: bool = False, **kwargs: Any
    ) -> Registry:
        project = Path(project_root).resolve()
        root = project / ".ananke" / "registry"
        bus = kwargs.pop("bus", None)
        if bus is None:
            bus = EventBus(audit_log=root / "events.jsonl") if create or root.exists() else None
        return cls.open(root, create=create, project_root=project, bus=bus, **kwargs)

    @classmethod
    def for_user(cls, *, create: bool = False, **kwargs: Any) -> Registry:
        return cls.open(Path.home() / ".ananke" / "registry", create=create, **kwargs)

    def init(self) -> Path:
        self.store.open(create=True)
        self.cas.ensure()
        for sub in ("materialized", "docs", "index", "locks"):
            (self.root / sub).mkdir(parents=True, exist_ok=True)
        return self.root

    def close(self) -> None:
        self.store.close()

    def __enter__(self) -> Registry:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @property
    def policy(self) -> RegistryPolicy:
        if self._policy is None:
            self._policy = load_policy(self.root)
        return self._policy

    @policy.setter
    def policy(self, value: RegistryPolicy) -> None:
        self._policy = value

    @property
    def registry_id(self) -> str:
        return self.store.get_meta("registry_id") or ""

    @property
    def skills(self) -> KindView:
        from ananke.plexus.registry.views import KindView

        return KindView(self, ArtifactKind.SKILL)

    @property
    def agents(self) -> KindView:
        from ananke.plexus.registry.views import KindView

        return KindView(self, ArtifactKind.AGENT)

    def kind_view(self, kind: ArtifactKind) -> KindView:
        from ananke.plexus.registry.views import KindView

        return KindView(self, kind)

    # ------------------------------------------------------------------ events
    def now(self) -> str:
        return self._clock()

    def emit(self, event_type: str, uri: str | None, payload: dict[str, Any]) -> None:
        self._emit(event_type, uri, payload)

    def _emit(self, event_type: str, uri: str | None, payload: dict[str, Any]) -> None:
        self.store.append_event(event_type, uri, self.actor, payload, created_at=self._clock())
        publish(self.bus, event_type, {"uri": uri, **payload}, actor=self.actor)

    def usage(self, event: str, record: VersionRecord, project: str | None = None) -> None:
        if self.policy.telemetry.local_usage:
            self.store.record_usage(event, record.uri, record.version, project, self._clock())

    # ------------------------------------------------------------------ identity lookup
    def locate(
        self, ref: str | ArtifactRef, kind: ArtifactKind | str | None = None
    ) -> tuple[ArtifactKind, str, str]:
        """Resolve a reference (URI, ``ns/name`` or alias/bare name) to an artifact identity."""
        r = parse_ref(ref, kind) if isinstance(ref, str) else ref
        want = ArtifactKind(kind) if kind else r.kind
        if r.namespace is None:
            alias = self.store.get_alias(r.name)
            if alias is not None:
                a_kind, a_ns, a_name = alias
                if want is not None and a_kind != want:
                    raise NotFoundError(f"alias {r.name!r} is a {a_kind.value}, not a {want.value}")
                return a_kind, a_ns, a_name
            matches = [
                a for a in self.store.list_artifacts(want) if a.name == r.name and a.version_count
            ]
            if not matches:
                raise NotFoundError(f"no artifact named {r.name!r}")
            if len(matches) > 1:
                names = ", ".join(sorted(f"{m.namespace}/{m.name}" for m in matches))
                raise InvalidIdentityError(
                    f"{r.name!r} is ambiguous ({names}); qualify the namespace"
                )
            m = matches[0]
            return m.kind, m.namespace, m.name
        if want is not None:
            if self.store.artifact_id(want, r.namespace, r.name) is None:
                raise NotFoundError(f"{artifact_uri(want, r.namespace, r.name)} not found")
            return want, r.namespace, r.name
        found = [
            k for k in ArtifactKind if self.store.artifact_id(k, r.namespace, r.name) is not None
        ]
        if not found:
            raise NotFoundError(f"no artifact {r.namespace}/{r.name}")
        if len(found) > 1:
            raise InvalidIdentityError(
                f"{r.namespace}/{r.name} exists as multiple kinds "
                f"({', '.join(k.value for k in found)}); pass --kind"
            )
        return found[0], r.namespace, r.name

    def exact_version(self, ref: str, kind: ArtifactKind | str | None = None) -> VersionRecord:
        """Fetch one exact registered version (``ns/name@1.2.3``)."""
        r = parse_ref(ref, kind)
        if not r.requirement:
            raise InvalidRequirementError(f"{ref!r} needs an exact version (…@1.2.3)")
        req = VersionReq.parse(r.requirement)
        k, ns, name = self.locate(r, kind)
        if req.exact_version is not None:
            for rec in self.store.versions_of(k, ns, name):
                if rec.semver.same_precedence(req.exact_version) and (
                    not req.exact_version.build or rec.semver.build == req.exact_version.build
                ):
                    return rec
            raise NotFoundError(f"{artifact_uri(k, ns, name)}@{r.requirement} not found")
        raise InvalidRequirementError(f"{ref!r} must pin an exact version")

    def select_versions(
        self, ref: str, kind: ArtifactKind | str | None = None, *, require_selector: bool = False
    ) -> list[VersionRecord]:
        """Versions matched by ``ref``: exact, range, or all when no selector is given."""
        r = parse_ref(ref, kind)
        if require_selector and not r.requirement:
            raise InvalidRequirementError(f"{ref!r} must include a version or range (…@1.2.3)")
        k, ns, name = self.locate(r, kind)
        recs = self.store.versions_of(k, ns, name)
        if r.requirement:
            req = VersionReq.parse(r.requirement)
            recs = [x for x in recs if req.matches(x.semver)]
        if not recs:
            raise NotFoundError(f"no versions match {ref!r}")
        return recs

    def versions(self, ref: str, kind: ArtifactKind | str | None = None) -> list[VersionRecord]:
        k, ns, name = self.locate(ref, kind)
        return self.store.versions_of(k, ns, name)

    def list_artifacts(
        self, kind: ArtifactKind | str | None = None, namespace: str | None = None
    ) -> list[ArtifactSummary]:
        return self.store.list_artifacts(kind, namespace)

    def list_versions(
        self, kind: ArtifactKind | str | None = None, namespace: str | None = None
    ) -> list[VersionRecord]:
        return self.store.list_records(kind, namespace)

    # ------------------------------------------------------------------ payload access
    def payload(self, record: VersionRecord) -> bytes:
        return self.cas.get(record.digest_sha256)

    def files(self, record: VersionRecord) -> dict[str, bytes]:
        return unpack_files(self.payload(record))

    def read_file(self, record: VersionRecord, path: str) -> bytes:
        files = self.files(record)
        rel = safe_relpath(path)
        if rel not in files:
            raise NotFoundError(f"{record.version_uri} has no file {path!r}")
        return files[rel]

    # ------------------------------------------------------------------ registration
    def _check_cycle(self, manifest: ArtifactManifest) -> None:
        target = manifest.uri
        stack = [d.id for d in manifest.artifact_dependencies() if d.id]
        seen: set[str] = set()
        while stack:
            dep = stack.pop()
            if dep == target:
                raise PolicyViolationError(
                    f"dependency cycle: {target} (transitively) depends on itself",
                    code="DEPENDENCY_CYCLE",
                )
            if dep in seen:
                continue
            seen.add(dep)
            ref = parse_ref(dep)
            if ref.kind is None or ref.namespace is None:
                continue
            for rec in self.store.versions_of(ref.kind, ref.namespace, ref.name):
                stack.extend(d.id for d in rec.manifest.artifact_dependencies() if d.id)

    def _build_payload(
        self, manifest: ArtifactManifest, files: dict[str, bytes]
    ) -> tuple[dict[str, bytes], bytes]:
        all_files = dict(files)
        all_files[MANIFEST_FILE] = canonical_json(manifest.canonical_dict()) + b"\n"
        return all_files, pack_files(all_files)

    def _collect_schemas(self, m: ArtifactManifest) -> list[tuple[str, str, dict[str, Any]]]:
        out: list[tuple[str, str, dict[str, Any]]] = []
        for role, contract in (("input", m.inputs), ("output", m.outputs)):
            if contract and contract.json_schema is not None:
                out.append((role, contract.format, contract.json_schema))
        for tool in m.tools:
            if tool.input_schema is not None:
                out.append((f"tool:{tool.name}:input", "json-schema", tool.input_schema))
            if tool.output_schema is not None:
                out.append((f"tool:{tool.name}:output", "json-schema", tool.output_schema))
        return out

    def _validate(
        self,
        m: ArtifactManifest,
        files: dict[str, bytes],
        prov: Provenance,
        diags: list[Diagnostic],
    ) -> LicenseInfo:
        policy = self.policy
        errors = [d for d in diags if d.level == "error"]
        if errors:
            raise ManifestError("; ".join(f"{d.code}: {d.message}" for d in errors))
        for _role, _fmt, schema in self._collect_schemas(m):
            err = check_json_schema(schema)
            if err:
                raise ManifestError(f"invalid JSON Schema: {err}")
        for cap in m.capabilities:
            if cap not in KNOWN_CAPABILITIES:
                diags.append(
                    Diagnostic(
                        level="info",
                        code="custom-capability",
                        message=f"custom capability {cap}",
                        field="capabilities",
                    )
                )
        implied = m.permissions.implied_capabilities()
        for cap in m.capabilities:
            if cap in {"filesystem.write", "shell.execute", "network.http"} and cap not in implied:
                diags.append(
                    Diagnostic(
                        level="warning",
                        code="capability-without-permission",
                        message=f"declares {cap} but no matching permission is granted",
                        field="permissions",
                    )
                )
        forbidden = set(policy.permissions.forbid)
        clash = forbidden & (implied | set(m.capabilities))
        if clash:
            raise PolicyViolationError(
                f"forbidden capability/permission: {', '.join(sorted(clash))}"
            )
        ns_policy = policy.namespaces.get(m.namespace)
        if ns_policy is not None and ns_policy.publishers:
            who = {self.actor}
            if prov.publisher:
                who.add(prov.publisher.name)
            if not who & set(ns_policy.publishers):
                raise PolicyViolationError(
                    f"namespace {m.namespace!r} is protected; allowed publishers: "
                    f"{', '.join(ns_policy.publishers)}",
                    code="NAMESPACE_PROTECTED",
                )
        approval, reason = evaluate_license(m.license.expression, policy.licenses)
        if approval == "denied":
            raise PolicyViolationError(reason, code="LICENSE_DENIED")
        if approval == "unknown" and not policy.licenses.allow_unknown:
            raise PolicyViolationError(
                "license metadata required by policy (licenses.allow_unknown = false)",
                code="LICENSE_REQUIRED",
            )
        lic = m.license.model_copy(update={"approval": approval})
        for path in (m.instructions.ref,) if m.instructions and m.instructions.ref else ():
            safe_relpath(path)
        self._check_cycle(m)
        return lic

    def _prepare(self, imported: ImportedArtifact, version: str | None) -> _Prepared:
        manifest = imported.manifest.model_copy(deep=True)
        prov = imported.provenance.model_copy(deep=True)
        diags = list(imported.diagnostics)
        policy = self.policy
        files = dict(imported.files)

        native = manifest.version if version in (None, "", "auto") else str(version)
        try:
            norm = normalize_version(native, strict=policy.strict_semver)
        except RegistryError:
            raise
        if str(norm) != native:
            prov.native_version = prov.native_version or native
            diags.append(
                Diagnostic(
                    level="info",
                    code="normalized-version",
                    message=f"version {native!r} normalized to {norm}",
                    field="version",
                )
            )
        manifest.version = str(norm)

        secrets_action = policy.secrets.mode
        scan_target = dict(files)
        scan_target[MANIFEST_FILE] = canonical_json(manifest.canonical_dict())
        findings = scan_files(scan_target)
        if findings:
            summary = ", ".join(f"{f.path}:{f.line} ({f.rule})" for f in findings[:5])
            if secrets_action == "reject":
                raise SecretDetectedError(
                    f"{len(findings)} potential secret(s) found; refusing to register: {summary}"
                )
            if secrets_action == "redact":
                files, count = redact_files(files)
                diags.append(
                    Diagnostic(
                        level="warning",
                        code="secrets-redacted",
                        message=f"redacted {count} secret(s) before hashing: {summary}",
                    )
                )
            else:
                diags.append(
                    Diagnostic(
                        level="warning",
                        code="secrets-detected",
                        message=f"potential secrets: {summary}",
                    )
                )

        lic = self._validate(manifest, files, prov, diags)
        manifest.license = lic

        previous = None
        suggested: Bump | None = None
        existing = self.store.versions_of(manifest.kind, manifest.namespace, manifest.name)
        if existing:
            previous = existing[-1]
        if version == "auto" and previous is not None:
            manifest.version = previous.version
            all_files, _ = self._build_payload(manifest, files)
            old_files = self.files(previous)
            d = diff_manifests(
                previous.manifest,
                manifest,
                old_files=old_files,
                new_files=all_files,
                with_text=False,
            )
            suggested = d.suggested_bump
            if suggested == "NONE":
                manifest.version = previous.version
            else:
                manifest.version = apply_bump(previous.version, suggested)
                diags.append(
                    Diagnostic(
                        level="info",
                        code="auto-version",
                        message=f"auto version: {suggested} bump {previous.version} -> {manifest.version}",
                        field="version",
                    )
                )
        all_files, payload = self._build_payload(manifest, files)
        sha, b3 = digest_pair(payload)
        if prov.discovered_at is None:
            prov.discovered_at = self._clock()
        return _Prepared(
            manifest=manifest,
            version=manifest.version,
            files=all_files,
            payload=payload,
            sha256=sha,
            blake3=b3,
            provenance=prov,
            license=lic,
            diagnostics=diags,
            schemas=self._collect_schemas(manifest),
            previous=previous,
            suggested=suggested,
        )

    def validate(
        self,
        manifest: ArtifactManifest,
        files: dict[str, bytes],
        provenance: Provenance,
        diagnostics: list[Diagnostic] | None = None,
    ) -> LicenseInfo:
        """Run registration-time policy/validation checks without registering (used by import)."""
        return self._validate(
            manifest, files, provenance, diagnostics if diagnostics is not None else []
        )

    def collect_schemas(self, manifest: ArtifactManifest) -> list[tuple[str, str, dict[str, Any]]]:
        return self._collect_schemas(manifest)

    def prepare(self, imported: ImportedArtifact, version: str | None = None) -> _Prepared:
        """Validate and pack without writing anything (used by sync/publish previews)."""
        return self._prepare(imported, version)

    def preview_record(
        self, imported: ImportedArtifact, *, channel: str = "dev", source_path: str | None = None
    ) -> VersionRecord:
        """An in-memory, *unregistered* record (path overrides / dev links)."""
        prep = self._prepare(imported, None)
        prov = prep.provenance.model_copy(update={"source_url": source_path} if source_path else {})
        return VersionRecord(
            uri=prep.manifest.uri,
            kind=prep.manifest.kind,
            namespace=prep.manifest.namespace,
            name=prep.manifest.name,
            version=prep.version,
            lifecycle=LifecycleStatus.ACTIVE,
            channel=channel,
            trust=TrustStatus.DISCOVERED,
            summary=prep.manifest.summary,
            description=prep.manifest.description,
            digest_sha256=prep.sha256,
            digest_blake3=prep.blake3,
            payload_size=len(prep.payload),
            created_at=self._clock(),
            manifest=prep.manifest,
            provenance=prov,
            license=prep.license,
        )

    def register(
        self,
        imported: ImportedArtifact,
        *,
        version: str | None = None,
        channel: str | None = None,
        trust: TrustStatus | None = None,
        dry_run: bool = False,
    ) -> RegisterResult:
        """Register an immutable version. Same content is idempotent; different content
        under an existing version raises ``VERSION_CONTENT_CONFLICT`` (spec §18)."""
        prep = self._prepare(imported, version)
        m = prep.manifest
        chan = validate_channel(channel or DEFAULT_CHANNEL)
        initial_trust = trust or TrustStatus.DISCOVERED
        if initial_trust not in _INITIAL_TRUST:
            raise PolicyViolationError(
                f"cannot register directly as {initial_trust.value}; register then promote "
                "(promotion runs the quality gate)"
            )
        existing = self.store.get_record(m.kind, m.namespace, m.name, prep.version)
        if existing is not None:
            if existing.digest_sha256 == prep.sha256:
                return RegisterResult(
                    uri=m.uri,
                    version=prep.version,
                    digest=f"sha256:{prep.sha256}",
                    already_registered=True,
                    diagnostics=prep.diagnostics,
                    dry_run=dry_run,
                )
            d = diff_manifests(existing.manifest, m, with_text=False)
            hint = (
                f"contents changed ({', '.join(d.change_classes) or 'payload'}); "
                f"publish as a new version, suggested bump: {d.suggested_bump if d.suggested_bump != 'NONE' else 'PATCH'}"
            )
            raise VersionContentConflictError(
                f"{m.uri}@{prep.version} is already registered with different content — {hint}"
            )
        result = RegisterResult(
            uri=m.uri,
            version=prep.version,
            digest=f"sha256:{prep.sha256}",
            created=not dry_run,
            dry_run=dry_run,
            diagnostics=prep.diagnostics,
            suggested_bump=prep.suggested,
            previous_version=prep.previous.version if prep.previous else None,
        )
        if dry_run:
            result.created = False
            return result
        claimed = prep.provenance.signature
        attached: SignatureStatus | None = None
        if claimed is not None and claimed.value and claimed.key_id:
            attached = check_signature(
                self.policy.signing,
                version_uri=f"{m.uri}@{prep.version}",
                sha256_hex=prep.sha256,
                key_id=claimed.key_id,
                signature_b64=claimed.value,
                signer=claimed.signer,
                algorithm=claimed.algorithm,
            )
            if attached.trusted and attached.valid is False:
                raise SignatureInvalidError(
                    f"attached signature by trusted key {claimed.key_id!r} does not verify "
                    f"for {m.uri}@{prep.version}"
                )
        self.cas.put(prep.payload)
        with self.store.transaction():
            new_vid = self.store.insert_version(
                VersionInsert(
                    manifest=m,
                    version=prep.version,
                    digest_sha256=prep.sha256,
                    digest_blake3=prep.blake3,
                    payload_size=len(prep.payload),
                    provenance=prep.provenance,
                    license=prep.license,
                    files=list_payload(prep.payload),
                    schemas=prep.schemas,
                    channel=chan,
                    trust=initial_trust,
                    quality=m.quality,
                    created_at=self._clock(),
                    actor=self.actor,
                )
            )
            if claimed is not None and attached is not None and claimed.value and claimed.key_id:
                self.store.add_signature(
                    new_vid,
                    key_id=validate_key_id(claimed.key_id),
                    algorithm=claimed.algorithm,
                    signature=claimed.value,
                    signer=claimed.signer,
                    created_at=self._clock(),
                )
            self._emit(
                "artifact.registered",
                m.uri,
                {"version": prep.version, "digest": f"sha256:{prep.sha256}", "channel": chan},
            )
        return result

    # ------------------------------------------------------------------ metadata / lifecycle
    def _change(
        self,
        rec: VersionRecord,
        changes: dict[str, Any],
        event: str,
        reason: str | None,
        extra: dict[str, Any] | None = None,
    ) -> VersionRecord:
        vid = self.store.version_id(rec.kind, rec.namespace, rec.name, rec.version)
        with self.store.transaction():
            self.store.update_metadata(
                vid, changes, actor=self.actor, reason=reason, created_at=self._clock()
            )
            self._emit(
                event,
                rec.uri,
                {"version": rec.version, "reason": reason, **(extra or {})},
            )
        return self.store.record_by_id(vid)

    def yank(
        self, ref: str, reason: str | None = None, kind: ArtifactKind | str | None = None
    ) -> list[VersionRecord]:
        """Yanked versions stay resolvable from lockfiles but are avoided by new resolution."""
        out = []
        for rec in self.select_versions(ref, kind, require_selector=True):
            if rec.lifecycle is LifecycleStatus.QUARANTINED:
                raise PolicyViolationError(f"{rec.version_uri} is quarantined; cannot yank")
            out.append(
                self._change(
                    rec,
                    {"lifecycle": LifecycleStatus.YANKED, "lifecycle_message": reason},
                    "artifact.yanked",
                    reason,
                )
            )
        return out

    def unyank(self, ref: str, kind: ArtifactKind | str | None = None) -> list[VersionRecord]:
        out = []
        for rec in self.select_versions(ref, kind, require_selector=True):
            if rec.lifecycle is not LifecycleStatus.YANKED:
                continue
            out.append(
                self._change(
                    rec,
                    {"lifecycle": LifecycleStatus.ACTIVE, "lifecycle_message": None},
                    "artifact.unyanked",
                    None,
                )
            )
        return out

    def deprecate(
        self,
        ref: str,
        message: str | None = None,
        replacement: str | None = None,
        kind: ArtifactKind | str | None = None,
    ) -> list[VersionRecord]:
        if replacement:
            parse_ref(replacement)
        out = []
        for rec in self.select_versions(ref, kind):
            if rec.lifecycle in {LifecycleStatus.QUARANTINED, LifecycleStatus.ARCHIVED}:
                continue
            out.append(
                self._change(
                    rec,
                    {
                        "lifecycle": LifecycleStatus.DEPRECATED,
                        "lifecycle_message": message,
                        "replacement": replacement,
                        "channel": "deprecated",
                    },
                    "artifact.deprecated",
                    message,
                    {"replacement": replacement},
                )
            )
        return out

    def quarantine(
        self, ref: str, reason: str | None = None, kind: ArtifactKind | str | None = None
    ) -> list[VersionRecord]:
        """Strongest state: never selected, materialization refused (spec §104)."""
        out = []
        for rec in self.select_versions(ref, kind, require_selector=True):
            out.append(
                self._change(
                    rec,
                    {
                        "lifecycle": LifecycleStatus.QUARANTINED,
                        "lifecycle_message": reason,
                        "trust": TrustStatus.QUARANTINED,
                        "channel": "quarantined",
                        "evidence": {"reason": reason},
                    },
                    "artifact.quarantined",
                    reason,
                )
            )
            self._emit("trust.quarantined", rec.uri, {"version": rec.version, "reason": reason})
        return out

    def release_quarantine(
        self, ref: str, reason: str, kind: ArtifactKind | str | None = None
    ) -> list[VersionRecord]:
        """Leave quarantine (explicit, audited). The version returns as *restricted*."""
        out = []
        for rec in self.select_versions(ref, kind, require_selector=True):
            if rec.lifecycle is not LifecycleStatus.QUARANTINED:
                continue
            out.append(
                self._change(
                    rec,
                    {
                        "lifecycle": LifecycleStatus.ACTIVE,
                        "lifecycle_message": None,
                        "trust": TrustStatus.RESTRICTED,
                        "channel": "candidate",
                        "evidence": {"released": reason},
                    },
                    "artifact.metadata",
                    reason,
                )
            )
        return out

    def archive(
        self, ref: str, reason: str | None = None, kind: ArtifactKind | str | None = None
    ) -> list[VersionRecord]:
        out = []
        for rec in self.select_versions(ref, kind, require_selector=True):
            out.append(
                self._change(
                    rec,
                    {"lifecycle": LifecycleStatus.ARCHIVED, "lifecycle_message": reason},
                    "artifact.archived",
                    reason,
                )
            )
        return out

    def set_channel(self, ref: str, channel: str, reason: str | None = None) -> VersionRecord:
        rec = self.exact_version(ref)
        validate_channel(channel)
        if rec.lifecycle is LifecycleStatus.QUARANTINED:
            raise PolicyViolationError("quarantined versions cannot change channel")
        updated = self._change(
            rec, {"channel": channel}, "channel.changed", reason, {"channel": channel}
        )
        return updated

    def set_trust(
        self,
        ref: str,
        status: TrustStatus,
        *,
        reason: str | None = None,
        evidence: dict[str, Any] | None = None,
        force: bool = False,
    ) -> VersionRecord:
        """Low-level trust transition following spec §90. Use :meth:`promote` for gated moves."""
        status = TrustStatus(status)
        rec = self.exact_version(ref)
        if status is rec.trust:
            return rec
        if not force and status not in TRUST_TRANSITIONS[rec.trust]:
            raise PolicyViolationError(
                f"trust transition {rec.trust.value} -> {status.value} is not allowed",
                code="INVALID_TRUST_TRANSITION",
            )
        changes: dict[str, Any] = {"trust": status, "evidence": evidence}
        if status is TrustStatus.QUARANTINED:
            changes.update({"lifecycle": LifecycleStatus.QUARANTINED, "channel": "quarantined"})
        updated = self._change(
            rec, changes, f"trust.{status.value}", reason, {"trust": status.value}
        )
        return updated

    def update_quality(
        self, ref: str, quality: Quality, reason: str | None = None
    ) -> VersionRecord:
        return self._change(
            self.exact_version(ref), {"quality": quality}, "artifact.metadata", reason
        )

    def update_security(
        self, ref: str, security: Security, reason: str | None = None
    ) -> VersionRecord:
        rec = self.exact_version(ref)
        rec = self._change(rec, {"security": security}, "artifact.metadata", reason)
        gated = "vulnerability_scan" in self.policy.approval.require
        if (
            security.critical_count > 0
            and gated
            and rec.lifecycle is not LifecycleStatus.QUARANTINED
        ):
            self.quarantine(rec.version_uri, "critical vulnerability reported")
            rec = self.exact_version(ref)
        return rec

    # ------------------------------------------------------------------ signatures
    def _signature_status(self, rec: VersionRecord, row: dict[str, Any]) -> SignatureStatus:
        return check_signature(
            self.policy.signing,
            version_uri=rec.version_uri,
            sha256_hex=rec.digest_sha256,
            key_id=row["key_id"],
            signature_b64=row["signature"],
            signer=row.get("signer"),
            created_at=row.get("created_at"),
            algorithm=row["algorithm"],
        )

    def signatures(self, ref: str | VersionRecord) -> list[SignatureStatus]:
        """Every stored signature with its *computed* status against current policy."""
        rec = ref if isinstance(ref, VersionRecord) else self.exact_version(ref)
        try:
            vid = self.store.version_id(rec.kind, rec.namespace, rec.name, rec.version)
        except NotFoundError:  # unregistered (path override / dev link): nothing to verify
            return []
        return [self._signature_status(rec, row) for row in self.store.signatures_of(vid)]

    def is_signed(self, rec: VersionRecord) -> bool:
        """True iff some signature is valid and from a trusted, unrevoked key."""
        return any(s.verified for s in self.signatures(rec))

    def add_signature(
        self,
        ref: str,
        *,
        key_id: str,
        signature: str,
        signer: str | None = None,
        algorithm: str = "ed25519",
    ) -> SignatureStatus:
        """Attach an externally produced signature (e.g. from CI). Signatures made by a key the
        policy trusts must be valid; signatures from unknown keys are kept but not trusted."""
        rec = self.exact_version(ref)
        validate_key_id(key_id)
        row = {
            "key_id": key_id,
            "signature": signature,
            "signer": signer,
            "algorithm": algorithm,
            "created_at": self._clock(),
        }
        status = self._signature_status(rec, row)
        if status.trusted and status.valid is False:
            raise SignatureInvalidError(
                f"signature by trusted key {key_id!r} does not verify for {rec.version_uri}"
            )
        vid = self.store.version_id(rec.kind, rec.namespace, rec.name, rec.version)
        with self.store.transaction():
            self.store.add_signature(
                vid,
                key_id=key_id,
                algorithm=algorithm,
                signature=signature,
                signer=signer,
                created_at=self._clock(),
            )
            self._emit(
                "artifact.signed",
                rec.uri,
                {"version": rec.version, "key_id": key_id, "verified": status.verified},
            )
        return status

    def sign(
        self,
        ref: str,
        private_key: Path,
        *,
        key_id: str | None = None,
        signer: str | None = None,
    ) -> SignatureStatus:
        """Sign a registered version with a local Ed25519 private key file (never stored)."""
        rec = self.exact_version(ref)
        signature, public = sign_message(
            private_key, signing_message(rec.version_uri, rec.digest_sha256)
        )
        return self.add_signature(
            ref,
            key_id=key_id or key_id_for(public),
            signature=base64.b64encode(signature).decode("ascii"),
            signer=signer,
        )

    def remove_signature(self, ref: str, key_id: str) -> bool:
        rec = self.exact_version(ref)
        vid = self.store.version_id(rec.kind, rec.namespace, rec.name, rec.version)
        with self.store.transaction():
            removed = self.store.remove_signature(vid, key_id)
            if removed:
                self._emit("artifact.signature_removed", rec.uri, {"key_id": key_id})
        return removed

    def correct_metadata(
        self,
        ref: str,
        *,
        summary: str | None = None,
        description: str | None = None,
        license: LicenseInfo | None = None,
        reason: str | None = None,
    ) -> VersionRecord:
        """Registry revision: fix metadata without touching the immutable payload (spec §19)."""
        rec = self.exact_version(ref)
        changes: dict[str, Any] = {}
        if summary is not None:
            changes["summary"] = summary
        if description is not None:
            changes["description"] = description
        if license is not None:
            approval, _ = evaluate_license(license.expression, self.policy.licenses)
            changes["license"] = license.model_copy(update={"approval": approval})
        if not changes:
            return rec
        return self._change(rec, changes, "artifact.metadata", reason)

    def revisions(self, ref: str) -> list[dict[str, Any]]:
        rec = self.exact_version(ref)
        return self.store.revisions_of(
            self.store.version_id(rec.kind, rec.namespace, rec.name, rec.version)
        )

    def promote(
        self,
        ref: str,
        *,
        channel: str | None = None,
        trust: TrustStatus | None = None,
        reason: str | None = None,
        reviewed_by: str | None = None,
        run_gate: bool = True,
        run_tests: bool | None = None,
    ) -> VersionRecord:
        """Promote channel/trust without changing the version (spec §154)."""
        from ananke.plexus.registry.quality import QualityGate

        trust = TrustStatus(trust) if trust is not None else None
        rec = self.exact_version(ref)
        if trust in {TrustStatus.VERIFIED, TrustStatus.APPROVED} and run_gate:
            report = QualityGate(self).run(
                rec, run_tests=run_tests, target=trust, reviewed_by=reviewed_by
            )
            if not report.passed:
                raise PolicyViolationError(
                    "quality gate failed: "
                    + "; ".join(f"{c.name}: {c.detail}" for c in report.checks if not c.ok),
                    code="QUALITY_GATE_FAILED",
                )
            rec = self.exact_version(ref)
        if trust is not None:
            steps = (
                [TrustStatus.VERIFIED, TrustStatus.APPROVED]
                if trust is TrustStatus.APPROVED
                and rec.trust
                in {TrustStatus.UNKNOWN, TrustStatus.DISCOVERED, TrustStatus.RESTRICTED}
                else [trust]
            )
            for step in steps:
                rec = self.set_trust(
                    rec.version_uri, step, reason=reason, evidence={"reviewed_by": reviewed_by}
                )
        if channel is not None:
            rec = self.set_channel(rec.version_uri, channel, reason)
        return rec

    def unregister(
        self, ref: str, *, purge: bool = False, force: bool = False, reason: str | None = None
    ) -> list[VersionRecord]:
        """Archive a version (default) or, with ``purge``, delete it if nothing references it."""
        recs = self.select_versions(ref, require_selector=True)
        if not purge:
            return self.archive(ref, reason or "unregistered")
        locked = self.store.locked_digests()
        for rec in recs:
            if not force and rec.digest_sha256 in locked:
                raise PolicyViolationError(
                    f"{rec.version_uri} is referenced by a lockfile; use force"
                )
            dependents = [
                d
                for d, _req in self.store.dependents_of(rec.uri)
                if d.version_uri != rec.version_uri
            ]
            if not force and dependents:
                raise PolicyViolationError(
                    f"{rec.version_uri} is depended on by {dependents[0].version_uri}; use force"
                )
        for rec in recs:
            vid = self.store.version_id(rec.kind, rec.namespace, rec.name, rec.version)
            with self.store.transaction():
                self.store.delete_version(vid)
                self._emit("artifact.purged", rec.uri, {"version": rec.version, "reason": reason})
        return recs

    # ------------------------------------------------------------------ aliases
    def alias_set(self, alias: str, ref: str, kind: ArtifactKind | str | None = None) -> None:
        if not _ALIAS_RE.match(alias) or "/" in alias:
            raise InvalidIdentityError(f"invalid alias {alias!r}")
        k, ns, name = self.locate(ref, kind)
        with self.store.transaction():
            self.store.set_alias(alias, k, ns, name, self._clock())
            self._emit("alias.updated", artifact_uri(k, ns, name), {"alias": alias})

    def alias_remove(self, alias: str) -> bool:
        with self.store.transaction():
            removed = self.store.delete_alias(alias)
            if removed:
                self._emit("alias.updated", None, {"alias": alias, "removed": True})
        return removed

    def aliases(self) -> list[dict[str, str]]:
        return self.store.list_aliases()

    # ------------------------------------------------------------------ diff / snapshot
    def diff(
        self, a: str, b: str, kind: ArtifactKind | str | None = None, *, with_text: bool = True
    ) -> VersionDiff:
        ra, rb = self.exact_version(a, kind), self.exact_version(b, kind)
        if ra.key != rb.key:
            raise InvalidIdentityError("can only diff versions of the same artifact")
        return diff_manifests(
            ra.manifest,
            rb.manifest,
            old_files=self.files(ra),
            new_files=self.files(rb),
            with_text=with_text,
        )

    def snapshot_id(self) -> str:
        return self.store.snapshot_id()

    def create_snapshot(self, note: str | None = None) -> str:
        sid = self.snapshot_id()
        self.store.save_snapshot(sid, self.store.count_versions(), note, self._clock())
        return sid

    def dependents(self, record: VersionRecord) -> list[tuple[VersionRecord, str | None]]:
        return self.store.dependents_of(record.uri)

    # ------------------------------------------------------------------ delegating conveniences
    def search(self, query: str = "", **kwargs: Any) -> list[Any]:
        from ananke.plexus.registry.search import search

        return search(self, query, **kwargs)

    def resolve(self, ref: str, **kwargs: Any) -> Any:
        """Resolve ``ref`` under policy; returns the selected :class:`VersionRecord`."""
        from ananke.plexus.registry.resolver import resolve

        return resolve(self, ref, **kwargs)

    def learn(self, source: str, **kwargs: Any) -> Any:
        from ananke.plexus.registry.learn import learn

        return learn(self, source, **kwargs)

    def verify(self, **kwargs: Any) -> Any:
        from ananke.plexus.registry.verify import verify

        return verify(self, **kwargs)

    def doctor(self) -> Any:
        from ananke.plexus.registry.verify import doctor

        return doctor(self)

    def gc(self, **kwargs: Any) -> Any:
        from ananke.plexus.registry.gc import gc

        return gc(self, **kwargs)

    def rebuild_index(self) -> int:
        return self.store.rebuild_index()

    def export_archive(self, out: Path | None = None, **kwargs: Any) -> Any:
        from ananke.plexus.registry.portable import export_registry

        return export_registry(self, out, **kwargs)

    def import_archive(self, archive: Path, **kwargs: Any) -> Any:
        from ananke.plexus.registry.portable import import_registry

        return import_registry(self, archive, **kwargs)

    def build_docs(self, out_dir: Path | None = None, **kwargs: Any) -> Any:
        from ananke.plexus.registry.docsgen.builder import build_site

        return build_site(self, out_dir, **kwargs)

    def dump_docs(self, output: Path | None = None, **kwargs: Any) -> Path:
        from ananke.plexus.registry.docsgen.builder import build_dump

        return build_dump(self, output, **kwargs)

    def report(self, name: str, **kwargs: Any) -> Any:
        from ananke.plexus.registry.reports import report

        return report(self, name, **kwargs)

    def semantic_state(self) -> dict[str, Any]:
        """Database-independent state used for export/import round-trip equality (spec §167)."""
        versions = []
        for rec in self.store.list_records():
            versions.append(
                {
                    "uri": rec.version_uri,
                    "digest": rec.digest,
                    "lifecycle": rec.lifecycle.value,
                    "channel": rec.channel,
                    "trust": rec.trust.value,
                    "revision": rec.revision,
                    "summary": rec.summary,
                    "description": rec.description,
                    "license": rec.license.model_dump(mode="json"),
                    "quality": rec.quality.model_dump(mode="json"),
                    "security": rec.security.model_dump(mode="json"),
                    "manifest": rec.manifest.canonical_dict(),
                    "signatures": sorted(
                        (s["key_id"], s["algorithm"], s["signature"])
                        for s in self.store.signatures_of(
                            self.store.version_id(rec.kind, rec.namespace, rec.name, rec.version)
                        )
                    ),
                }
            )
        return {"versions": versions, "aliases": self.store.list_aliases_semantic()}
