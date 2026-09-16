"""Graph provider interface."""

from pathlib import Path
from typing import Protocol

from ananke.plexus.graph.models import GraphSnapshot, ImpactReport


class GraphProvider(Protocol):
    provider_id: str

    def build(self, repository_root: Path) -> GraphSnapshot: ...

    def impact(self, repository_root: Path, changed_files: list[str]) -> ImpactReport: ...
