"""ASM-A09: typed connectors run in their own process, and only verified output is accepted."""

from __future__ import annotations

import dataclasses
import json
import subprocess
from pathlib import Path

import pytest
from registry_support import open_registry

from ananke.plexus import connectors
from ananke.plexus.assembly import gaps
from ananke.plexus.connectors import ConnectorError, run

JUNIT = """<?xml version="1.0"?>
<testsuite name="shop" tests="3">
  <testcase classname="tests.test_cart" name="test_total" file="tests/test_cart.py" line="12"/>
  <testcase classname="tests.test_cart" name="test_discount" file="tests/test_cart.py" line="20">
    <failure message="assert 90 == 80">Traceback...</failure>
  </testcase>
  <testcase classname="tests.test_api" name="test_slow"><skipped message="needs a database"/></testcase>
</testsuite>
"""
OPENAPI = {
    "openapi": "3.0.3",
    "info": {"title": "Shop", "version": "1.0"},
    "paths": {
        "/orders/{id}": {
            "parameters": [{"name": "id", "in": "path"}],
            "get": {"operationId": "getOrder", "responses": {"200": {}, "404": {}}},
            "delete": {"operationId": "deleteOrder", "responses": {"204": {}}},
        }
    },
}
JENKINS = """Started by user admin
[Pipeline] stage
2026-11-10T10:00:00Z ERROR: npm test failed
2026-11-10T10:00:01Z ERROR: npm test failed
Traceback (most recent call last):
Process exited with code 137
Finished: FAILURE
"""


def test_junit_becomes_verified_sarif(tmp_path: Path) -> None:
    (tmp_path / "r.xml").write_text(JUNIT)
    out = run("junit-to-sarif", tmp_path / "r.xml")
    results = out["runs"][0]["results"]
    assert out["version"] == "2.1.0" and out["runs"][0]["properties"]["tests"] == 3
    assert [r["ruleId"] for r in results] == ["test-failure", "test-skipped"]
    loc = results[0]["locations"][0]["physicalLocation"]
    assert loc == {"artifactLocation": {"uri": "tests/test_cart.py"}, "region": {"startLine": 20}}
    assert "assert 90 == 80" in results[0]["message"]["text"]


def test_openapi_becomes_routes(tmp_path: Path) -> None:
    (tmp_path / "api.json").write_text(json.dumps(OPENAPI))
    out = run("openapi-routes", tmp_path / "api.json")
    assert [(r["method"], r["path"]) for r in out["routes"]] == [
        ("GET", "/orders/{id}"),
        ("DELETE", "/orders/{id}"),
    ]
    assert out["routes"][0]["parameters"] == ["id"] and out["routes"][0]["responses"] == [
        "200",
        "404",
    ]
    (tmp_path / "v2.json").write_text(json.dumps({"swagger": "2.0", "paths": {}}))
    with pytest.raises(ConnectorError) as exc:
        run("openapi-routes", tmp_path / "v2.json")
    assert exc.value.code == "INVALID_INPUT" and "OpenAPI 3" in exc.value.message


def test_a_log_is_triaged_into_grouped_findings(tmp_path: Path) -> None:
    (tmp_path / "console.log").write_text(JENKINS)
    out = run("log-triage", tmp_path / "console.log")
    assert out["source"] == "jenkins"
    rules = {f["rule"]: f for f in out["findings"]}
    assert rules["error-line"]["count"] == 2 and rules["error-line"]["lines"] == [
        3,
        4,
    ]  # timestamps don't split it
    assert {"python-traceback", "exit-code", "build-failed"} <= set(rules)


def test_the_code_graph_connector_reads_without_writing(tmp_path: Path) -> None:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "a.py").write_text("from pkg import b\n\n\ndef f():\n    return b.g()\n")
    (tmp_path / "pkg" / "b.py").write_text("def g():\n    return 1\n")
    before = sorted(p.name for p in tmp_path.rglob("*"))
    out = run("code-graph", tmp_path)
    assert out["schema"] == "code-graph/v1" and any(n["kind"] == "file" for n in out["nodes"])
    assert sorted(p.name for p in tmp_path.rglob("*")) == before  # no .ananke, nothing written


def test_each_connector_runs_in_its_own_isolated_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = {}
    real = subprocess.run

    def spy(argv, **kw):
        seen["argv"], seen["env"] = argv, kw.get("env")
        return real(argv, **kw)

    monkeypatch.setenv("ANANKE_TEST_SECRET", "x")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid")
    monkeypatch.setattr(connectors.subprocess, "run", spy)
    (tmp_path / "r.xml").write_text(JUNIT)
    run("junit-to-sarif", tmp_path / "r.xml")
    assert seen["argv"][1:4] == ["-I", "-m", "ananke.plexus.connectors"]
    assert "ANANKE_TEST_SECRET" not in seen["env"] and "HTTPS_PROXY" not in seen["env"]


def test_output_that_fails_its_check_is_an_error_never_a_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "r.xml").write_text(JUNIT)
    strict = dataclasses.replace(
        connectors.CONNECTORS["junit-to-sarif"], verify=lambda out: ["refused for the test"]
    )
    monkeypatch.setitem(connectors.CONNECTORS, "junit-to-sarif", strict)
    with pytest.raises(ConnectorError) as exc:
        run("junit-to-sarif", tmp_path / "r.xml")
    assert exc.value.code == "UNVERIFIED_OUTPUT"
    assert connectors._verify_sarif({"version": "2.1.0", "runs": [{"tool": {"driver": {}}}]})
    assert connectors._verify_routes(
        {"schema": "api-routes/v1", "routes": [{"method": "FETCH", "path": "x"}]}
    )


def test_bad_input_is_refused_with_a_code(tmp_path: Path) -> None:
    (tmp_path / "xxe.xml").write_text(
        '<!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]><testsuite>&e;</testsuite>'
    )
    (tmp_path / "empty.xml").write_text("<testsuite/>")
    for name, src, code in [
        ("junit-to-sarif", tmp_path / "xxe.xml", "INVALID_INPUT"),
        ("junit-to-sarif", tmp_path / "empty.xml", "INVALID_INPUT"),
        ("junit-to-sarif", tmp_path / "missing.xml", "INVALID_INPUT"),
        ("code-graph", tmp_path / "empty.xml", "INVALID_INPUT"),
        ("nope", tmp_path, "UNKNOWN_CONNECTOR"),
    ]:
        with pytest.raises(ConnectorError) as exc:
            run(name, src)
        assert exc.value.code == code, (name, exc.value.message)


def test_registered_connectors_fill_workflow_gaps(tmp_path: Path) -> None:
    reg = open_registry(tmp_path)
    refs = connectors.register(reg, approve=True)
    assert refs == [f"ananke/{n}@1.0.0" for n in connectors.CONNECTORS]
    assert connectors.register(reg) == refs  # registering again changes nothing
    draft = {
        "schema_version": "1.0.0",
        "workflow_id": "demo/ci-triage",
        "nodes": [
            {
                "id": "triage",
                "capability_ref": "ananke/log-triage@1.0.0",
                "kind": "tool",
                "inputs": [{"name": "log", "schema_ref": "build-log/v1"}],
                "outputs": [{"name": "findings", "schema_ref": "findings/v1"}],
            },
            {
                "id": "report",
                "capability_ref": "ananke/junit-to-sarif@1.0.0",
                "kind": "tool",
                "inputs": [{"name": "junit", "schema_ref": "findings/v1", "required": True}],
                "outputs": [{"name": "sarif", "schema_ref": "sarif/2.1.0"}],
            },
        ],
        "edges": [],
    }
    out = gaps(draft, reg)
    (gap,) = out["gaps"]
    assert gap["node"] == "report" and [r["ref"] for r in gap["authorized"]] == [
        "ananke/log-triage@1.0.0"
    ]
