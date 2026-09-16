"""Native graph provider implementation."""

from pathlib import Path

from ananke.plexus.graph.models import GraphSnapshot, ImpactReport
from ananke.plexus.graph.service import build_graph, impact_from_paths

from .base import GraphProvider


class NativeGraphProvider(GraphProvider):
    provider_id = "native"

    def build(self, repository_root: Path) -> GraphSnapshot:
        return build_graph(repository_root)

    def impact(self, repository_root: Path, changed_files: list[str]) -> ImpactReport:
        return impact_from_paths(repository_root, changed_files)
