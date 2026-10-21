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


class ConfigModel(BaseModel):
    project: ProjectConfig = Field(default_factory=ProjectConfig)
    ananke: AnankeConfig = Field(default_factory=AnankeConfig)
    spec: SpecConfig = Field(default_factory=SpecConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    troves: dict[str, TroveConfig] = Field(default_factory=dict)
