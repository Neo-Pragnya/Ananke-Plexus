"""Hook system data models."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

HookStage = Literal["pre-commit", "post-commit", "pre-push"]
HookMode = Literal["native", "pre-commit-framework", "delegated", "disabled"]


class HookConfig(BaseModel):
    mode: HookMode = "native"
    chain: bool = False
    stages: list[HookStage] = Field(
        default_factory=lambda: ["pre-commit", "post-commit", "pre-push"]
    )


class HookStatus(BaseModel):
    stage: HookStage
    installed: bool
    managed: bool
    path: str
    mode: HookMode
    chained: bool = False


class HookRunResult(BaseModel):
    stage: HookStage
    ok: bool
    summary: str
    checks: list[dict[str, object]] = Field(default_factory=list)
