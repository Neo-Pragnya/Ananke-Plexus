"""Tests for canonical graph models (F1)."""

from ananke.plexus.graph.models import (
    GraphEdge,
    GraphNode,
    GraphResult,
    GraphSnapshot,
    ImpactReport,
    ReconciledEdge,
)


def test_graph_node_defaults():
    node = GraphNode(node_id="n1", kind="file", name="main.py")
    assert node.provider == "native"
    assert node.metadata == {}
    assert node.path == ""


def test_graph_edge_defaults():
    edge = GraphEdge(source="a", target="b", kind="imports")
    assert edge.origin == "extracted"
    assert edge.confidence == 1.0
    assert edge.provider == "native"


def test_graph_snapshot_serialization():
    snap = GraphSnapshot(
        snapshot_id="s1",
        nodes=[GraphNode(node_id="n", kind="function", name="f")],
        edges=[GraphEdge(source="n", target="m", kind="calls")],
    )
    d = snap.model_dump()
    assert d["snapshot_id"] == "s1"
    assert len(d["nodes"]) == 1
    assert len(d["edges"]) == 1


def test_impact_report_defaults():
    report = ImpactReport(summary="ok")
    assert report.blast_radius == 0
    assert report.forbidden_edges == []
    assert report.impacted_files == []


def test_reconciled_edge():
    edge = GraphEdge(source="a", target="b", kind="imports")
    rec = ReconciledEdge(edge=edge, status="CONSENSUS", providers=["native", "graphifyy"])
    assert rec.status == "CONSENSUS"
    assert len(rec.providers) == 2


def test_graph_result_empty():
    result = GraphResult()
    assert result.nodes == []
    assert result.edges == []
    assert result.summary == ""
