"""Canonical registry models (spec §3, §15-§33).

One generalised :class:`ArtifactManifest` covers every artifact kind; skill- and
agent-specific fields are simply unused by the other kinds.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ananke.plexus.registry.errors import InvalidIdentityError
from ananke.plexus.registry.semver import Version, VersionReq

REGISTRY_SCHEMA_VERSION = 1
URI_SCHEME = "ananke://"


class ArtifactKind(StrEnum):
    SKILL = "skill"
    AGENT = "agent"
    TOOL = "tool"
    WORKFLOW = "workflow"
    EVALUATOR = "evaluator"
    PROMPT = "prompt"
    POLICY = "policy"
    BUNDLE = "bundle"
    RUNTIME_PROFILE = "runtime-profile"

    @property
    def plural(self) -> str:
        return {"policy": "policies"}.get(self.value, self.value + "s")


class LifecycleStatus(StrEnum):
    ACTIVE = "active"
    DEPRECATED = "deprecated"
    YANKED = "yanked"
    QUARANTINED = "quarantined"
    ARCHIVED = "archived"


class TrustStatus(StrEnum):
    UNKNOWN = "unknown"
    DISCOVERED = "discovered"
    VERIFIED = "verified"
    APPROVED = "approved"
    RESTRICTED = "restricted"
    QUARANTINED = "quarantined"


class ResolutionMode(StrEnum):
    LOCKED = "locked"
    HIGHEST_COMPATIBLE = "highest-compatible"
    HIGHEST_APPROVED = "highest-approved"
    LOWEST_COMPATIBLE = "lowest-compatible"
    STABLE_ONLY = "stable-only"
    EXACT = "exact"


class DependencyType(StrEnum):
    ARTIFACT = "artifact"
    PYTHON_PACKAGE = "python-package"
    RUST_CRATE = "rust-crate"
    SYSTEM_BINARY = "system-binary"
    MCP_SERVER = "mcp-server"
    MODEL_CAPABILITY = "model-capability"
    RUNTIME = "runtime"


TRUST_RANK: dict[TrustStatus, int] = {
    TrustStatus.QUARANTINED: -1,
    TrustStatus.UNKNOWN: 0,
    TrustStatus.DISCOVERED: 1,
    TrustStatus.RESTRICTED: 1,
    TrustStatus.VERIFIED: 2,
    TrustStatus.APPROVED: 3,
}

# Spec §90 trust promotion workflow. Quarantine can only be left with force=True.
TRUST_TRANSITIONS: dict[TrustStatus, frozenset[TrustStatus]] = {
    TrustStatus.UNKNOWN: frozenset(
        {TrustStatus.DISCOVERED, TrustStatus.RESTRICTED, TrustStatus.QUARANTINED}
    ),
    TrustStatus.DISCOVERED: frozenset(
        {TrustStatus.VERIFIED, TrustStatus.RESTRICTED, TrustStatus.QUARANTINED}
    ),
    TrustStatus.VERIFIED: frozenset(
        {TrustStatus.APPROVED, TrustStatus.RESTRICTED, TrustStatus.QUARANTINED}
    ),
    TrustStatus.APPROVED: frozenset({TrustStatus.RESTRICTED, TrustStatus.QUARANTINED}),
    TrustStatus.RESTRICTED: frozenset({TrustStatus.VERIFIED, TrustStatus.QUARANTINED}),
    TrustStatus.QUARANTINED: frozenset(),
}

BUILTIN_CHANNELS = ("stable", "candidate", "beta", "canary", "deprecated", "quarantined")
CUSTOM_CHANNELS = ("approved", "restricted", "experimental")
DEFAULT_CHANNEL = "candidate"

# Spec §25 capability taxonomy (open set — unknown IDs only produce an info diagnostic).
KNOWN_CAPABILITIES = (
    "filesystem.read",
    "filesystem.write",
    "shell.execute",
    "network.http",
    "graph.query",
    "graph.write",
    "architecture.read",
    "architecture.validate",
    "spec.read",
    "spec.write",
    "git.commit",
    "git.push",
    "scm.pull_request.create",
    "issue.read",
    "issue.update",
    "eval.run",
    "test.run",
)

_NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{0,62}[a-z0-9])?$")
_CAP_RE = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")
_CHANNEL_RE = re.compile(r"^[a-z][a-z0-9-]{0,31}$")


def validate_name(label: str, value: str) -> str:
    if not _NAME_RE.match(value) or ".." in value:
        raise InvalidIdentityError(
            f"invalid {label} {value!r}: use lowercase letters, digits, '.', '_' or '-' (max 64)"
        )
    return value


def validate_channel(value: str) -> str:
    if not _CHANNEL_RE.match(value):
        raise InvalidIdentityError(f"invalid channel name {value!r}")
    return value


def artifact_uri(kind: ArtifactKind | str, namespace: str, name: str, version: str = "") -> str:
    base = f"{URI_SCHEME}{ArtifactKind(kind).value}/{namespace}/{name}"
    return f"{base}@{version}" if version else base


@dataclass(frozen=True)
class ArtifactRef:
    """A parsed artifact reference: full URI, ``ns/name[@req]``, or a bare alias/name."""

    kind: ArtifactKind | None
    namespace: str | None
    name: str
    requirement: str | None = None

    @property
    def qualified(self) -> bool:
        return self.namespace is not None

    def with_requirement(self, req: str | None) -> ArtifactRef:
        return ArtifactRef(self.kind, self.namespace, self.name, req)

    def __str__(self) -> str:
        ident = f"{self.namespace}/{self.name}" if self.namespace else self.name
        if self.kind is not None:
            ident = f"{URI_SCHEME}{self.kind.value}/{ident}"
        return f"{ident}@{self.requirement}" if self.requirement else ident


def parse_ref(text: str, default_kind: ArtifactKind | str | None = None) -> ArtifactRef:
    raw = text.strip()
    if not raw:
        raise InvalidIdentityError("empty artifact reference")
    kind = ArtifactKind(default_kind) if default_kind else None
    body, requirement = raw, None
    if "@" in raw:
        body, _, requirement = raw.rpartition("@")
        requirement = requirement or None
    if body.startswith(URI_SCHEME):
        parts = body[len(URI_SCHEME) :].split("/")
        if len(parts) != 3:
            raise InvalidIdentityError(f"invalid artifact URI: {text!r}")
        try:
            kind = ArtifactKind(parts[0])
        except ValueError as exc:
            raise InvalidIdentityError(f"unknown artifact kind {parts[0]!r}") from exc
        return ArtifactRef(
            kind,
            validate_name("namespace", parts[1]),
            validate_name("name", parts[2]),
            requirement,
        )
    segments = body.split("/")
    if len(segments) == 1:
        return ArtifactRef(kind, None, validate_name("name", segments[0]), requirement)
    if len(segments) == 2:
        return ArtifactRef(
            kind,
            validate_name("namespace", segments[0]),
            validate_name("name", segments[1]),
            requirement,
        )
    raise InvalidIdentityError(f"invalid artifact reference: {text!r}")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FilesystemPermissions(_Strict):
    read: list[str] = Field(default_factory=list)
    write: list[str] = Field(default_factory=list)


class ShellPermissions(_Strict):
    allow: list[str] = Field(default_factory=list)


class Permissions(_Strict):
    filesystem: FilesystemPermissions = Field(default_factory=FilesystemPermissions)
    shell: ShellPermissions = Field(default_factory=ShellPermissions)
    network: list[str] = Field(default_factory=list)

    @field_validator("network", mode="before")
    @classmethod
    def _network_bool(cls, value: Any) -> Any:
        if value is True:
            return ["*"]
        if value in (False, None):
            return []
        return value

    def flatten(self) -> list[tuple[str, str]]:
        """``(category, pattern)`` pairs used for indexing and diffing."""
        out = [("filesystem.read", p) for p in self.filesystem.read]
        out += [("filesystem.write", p) for p in self.filesystem.write]
        out += [("shell.allow", p) for p in self.shell.allow]
        out += [("network", p) for p in self.network]
        return sorted(set(out))

    def implied_capabilities(self) -> set[str]:
        caps: set[str] = set()
        if self.filesystem.read:
            caps.add("filesystem.read")
        if self.filesystem.write:
            caps.add("filesystem.write")
        if self.shell.allow:
            caps.add("shell.execute")
        if self.network:
            caps.add("network.http")
        return caps


class SchemaContract(_Strict):
    """Input/output contract. ``json_schema`` is canonical JSON Schema (spec §26)."""

    schema_path: str | None = Field(default=None, alias="schema")
    json_schema: dict[str, Any] | None = None
    format: Literal["json-schema", "pydantic", "openapi", "schemars", "mcp"] = "json-schema"
    native: dict[str, Any] | None = None

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ToolSpec(_Strict):
    name: str
    description: str = ""
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None
    capabilities: list[str] = Field(default_factory=list)


class RuntimeSpec(_Strict):
    supported: list[str] = Field(default_factory=list)
    provider: str | None = None


class SkillRef(_Strict):
    ref: str
    version: str = "*"
    optional: bool = False


class EvaluationSpec(_Strict):
    suite: str | None = None
    min_score: float | None = None


class InstructionsSpec(_Strict):
    ref: str | None = None


class Dependency(_Strict):
    type: DependencyType = DependencyType.ARTIFACT
    id: str | None = None
    name: str | None = None
    version: str | None = None
    optional: bool = False

    _SHORTHAND: ClassVar[dict[str, ArtifactKind]] = {k.value: k for k in ArtifactKind}

    @model_validator(mode="before")
    @classmethod
    def _shorthand(cls, data: Any) -> Any:
        if not isinstance(data, dict) or "type" in data:
            return data
        data = dict(data)
        for key, kind in cls._SHORTHAND.items():
            if key in data:
                ref = parse_ref(str(data.pop(key)), kind)
                if not ref.namespace:
                    raise InvalidIdentityError(
                        f"dependency {ref} needs a namespace (e.g. core/{ref.name})"
                    )
                data.setdefault("version", ref.requirement)
                data["type"] = "artifact"
                data["id"] = artifact_uri(kind, ref.namespace, ref.name)
                return data
        for legacy, dep_type in (
            ("python", "python-package"),
            ("crate", "rust-crate"),
            ("binary", "system-binary"),
            ("mcp", "mcp-server"),
        ):
            if legacy in data:
                data["type"] = dep_type
                data["name"] = str(data.pop(legacy))
                return data
        return data

    @model_validator(mode="after")
    def _check(self) -> Dependency:
        if self.type is DependencyType.ARTIFACT:
            if not self.id:
                raise InvalidIdentityError("artifact dependency requires 'id'")
            ref = parse_ref(self.id)
            if ref.kind is None or ref.namespace is None:
                raise InvalidIdentityError(f"artifact dependency id must be a full URI: {self.id}")
            if ref.requirement:
                raise InvalidIdentityError("put the version in 'version', not in the id")
        elif not self.name:
            raise InvalidIdentityError(f"{self.type.value} dependency requires 'name'")
        if self.version and self.type in {DependencyType.ARTIFACT}:
            VersionReq.parse(self.version)
        return self

    @property
    def artifact_ref(self) -> ArtifactRef | None:
        return parse_ref(self.id) if self.type is DependencyType.ARTIFACT and self.id else None


class Compatibility(_Strict):
    ananke: str | None = None
    python: str | None = None
    rust: str | None = None
    runtimes: dict[str, str] = Field(default_factory=dict)
    operating_systems: list[str] = Field(default_factory=list)
    architecture: list[str] = Field(default_factory=list)
    mcp_protocol: str | None = None
    model_capabilities: list[str] = Field(default_factory=list)
    required_tools: list[str] = Field(default_factory=list)
    optional_tools: list[str] = Field(default_factory=list)

    def flatten(self) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        for key in ("ananke", "python", "rust", "mcp_protocol"):
            value = getattr(self, key)
            if value:
                out.append((key, str(value)))
        out += [(f"runtime:{k}", v) for k, v in sorted(self.runtimes.items())]
        for key in (
            "operating_systems",
            "architecture",
            "model_capabilities",
            "required_tools",
            "optional_tools",
        ):
            out += [(key, v) for v in getattr(self, key)]
        return out


class LicenseInfo(_Strict):
    expression: str | None = None
    source: str | None = None
    confidence: float = 1.0
    approval: Literal["approved", "denied", "pending", "unknown"] = "unknown"


class Owner(_Strict):
    team: str | None = None
    user: str | None = None


class Metadata(_Strict):
    tags: list[str] = Field(default_factory=list)
    homepage: str | None = None
    repository: str | None = None

    @field_validator("tags")
    @classmethod
    def _tags(cls, value: list[str]) -> list[str]:
        return sorted({t.strip().lower() for t in value if t.strip()})


class LifecycleInfo(_Strict):
    status: LifecycleStatus = LifecycleStatus.ACTIVE
    message: str | None = None
    replacement: str | None = None


class Quality(_Strict):
    tests_status: Literal["pass", "fail", "none", "unknown"] = "unknown"
    evaluation_suite: str | None = None
    evaluation_score: float | None = None
    last_verified_at: str | None = None


class Security(_Strict):
    status: Literal["unknown", "clean", "findings", "vulnerable"] = "unknown"
    last_scanned_at: str | None = None
    scanner: str | None = None
    findings_count: int = 0
    critical_count: int = 0


class SignatureInfo(_Strict):
    """A signature *claimed* by a source. It is evidence to check, never a trust decision:
    ``verified`` is forced to False on load and recomputed by the registry (see ``signing``)."""

    algorithm: str
    signer: str | None = None
    key_id: str | None = None
    value: str | None = None  # base64 signature over the version URI + payload digest
    verified: bool = False

    @field_validator("verified", mode="after")
    @classmethod
    def _never_self_verified(cls, _value: bool) -> bool:
        return False


class Publisher(_Strict):
    name: str
    url: str | None = None


class ImporterInfo(_Strict):
    id: str
    version: str = "1"


class GitInfo(_Strict):
    repository: str | None = None
    commit: str | None = None


class Provenance(_Strict):
    """Where a version came from (spec §27). Timestamps live here, never in the payload."""

    source_type: str = "local"
    source_name: str | None = None
    source_version: str | None = None
    source_url: str | None = None
    native_id: str | None = None
    git: GitInfo | None = None
    discovered_at: str | None = None
    importer: ImporterInfo | None = None
    publisher: Publisher | None = None
    fingerprint: str | None = None
    signature: SignatureInfo | None = None
    native_version: str | None = None
    native_schema: dict[str, Any] | None = None


class ArtifactManifest(_Strict):
    """Canonical manifest for any artifact kind (spec §22, §23, §33)."""

    schema_version: int = REGISTRY_SCHEMA_VERSION
    kind: ArtifactKind
    namespace: str
    name: str
    version: str
    ananke_id: str | None = None
    summary: str = ""
    description: str = ""

    capabilities: list[str] = Field(default_factory=list)
    tools: list[ToolSpec] = Field(default_factory=list)
    inputs: SchemaContract | None = None
    outputs: SchemaContract | None = None
    runtime: RuntimeSpec = Field(default_factory=RuntimeSpec)
    model_requirements: dict[str, Any] = Field(default_factory=dict)
    permissions: Permissions = Field(default_factory=Permissions)

    skills: list[SkillRef] = Field(default_factory=list)
    members: list[SkillRef] = Field(default_factory=list)
    policies: list[str] = Field(default_factory=list)
    evaluation: EvaluationSpec | None = None
    instructions: InstructionsSpec | None = None

    dependencies: list[Dependency] = Field(default_factory=list)
    compatibility: Compatibility = Field(default_factory=Compatibility)
    license: LicenseInfo = Field(default_factory=LicenseInfo)
    metadata: Metadata = Field(default_factory=Metadata)
    owners: list[Owner] = Field(default_factory=list)
    maintainers: list[str] = Field(default_factory=list)
    lifecycle: LifecycleInfo = Field(default_factory=LifecycleInfo)
    quality: Quality = Field(default_factory=Quality)

    @field_validator("schema_version")
    @classmethod
    def _schema_version(cls, value: int) -> int:
        if not 1 <= value <= REGISTRY_SCHEMA_VERSION:
            raise ValueError(
                f"unsupported manifest schema_version {value} (supported: 1..{REGISTRY_SCHEMA_VERSION})"
            )
        return value

    @field_validator("namespace")
    @classmethod
    def _ns(cls, value: str) -> str:
        return validate_name("namespace", value)

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        return validate_name("name", value)

    @field_validator("capabilities")
    @classmethod
    def _caps(cls, value: list[str]) -> list[str]:
        for cap in value:
            if not _CAP_RE.match(cap):
                raise ValueError(
                    f"invalid capability id {cap!r}: use hierarchical ids like graph.query"
                )
        return sorted(set(value))

    @property
    def uri(self) -> str:
        return artifact_uri(self.kind, self.namespace, self.name)

    def canonical_dict(self) -> dict[str, Any]:
        """Stable dict used for hashing: defaults/None removed so new optional fields
        never change the digest of previously registered payloads."""
        return self.model_dump(mode="json", exclude_defaults=True, exclude_none=True, by_alias=True)

    def all_dependencies(self) -> list[Dependency]:
        """Explicit dependencies plus those implied by ``skills``, ``members`` and
        slash-qualified ``policies`` (agents/bundles are versioned dependency graphs)."""
        deps = list(self.dependencies)
        seen = {(d.type, d.id, d.name) for d in deps}
        implied: list[Dependency] = []
        for entry, kind in [(s, ArtifactKind.SKILL) for s in self.skills] + [
            (m, None) for m in self.members
        ]:
            ref = parse_ref(entry.ref, kind)
            if ref.kind is None:
                ref = ArtifactRef(ArtifactKind.SKILL, ref.namespace, ref.name, ref.requirement)
            if not ref.namespace:
                raise InvalidIdentityError(f"reference {entry.ref!r} needs a namespace")
            implied.append(
                Dependency(
                    type=DependencyType.ARTIFACT,
                    id=artifact_uri(ref.kind or ArtifactKind.SKILL, ref.namespace, ref.name),
                    version=ref.requirement or entry.version,
                    optional=entry.optional,
                )
            )
        for pol in self.policies:
            if "/" in pol:
                pref = parse_ref(pol, ArtifactKind.POLICY)
                if pref.namespace:
                    implied.append(
                        Dependency(
                            type=DependencyType.ARTIFACT,
                            id=artifact_uri(ArtifactKind.POLICY, pref.namespace, pref.name),
                            version=pref.requirement or "*",
                        )
                    )
        if self.evaluation and self.evaluation.suite and "/" in self.evaluation.suite:
            eref = parse_ref(self.evaluation.suite, ArtifactKind.EVALUATOR)
            if eref.namespace:
                implied.append(
                    Dependency(
                        type=DependencyType.ARTIFACT,
                        id=artifact_uri(ArtifactKind.EVALUATOR, eref.namespace, eref.name),
                        version=eref.requirement or "*",
                    )
                )
        for dep in implied:
            key = (dep.type, dep.id, dep.name)
            if key not in seen:
                seen.add(key)
                deps.append(dep)
        return deps

    def artifact_dependencies(self) -> list[Dependency]:
        return [d for d in self.all_dependencies() if d.type is DependencyType.ARTIFACT]


class Diagnostic(BaseModel):
    level: Literal["info", "warning", "error"] = "warning"
    code: str
    message: str
    field: str | None = None


class VersionRecord(BaseModel):
    """A registered, immutable artifact version plus its mutable registry metadata."""

    db_id: int = Field(default=0, exclude=True)
    uri: str
    kind: ArtifactKind
    namespace: str
    name: str
    version: str
    lifecycle: LifecycleStatus
    channel: str
    trust: TrustStatus
    summary: str = ""
    description: str = ""
    digest_sha256: str
    digest_blake3: str | None = None
    payload_size: int = 0
    revision: int = 0
    created_at: str
    manifest: ArtifactManifest
    provenance: Provenance = Field(default_factory=Provenance)
    license: LicenseInfo = Field(default_factory=LicenseInfo)
    quality: Quality = Field(default_factory=Quality)
    security: Security = Field(default_factory=Security)
    lifecycle_message: str | None = None
    replacement: str | None = None

    @property
    def semver(self) -> Version:
        return Version.parse(self.version)

    @property
    def digest(self) -> str:
        return f"sha256:{self.digest_sha256}"

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.kind.value, self.namespace, self.name)

    @property
    def version_uri(self) -> str:
        return f"{self.uri}@{self.version}"
