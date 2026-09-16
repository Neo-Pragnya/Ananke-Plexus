"""Graph provider resolution — returns the best available provider."""

from __future__ import annotations

from ananke.plexus.graph.providers.base import GraphProvider
from ananke.plexus.graph.providers.code_review_graph import CodeReviewGraphAdapter
from ananke.plexus.graph.providers.graphifyy import GraphifyyAdapter
from ananke.plexus.graph.providers.native import NativeGraphProvider


def resolve_graph_provider(provider_name: str) -> GraphProvider:
    name = provider_name.strip().lower()
    if name == "graphifyy":
        adapter = GraphifyyAdapter()
        if adapter.available():
            return adapter  # type: ignore[return-value]
    if name in {"code-review-graph", "crg"}:
        adapter_crg = CodeReviewGraphAdapter()
        if adapter_crg.available():
            return adapter_crg  # type: ignore[return-value]
    return NativeGraphProvider()


def list_available_providers() -> list[dict[str, object]]:
    providers = [
        {"name": "native", "available": True, "description": "Built-in AST-based provider"},
        {
            "name": "graphifyy",
            "available": GraphifyyAdapter().available(),
            "description": "Graphifyy knowledge-graph enrichment",
        },
        {
            "name": "code-review-graph",
            "available": CodeReviewGraphAdapter().available(),
            "description": "Structural code review + blast-radius",
        },
    ]
    return providers
