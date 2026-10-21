"""APM manifest parsing and validation."""

import tomllib
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError


class SkillSection(BaseModel):
    name: str
    version: str
    description: str = ""
    license: str = ""


class CompatibilitySection(BaseModel):
    ananke: str = ">=0"
    skill_api: str = "1"


class PermissionSection(BaseModel):
    filesystem_read: list[str] = Field(default_factory=list)
    filesystem_write: list[str] = Field(default_factory=list)
    network: list[str] = Field(default_factory=list)
    shell: list[str] = Field(default_factory=list)


class EntrypointsSection(BaseModel):
    instructions: str = "SKILL.md"


class ProvenanceSection(BaseModel):
    source: str = "local"
    repository: str = ""
    revision: str = ""


class SkillManifest(BaseModel):
    skill: SkillSection
    compatibility: CompatibilitySection = Field(default_factory=CompatibilitySection)
    permissions: PermissionSection = Field(default_factory=PermissionSection)
    entrypoints: EntrypointsSection = Field(default_factory=EntrypointsSection)
    provenance: ProvenanceSection = Field(default_factory=ProvenanceSection)


class ManifestError(ValueError):
    """The file is missing, is not TOML, or is not an APM skill manifest."""


def load_manifest(path: Path) -> SkillManifest:
    if not path.is_file():
        raise ManifestError(f"{path}: no such manifest")
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise ManifestError(f"{path}: not a TOML APM manifest (ananke-skill.toml): {exc}") from None
    try:
        return SkillManifest.model_validate(data)
    except ValidationError as exc:
        fields = ", ".join(".".join(str(p) for p in e["loc"]) for e in exc.errors()[:5])
        raise ManifestError(f"{path}: not a valid APM skill manifest (check: {fields})") from None
