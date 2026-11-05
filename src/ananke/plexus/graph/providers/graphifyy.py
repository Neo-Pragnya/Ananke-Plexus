"""Graphifyy graph provider adapter (F3).

Uses Graphifyy for broad knowledge-graph enrichment and multi-source relationships.
Falls back gracefully when Graphifyy is not installed.

When Graphify's command is available, this refreshes its graph and normalizes it to the canonical Ananke
graph model. The graph is a snapshot: for code as it is now, see ``ananke.plexus.context``.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from ananke.plexus.graph.models import GraphEdge, GraphNode, GraphSnapshot, ImpactReport


class GraphifyyAdapter:
    provider_id = "graphifyy"

    def available(self) -> bool:
        # the PyPI package is graphifyy; the module and command are graphify
        return shutil.which("graphify") is not None

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
        """Refresh Graphify's code graph, which needs no LLM, and read it. It writes to ``graphify-out/``."""
        done = subprocess.run(  # noqa: S603 - argv list, no shell
            ["graphify", "update", str(repository_root)],  # noqa: S607 - checked by available()
            capture_output=True,
            timeout=900,
            check=False,
        )
        graph = repository_root / "graphify-out" / "graph.json"
        if done.returncode != 0 or not graph.is_file():
            return _unavailable_snapshot()
        raw = json.loads(graph.read_text(encoding="utf-8"))

        nodes = [
            GraphNode(
                node_id=str(item.get("id", "")),
                kind=str(item.get("file_type", "code")),
                name=str(item.get("label", "")),
                path=str(item.get("source_file", "")),
                provider="graphifyy",
                metadata={
                    "community": item.get("community"),
                    "location": item.get("source_location"),
                },
            )
            for item in raw.get("nodes", [])
        ]
        edges = [
            GraphEdge(
                source=str(item.get("source", "")),
                target=str(item.get("target", "")),
                kind=str(item.get("relation", "depends_on")),
                origin="inferred" if item.get("confidence") == "INFERRED" else "extracted",
                provider="graphifyy",
                source_location=str(item.get("source_file") or ""),
            )
            for item in raw.get("links", [])
        ]

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
