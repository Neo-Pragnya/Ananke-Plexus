"""APM manifest parsing and validation."""

import tomllib
from pathlib import Path

from pydantic import BaseModel, Field


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


def load_manifest(path: Path) -> SkillManifest:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return SkillManifest.model_validate(data)
