"""Graphifyy graph provider adapter (F3).

Uses Graphifyy for broad knowledge-graph enrichment and multi-source relationships.
Falls back gracefully when Graphifyy is not installed.

When Graphifyy is available it is called via its Python API; this adapter
normalizes its output to the canonical Ananke graph model.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from ananke.plexus.graph.models import GraphEdge, GraphNode, GraphSnapshot, ImpactReport


class GraphifyyAdapter:
    provider_id = "graphifyy"

    def available(self) -> bool:
        try:
            import graphifyy  # type: ignore[import-not-found]  # noqa: F401

            return True
        except ImportError:
            return False

    def build(self, repository_root: Path) -> GraphSnapshot:
        if not self.available():
            return _unavailable_snapshot()
        try:
            return self._build_via_graphifyy(repository_root)
        except Exception:
            return _unavailable_snapshot()

    def impact(self, repository_root: Path, changed_files: list[str]) -> ImpactReport:
        if not self.available():
            return ImpactReport(
                changed_files=changed_files,
                summary="graphifyy unavailable",
            )
        snapshot = self.build(repository_root)
        path_set = set(changed_files)
        symbols = [
            e.target
            for e in snapshot.edges
            if e.kind == "owns" and e.source.replace("file:", "") in path_set
        ]
        return ImpactReport(
            changed_files=changed_files,
            impacted_symbols=sorted(symbols),
            blast_radius=len(symbols),
            summary=f"graphifyy: {len(symbols)} impacted symbols",
        )

    def _build_via_graphifyy(self, repository_root: Path) -> GraphSnapshot:
        import graphifyy  # type: ignore[import-not-found]

        raw = graphifyy.analyze(str(repository_root))  # type: ignore[attr-defined]
        nodes: list[GraphNode] = []
        edges: list[GraphEdge] = []

        for item in raw.get("nodes", []):
            nodes.append(
                GraphNode(
                    node_id=str(item.get("id", "")),
                    kind=str(item.get("kind", "file")),
                    name=str(item.get("name", "")),
                    path=str(item.get("path", "")),
                    provider="graphifyy",
                    metadata={
                        k: v for k, v in item.items() if k not in {"id", "kind", "name", "path"}
                    },
                )
            )

        for item in raw.get("edges", []):
            edges.append(
                GraphEdge(
                    source=str(item.get("source", "")),
                    target=str(item.get("target", "")),
                    kind=str(item.get("kind", "depends_on")),
                    origin=str(item.get("origin", "extracted")),
                    provider="graphifyy",
                    confidence=float(item.get("confidence", 1.0)),
                )
            )

        return GraphSnapshot(
            snapshot_id=datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"),
            provider="graphifyy",
            nodes=nodes,
            edges=edges,
        )


def _unavailable_snapshot() -> GraphSnapshot:
    return GraphSnapshot(
        snapshot_id=datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"),
        provider="graphifyy",
        nodes=[],
        edges=[],
    )
