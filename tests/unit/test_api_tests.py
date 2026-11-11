"""CAP-api-testing-01..04: cases and fixtures from OpenAPI, missing statuses, a local run, suggested vs executed."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ananke.plexus.api_tests import ApiTestError, example, plan, pytest_file, report, run
from ananke.plexus.cli.app import app

SPEC = {
    "openapi": "3.0.3",
    "info": {"title": "Shop", "version": "1"},
    "security": [{"bearer": []}],
    "components": {
        "schemas": {
            "Order": {
                "type": "object",
                "required": ["item", "quantity"],
                "properties": {
                    "item": {"type": "string", "example": "book"},
                    "quantity": {"type": "integer"},
                },
            }
        }
    },
    "paths": {
        "/orders/{order_id}": {
            "get": {"operationId": "getOrder", "security": [], "responses": {"200": {}}},
        },
        "/orders": {
            "post": {
                "operationId": "createOrder",
                "requestBody": {
                    "content": {
                        "application/json": {"schema": {"$ref": "#/components/schemas/Order"}}
                    }
                },
                "responses": {"201": {}, "422": {}},
            }
        },
    },
}


class Shop(BaseHTTPRequestHandler):
    def log_message(self, *args):  # quiet
        pass

    def _send(self, code: int) -> None:
        self.send_response(code)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        self._send(200 if self.path == "/orders/1" else 404)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        # it never checks credentials, so the generated "no credentials" case should fail: a real gap
        ok = isinstance(body.get("item"), str) and isinstance(body.get("quantity"), int)
        self._send(201 if ok else 422)


@pytest.fixture
def server():
    srv = HTTPServer(("127.0.0.1", 0), Shop)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def test_cases_fixtures_and_missing_statuses() -> None:
    p = plan(SPEC)
    ops = {o["name"]: o for o in p["operations"]}
    cases = [c["case"] for c in ops["createOrder"]["cases"]]
    assert cases[0] == "positive" and "no credentials" in cases and "missing item" in cases
    assert "wrong type for item" in cases and "responds within 5 seconds" in cases
    assert ops["createOrder"]["cases"][0]["body"] == {
        "item": "book",
        "quantity": 1,
    }  # example, then by type
    assert [c["case"] for c in ops["getOrder"]["cases"]] == [
        "positive",
        "unknown order_id",
        "responds within 5 seconds",
    ]
    missing = {m["operation"]: m["missing"] for m in p["missing_status_codes"]}
    assert missing == {"GET /orders/{order_id}": ["404"], "POST /orders": ["401 or 403"]}
    assert example(SPEC, {"type": "array", "items": {"type": "boolean"}}) == [True]


def test_generated_tests_run_only_against_this_machine(tmp_path: Path) -> None:
    test_file = tmp_path / "test_x.py"
    test_file.write_text(pytest_file(plan(SPEC)))
    with pytest.raises(ApiTestError, match="only against a service on this machine"):
        run(test_file, "https://api.example.com", tmp_path)


def test_a_local_run_marks_each_case_executed_or_suggested(tmp_path: Path, server: str) -> None:
    p = plan(SPEC)
    test_file = tmp_path / "test_api_contract.py"
    test_file.write_text(pytest_file(p))
    out = run(test_file, server, tmp_path)
    assert Path(out["junit"]).is_file()
    rep = report(p, out["results"])
    status = {(r["operation"], r["case"]): r["status"] for r in rep["cases"]}
    assert status[("GET /orders/{order_id}", "unknown order_id")] == "executed: passed"
    assert status[("POST /orders", "missing item")] == "executed: passed"
    assert (
        status[("POST /orders", "no credentials")] == "executed: failed"
    )  # the service doesn't check auth: a real gap
    assert all(r["status"] == "suggested" for r in report(p)["cases"])  # nothing runs unless asked


def test_cli(tmp_path: Path, server: str) -> None:
    spec = tmp_path / "openapi.json"
    spec.write_text(json.dumps(SPEC))
    runner = CliRunner()
    res = runner.invoke(app, ["triage", "api-tests", str(spec), "--out", str(tmp_path / "out")])
    assert res.exit_code == 0 and "suggested" in res.output and "documents no 404" in res.output
    res = runner.invoke(
        app,
        [
            "triage",
            "api-tests",
            str(spec),
            "--out",
            str(tmp_path / "out"),
            "--run",
            server,
            "--json",
        ],
    )
    assert res.exit_code == 1 and "executed: passed" in res.output
    bad = tmp_path / "v2.json"
    bad.write_text(json.dumps({"swagger": "2.0"}))
    assert runner.invoke(app, ["triage", "api-tests", str(bad)]).exit_code == 2
