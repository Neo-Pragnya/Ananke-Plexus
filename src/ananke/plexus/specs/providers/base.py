"""Provider contracts for specification generation."""

from pathlib import Path
from typing import Protocol

from ananke.plexus.specs.models import Requirement


class SpecProvider(Protocol):
    """Provider interface for spec lifecycle generation."""

    provider_id: str

    def available(self) -> tuple[bool, str]: ...

    def create(self, repository_root: Path, requirement: Requirement) -> Path: ...

    def plan(self, feature_dir: Path) -> Path: ...

    def tasks(self, feature_dir: Path) -> Path: ...
