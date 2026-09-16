"""code-review-graph provider adapter (F4).

Uses code-review-graph for structural code review and incremental
blast-radius analysis.  Falls back gracefully when not installed.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from ananke.plexus.graph.models import GraphEdge, GraphNode, GraphSnapshot, ImpactReport


class CodeReviewGraphAdapter:
    provider_id = "code-review-graph"

    def available(self) -> bool:
        try:
            import code_review_graph  # type: ignore[import-not-found]  # noqa: F401

            return True
        except ImportError:
            return False

    def build(self, repository_root: Path) -> GraphSnapshot:
        if not self.available():
            return _unavailable_snapshot()
        try:
            return self._build_via_crg(repository_root)
        except Exception:
            return _unavailable_snapshot()

    def impact(self, repository_root: Path, changed_files: list[str]) -> ImpactReport:
        if not self.available():
            return ImpactReport(
                changed_files=changed_files,
                summary="code-review-graph unavailable",
            )
        try:
            return self._impact_via_crg(repository_root, changed_files)
        except Exception:
            return ImpactReport(
                changed_files=changed_files,
                summary="code-review-graph impact failed",
            )

    def _build_via_crg(self, repository_root: Path) -> GraphSnapshot:
        import code_review_graph  # type: ignore[import-not-found]

        raw = code_review_graph.build(str(repository_root))  # type: ignore[attr-defined]
        nodes = [
            GraphNode(
                node_id=str(n.get("id", "")),
                kind=str(n.get("kind", "file")),
                name=str(n.get("name", "")),
                path=str(n.get("path", "")),
                provider="code-review-graph",
            )
            for n in raw.get("nodes", [])
        ]
        edges = [
            GraphEdge(
                source=str(e.get("source", "")),
                target=str(e.get("target", "")),
                kind=str(e.get("kind", "imports")),
                origin=str(e.get("origin", "extracted")),
                provider="code-review-graph",
            )
            for e in raw.get("edges", [])
        ]
        return GraphSnapshot(
            snapshot_id=datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"),
            provider="code-review-graph",
            nodes=nodes,
            edges=edges,
        )

    def _impact_via_crg(self, repository_root: Path, changed_files: list[str]) -> ImpactReport:
        import code_review_graph  # type: ignore[import-not-found]

        raw = code_review_graph.impact(  # type: ignore[attr-defined]
            str(repository_root), changed_files
        )
        return ImpactReport(
            changed_files=changed_files,
            impacted_symbols=list(raw.get("symbols", [])),
            impacted_files=list(raw.get("files", [])),
            blast_radius=int(raw.get("blast_radius", 0)),
            summary=str(raw.get("summary", "code-review-graph impact")),
        )


def _unavailable_snapshot() -> GraphSnapshot:
    return GraphSnapshot(
        snapshot_id=datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"),
        provider="code-review-graph",
        nodes=[],
        edges=[],
    )
