"""Tests for graph service — impact with new blast_radius field."""

from ananke.plexus.graph.service import (
    build_graph,
    export_graph,
    impact_from_paths,
    persist_graph,
    query_graph,
)


def test_build_graph_empty_dir(tmp_path):
    snap = build_graph(tmp_path)
    assert snap.snapshot_id
    assert snap.nodes == []


def test_build_graph_python_file(tmp_path):
    (tmp_path / "main.py").write_text(
        "def foo():\n    pass\n\nclass Bar:\n    pass\n",
        encoding="utf-8",
    )
    snap = build_graph(tmp_path)
    kinds = {n.kind for n in snap.nodes}
    assert "file" in kinds
    assert "function" in kinds
    assert "class" in kinds


def test_persist_and_load(tmp_path):
    snap = build_graph(tmp_path)
    path = persist_graph(tmp_path, snap)
    assert path.exists()


def test_query_graph_no_graph(tmp_path):
    result = query_graph(tmp_path, "main")
    assert result == []


def test_query_graph_finds_function(tmp_path):
    (tmp_path / "auth.py").write_text("def authenticate():\n    pass\n", encoding="utf-8")
    snap = build_graph(tmp_path)
    persist_graph(tmp_path, snap)
    result = query_graph(tmp_path, "authenticate")
    assert any(n.name == "authenticate" for n in result)


def test_impact_no_graph(tmp_path):
    report = impact_from_paths(tmp_path, ["src/main.py"])
    assert "not built" in report.summary.lower() or report.changed_files == ["src/main.py"]


def test_impact_with_graph(tmp_path):
    (tmp_path / "main.py").write_text("def run():\n    pass\n", encoding="utf-8")
    snap = build_graph(tmp_path)
    persist_graph(tmp_path, snap)
    report = impact_from_paths(tmp_path, ["main.py"])
    assert isinstance(report.blast_radius, int)
    assert isinstance(report.impacted_symbols, list)


def test_export_graph_json(tmp_path):
    snap = build_graph(tmp_path)
    persist_graph(tmp_path, snap)
    path = export_graph(tmp_path, fmt="json")
    assert path.exists()
