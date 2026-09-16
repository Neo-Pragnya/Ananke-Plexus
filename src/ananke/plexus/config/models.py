"""Configuration models."""

from typing import Literal

from pydantic import BaseModel, Field


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


class ConfigModel(BaseModel):
    project: ProjectConfig = Field(default_factory=ProjectConfig)
    ananke: AnankeConfig = Field(default_factory=AnankeConfig)
    spec: SpecConfig = Field(default_factory=SpecConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
