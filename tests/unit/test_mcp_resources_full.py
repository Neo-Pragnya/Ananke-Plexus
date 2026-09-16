"""Additional coverage tests for MCP resources."""

import json

from ananke.plexus.mcp.resources import read_resource


def test_graph_snapshot_not_built(tmp_path):
    result = read_resource(tmp_path, "ananke://graph/snapshot")
    assert "not built" in result


def test_graph_snapshot_found(tmp_path):
    graph_dir = tmp_path / ".ananke" / "graph"
    graph_dir.mkdir(parents=True)
    (graph_dir / "metadata.json").write_text('{"snapshot_id": "s1"}', encoding="utf-8")
    result = read_resource(tmp_path, "ananke://graph/snapshot")
    assert "s1" in result


def test_evidence_index_empty(tmp_path):
    result = read_resource(tmp_path, "ananke://evidence/index")
    assert "no evidence" in result


def test_evidence_index_found(tmp_path):
    run_dir = tmp_path / ".ananke" / "evidence" / "run-001"
    run_dir.mkdir(parents=True)
    result = read_resource(tmp_path, "ananke://evidence/index")
    data = json.loads(result)
    assert "run-001" in data["runs"]


def test_run_index_empty(tmp_path):
    result = read_resource(tmp_path, "ananke://run/index")
    assert "no runs" in result


def test_run_index_found(tmp_path):
    run_dir = tmp_path / ".ananke" / "runs" / "run-abc"
    run_dir.mkdir(parents=True)
    result = read_resource(tmp_path, "ananke://run/index")
    data = json.loads(result)
    assert "run-abc" in data["runs"]


def test_per_run_evidence_not_found(tmp_path):
    result = read_resource(tmp_path, "ananke://run/no-such-run/evidence")
    assert "not found" in result


def test_per_run_evidence_found(tmp_path):
    run_dir = tmp_path / ".ananke" / "evidence" / "run-xyz"
    run_dir.mkdir(parents=True)
    (run_dir / "manifest.json").write_text('{"run_id": "run-xyz"}', encoding="utf-8")
    result = read_resource(tmp_path, "ananke://run/run-xyz/evidence")
    assert "run-xyz" in result


def test_per_policy_not_found(tmp_path):
    result = read_resource(tmp_path, "ananke://policy/baseline")
    assert "not found" in result


def test_per_policy_found(tmp_path):
    policy_dir = tmp_path / ".ananke" / "policy"
    policy_dir.mkdir(parents=True)
    (policy_dir / "baseline.toml").write_text("[[rule]]\nid = 'x'\n", encoding="utf-8")
    result = read_resource(tmp_path, "ananke://policy/baseline")
    assert "[[rule]]" in result


def test_policy_index_empty(tmp_path):
    result = read_resource(tmp_path, "ananke://policy/index")
    assert "no policies" in result


def test_arch_not_initialized(tmp_path):
    result = read_resource(tmp_path, "ananke://architecture/system")
    assert "not initialized" in result
