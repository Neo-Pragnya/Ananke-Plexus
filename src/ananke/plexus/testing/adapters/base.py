"""TestAdapter Protocol and AdapterCapabilities."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from ananke.plexus.testing.models.result import TestResult
from ananke.plexus.testing.models.test import TestDefinition


class AdapterCapabilities(BaseModel):
    kinds: set[str] = Field(default_factory=set)
    supports_selection: bool = False
    supports_parallelism: bool = False
    supports_seed: bool = False
    supports_timeout: bool = True
    supports_junit: bool = False
    supports_json: bool = False
    supports_coverage: bool = False
    network_required: bool = False
    languages: list[str] = Field(default_factory=list)


@runtime_checkable
class TestAdapter(Protocol):
    adapter_id: str

    def available(self) -> bool: ...

    def version(self) -> str | None: ...

    def capabilities(self) -> AdapterCapabilities: ...

    def discover(self, project_root: Path, profile: str) -> list[TestDefinition]: ...

    def run(
        self,
        tests: list[TestDefinition],
        project_root: Path,
        timeout: int | None,
    ) -> list[TestResult]: ...

    def doctor(self) -> dict[str, Any]: ...
