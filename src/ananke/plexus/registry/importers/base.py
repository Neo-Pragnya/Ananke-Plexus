"""Importer port (spec §35, §36, §172).

Importers turn an external source into :class:`Candidate` drafts. A candidate is not yet a
valid manifest: required fields may be missing (namespace, license, …). ``finalize`` merges
explicit answers/overrides and validates it into an :class:`ImportedArtifact`, reporting
every missing field as a diagnostic instead of guessing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ValidationError

from ananke.plexus.registry.errors import InvalidIdentityError, ManifestError
from ananke.plexus.registry.models import (
    ArtifactKind,
    ArtifactManifest,
    Diagnostic,
    Provenance,
)
from ananke.plexus.registry.policy import RegistryPolicy

REQUIRED_FIELDS = ("kind", "namespace", "name", "version")
_SCHEMES = {
    "python",
    "rust",
    "cargo",
    "mcp",
    "mcp-stdio",
    "mcp-http",
    "git",
    "archive",
    "framework",
    "dynamic",
    "path",
}
_ARCHIVE_SUFFIXES = (".tar.gz", ".tgz", ".tar", ".zip")
_URL_RE = re.compile(r"^(https?|ssh|git)://|^git@[\w.\-]+:")


@dataclass(frozen=True)
class Source:
    raw: str
    scheme: str
    target: str

    @property
    def path(self) -> Path:
        return Path(self.target).expanduser()


def parse_source(raw: str) -> Source:
    text = raw.strip()
    if not text:
        raise ManifestError("empty source")
    head, sep, rest = text.partition(":")
    if sep and head in _SCHEMES and len(head) != 1:
        # Windows drive letters are single characters and never schemes.
        return Source(text, "rust" if head == "cargo" else head, rest)
    if _URL_RE.match(text):
        return Source(text, "git", text)
    if text.lower().endswith(_ARCHIVE_SUFFIXES) and Path(text).expanduser().is_file():
        return Source(text, "archive", text)
    return Source(text, "path", text)


class ProbeResult(BaseModel):
    ok: bool
    confidence: float = 0.0
    reason: str = ""


class ImporterPermissions(BaseModel):
    reads_filesystem: bool = True
    executes_code: bool = False
    network: bool = False


@dataclass
class InspectionContext:
    policy: RegistryPolicy = field(default_factory=RegistryPolicy)
    allow_dynamic: bool = False
    allow_network: bool = False
    namespace: str | None = None
    kind: ArtifactKind | None = None
    plugin_paths: list[str] = field(default_factory=list)
    plugin: str | None = None
    project_root: Path | None = None

    @property
    def default_namespace(self) -> str | None:
        return self.namespace or self.policy.default_namespace


@dataclass
class Candidate:
    """A discovered but not yet validated artifact."""

    draft: dict[str, Any]
    files: dict[str, bytes] = field(default_factory=dict)
    provenance: Provenance = field(default_factory=Provenance)
    diagnostics: list[Diagnostic] = field(default_factory=list)
    dynamic: bool = False
    source: str = ""

    def missing_required(self) -> list[str]:
        return [f for f in REQUIRED_FIELDS if not self.draft.get(f)]

    def note(
        self,
        level: Literal["info", "warning", "error"],
        code: str,
        message: str,
        field_: str | None = None,
    ) -> None:
        self.diagnostics.append(Diagnostic(level=level, code=code, message=message, field=field_))


@dataclass
class ImportedArtifact:
    manifest: ArtifactManifest
    files: dict[str, bytes]
    provenance: Provenance
    diagnostics: list[Diagnostic] = field(default_factory=list)
    dynamic: bool = False


def _deep_merge(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def finalize(
    candidate: Candidate,
    *,
    overrides: dict[str, Any] | None = None,
    ctx: InspectionContext | None = None,
) -> ImportedArtifact:
    """Merge explicit answers into a candidate and validate it into a manifest."""
    draft = dict(candidate.draft)
    if ctx is not None:
        if not draft.get("namespace") and ctx.default_namespace:
            draft["namespace"] = ctx.default_namespace
            candidate.note(
                "info",
                "default-namespace",
                f"using default namespace {ctx.default_namespace!r}",
                "namespace",
            )
        if not draft.get("kind") and ctx.kind:
            draft["kind"] = ctx.kind.value
    if overrides:
        draft = _deep_merge(draft, {k: v for k, v in overrides.items() if v not in (None, "")})
    missing = [f for f in REQUIRED_FIELDS if not draft.get(f)]
    if missing:
        raise ManifestError(
            "missing required manifest fields: "
            + ", ".join(missing)
            + " (supply them in the manifest or via CLI options)"
        )
    try:
        manifest = ArtifactManifest.model_validate(draft)
    except (ValidationError, InvalidIdentityError) as exc:
        raise ManifestError(
            f"invalid manifest for {candidate.source or 'candidate'}: {exc}"
        ) from exc
    diags = list(candidate.diagnostics)
    if not manifest.license.expression:
        diags.append(
            Diagnostic(
                level="warning",
                code="missing-license",
                message="no license metadata found",
                field="license",
            )
        )
    if not manifest.summary:
        diags.append(
            Diagnostic(
                level="info", code="missing-summary", message="no summary provided", field="summary"
            )
        )
    if manifest.kind.value in {"skill", "tool"} and not (
        manifest.permissions.flatten() or manifest.capabilities
    ):
        diags.append(
            Diagnostic(
                level="warning",
                code="missing-permissions",
                message="no explicit permissions or capabilities declared",
                field="permissions",
            )
        )
    return ImportedArtifact(
        manifest, candidate.files, candidate.provenance, diags, candidate.dynamic
    )


@runtime_checkable
class RegistryImporter(Protocol):
    id: ClassVar[str]
    version: ClassVar[str]
    static: ClassVar[bool]
    permissions: ClassVar[ImporterPermissions]

    def probe(self, source: Source, ctx: InspectionContext) -> ProbeResult: ...

    def inspect(self, source: Source, ctx: InspectionContext) -> list[Candidate]: ...


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9._-]+", "-", text.strip().lower()).strip("-._")
    slug = re.sub(r"-{2,}", "-", slug)
    return slug[:64].strip("-._") or "unnamed"
