"""EvalCase — a single evaluation input/expected pair."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class EvalCase(BaseModel):
    id: str
    input: Any
    expected: Any | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)

    def has_tag(self, tag: str) -> bool:
        return tag in self.tags
