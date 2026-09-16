"""Tests for graph providers registry and adapters (F2-F4)."""

from ananke.plexus.graph.providers.code_review_graph import CodeReviewGraphAdapter
from ananke.plexus.graph.providers.graphifyy import GraphifyyAdapter
from ananke.plexus.graph.providers.native import NativeGraphProvider
from ananke.plexus.graph.providers.registry import list_available_providers, resolve_graph_provider


def test_native_provider_available():
    p = NativeGraphProvider()
    assert p.provider_id == "native"


def test_graphifyy_adapter_unavailable():
    # In test env, graphifyy is not installed
    a = GraphifyyAdapter()
    assert a.available() is False


def test_graphifyy_adapter_build_returns_empty_snapshot(tmp_path):
    a = GraphifyyAdapter()
    snap = a.build(tmp_path)
    assert snap.provider == "graphifyy"
    assert snap.nodes == []


def test_graphifyy_adapter_impact_unavailable(tmp_path):
    a = GraphifyyAdapter()
    report = a.impact(tmp_path, ["src/main.py"])
    assert "unavailable" in report.summary.lower()


def test_code_review_graph_unavailable():
    a = CodeReviewGraphAdapter()
    assert a.available() is False


def test_code_review_graph_build_returns_empty(tmp_path):
    a = CodeReviewGraphAdapter()
    snap = a.build(tmp_path)
    assert snap.provider == "code-review-graph"


def test_list_available_providers():
    providers = list_available_providers()
    names = [p["name"] for p in providers]
    assert "native" in names
    assert "graphifyy" in names
    assert "code-review-graph" in names


def test_resolve_native():
    p = resolve_graph_provider("native")
    assert isinstance(p, NativeGraphProvider)


def test_resolve_unknown_falls_back_to_native():
    p = resolve_graph_provider("nonexistent-provider")
    assert isinstance(p, NativeGraphProvider)
