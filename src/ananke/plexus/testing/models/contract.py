"""ContractDefinition — consumer/provider contract test models."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ContractDefinition(BaseModel):
    id: str
    consumer: str
    provider: str
    protocol: str = "http"
    schemas: list[str] = Field(default_factory=list)
    version: str = "1"
    engine: str = "pact"
    config: dict[str, Any] = Field(default_factory=dict)
