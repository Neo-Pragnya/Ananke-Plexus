"""Configuration models."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ProjectConfig(BaseModel):
    name: str = "ananke-project"
    default_branch: str = "main"


class AnankeConfig(BaseModel):
    mode: Literal["developer", "ci", "strict"] = "developer"
    fail_closed: bool = True
    offline: bool = False


class SpecConfig(BaseModel):
    provider: str = "speckit"
    contract_mode: str = "ananke-bmad"


class SecurityConfig(BaseModel):
    allow_network: bool = False
    secret_scan: bool = True
    sast: bool = True
    sca: bool = True
    license_scan: bool = True


class TroveConfig(BaseModel):
    """One installed trove (a package exposing an ``ananke.troves`` entry point), e.g. ``[troves.dex-trove]``."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    kinds: list[str] = Field(
        default_factory=list, description="only these artifact kinds (empty = all)"
    )
    namespaces: list[str] = Field(
        default_factory=list, description="only these namespaces (empty = all)"
    )
    include_flagged: bool = Field(
        default=False, description="also learn items whose placeholders trip the secret scanner"
    )


class CapabilitySourceConfig(BaseModel):
    """An approved local folder of capabilities, e.g. ``[capability_sources.team]`` (see registry/providers.py)."""

    model_config = ConfigDict(extra="forbid")

    type: str = Field(
        default="local", description="local (an approved folder); troves are added from [troves]"
    )
    root: str = Field(description="folder of skills, relative to the project or absolute")
    namespace: str = Field(description="namespace every capability from this source gets")
    writable: bool = Field(default=False, description="allow reviewed publishes into this folder")
    enabled: bool = True


class ConfigModel(BaseModel):
    project: ProjectConfig = Field(default_factory=ProjectConfig)
    ananke: AnankeConfig = Field(default_factory=AnankeConfig)
    spec: SpecConfig = Field(default_factory=SpecConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    troves: dict[str, TroveConfig] = Field(default_factory=dict)
    capability_sources: dict[str, CapabilitySourceConfig] = Field(default_factory=dict)
