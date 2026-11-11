"""CAP-api-contract-intelligence-01/02: auth, request and response shapes per operation, normalised to the BMAD
model contract, with findings that cite their source."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from ananke.plexus import intel
from ananke.plexus.cli.app import app
from ananke.plexus.intel.api_contract import (
    ApiContractError,
    build,
    load_spec,
    stable_digest,
    to_yaml,
)

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "api-contract"


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    dest = tmp_path / "repo"
    shutil.copytree(FIXTURE, dest)
    return dest


def _report(root: Path, specs: list[Path] | None = None) -> dict:
    api = intel.analyze(root)["sections"]["api"]
    files = [p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()]
    return build(root, api, files, specs)


def _op(report: dict, method: str, path: str) -> dict:
    return next(o for o in report["operations"] if o["method"] == method and o["path"] == path)


def test_fastapi_body_model_auth_and_statuses(repo: Path) -> None:
    rep = _report(repo)
    op = _op(rep, "POST", "/orders")
    assert op["request"]["body_schema"]["value"] == "OrderIn"
    assert op["request"]["body_schema"]["status"] == "observed"
    assert [a["value"] for a in op["auth"]] == ["get_current_user"]
    assert set(op["responses"]) == {"201", "422"}
    assert op["responses"]["201"]["value"] == "OrderOut"
    # every fact cites a file and line
    assert op["auth"][0]["evidence"].startswith("svc/main.py:")
    order_in = rep["schemas"]["OrderIn"]
    assert order_in["required"] == ["customer_id", "items", "priority"]
    assert order_in["properties"]["note"] == {"type": "string", "nullable": True}
    # an inherited model gets the parent's fields, and the item model it refers to comes along
    assert {"customer_id", "id", "status"} <= set(rep["schemas"]["OrderOut"]["properties"])
    assert "Item" in rep["schemas"]


def test_query_params_and_status_constants(repo: Path) -> None:
    op = _op(_report(repo), "GET", "/orders/{order_id}")
    assert "expand" in op["request"]["query"]
    assert "order_id" in op["request"]["path"]
    assert "404" in op["responses"]


def test_flask_and_express_inferred_fields(repo: Path) -> None:
    rep = _report(repo)
    inv = _op(rep, "POST", "/invoices")
    assert [a["value"] for a in inv["auth"]] == ["login_required"]
    assert inv["request"]["body"]["amount"]["value"]["required"] is True
    assert inv["request"]["body"]["currency"]["value"]["required"] is False
    assert inv["request"]["body"]["amount"]["status"] == "inferred"
    assert {"201", "400"} <= set(inv["responses"])
    assert "q" in _op(rep, "GET", "/invoices/search")["request"]["query"]
    cart = _op(rep, "POST", "/carts")
    assert [a["value"] for a in cart["auth"]] == ["requireAuth"]
    assert set(cart["request"]["body"]) == {"sku", "quantity"}
    assert cart["request"]["body"]["quantity"]["value"]["required"] is False
    assert {"201", "400"} <= set(cart["responses"])
    assert "verbose" in _op(rep, "GET", "/carts/{id}")["request"]["query"]


def test_openapi_is_the_declared_contract_and_cycles_stop(repo: Path) -> None:
    rep = _report(repo)
    got = _op(rep, "GET", "/orders/{order_id}")
    assert got["source"] == "openapi+code"
    assert [a["value"] for a in got["auth"]] == ["bearer"]
    reports = _op(rep, "GET", "/reports")
    assert reports["source"] == "openapi"
    assert reports["evidence"] == "openapi.yaml#/paths/~1reports/get"
    assert rep["schemas"]["Order"]["properties"]["parent"] == {"$ref": "Order"}
    assert rep["schemas"]["Order"]["properties"]["lines"]["items"] == {"$ref": "Line"}
    assert _op(rep, "GET", "/status")["auth"] == []  # security: [] turns the global scheme off


def test_findings_catch_missing_errors_and_untested_auth(repo: Path) -> None:
    rep = _report(repo)
    rules = {(f["rule"], f["operation"]) for f in rep["findings"]}
    assert ("auth-without-401", "POST /carts") in rules
    assert ("untested-auth", "POST /carts") in rules
    assert ("missing-error-response", "GET /carts/{id}") in rules
    # POST /orders is called by a test that checks 401, so its auth is tested
    assert ("untested-auth", "POST /orders") not in rules
    assert all(f["evidence"] for f in rep["findings"])
    assert all(f["remediation"] for f in rep["findings"])


def test_unsupported_frameworks_are_surfaced(repo: Path) -> None:
    rep = _report(repo)
    assert rep["unsupported_frameworks"] == [
        {"framework": "Go HTTP", "evidence": "gateway/main.go:6"}
    ]
    assert any(f["rule"] == "unsupported-framework" for f in rep["findings"])


def test_auth_mismatch_between_contract_and_code(repo: Path) -> None:
    spec = repo / "openapi.yaml"
    doc = yaml.safe_load(spec.read_text())
    doc["paths"]["/carts/{id}"] = {
        "get": {
            "operationId": "getCart",
            "responses": {"200": {"description": "ok"}, "404": {"description": "x"}},
        }
    }
    spec.write_text(yaml.safe_dump(doc))
    rep = _report(repo)
    found = [f for f in rep["findings"] if f["rule"] == "auth-mismatch"]
    assert [f["operation"] for f in found] == ["GET /carts/{id}"]
    assert found[0]["status"] == "inferred"


def test_model_contract_has_the_bmad_shape(repo: Path) -> None:
    contract = _report(repo)["model_contract"]
    assert {
        "contract_id",
        "schemas",
        "error_schemas",
        "boundary_types",
        "compatibility_policy",
    } <= set(contract)
    ops = {o["operation_id"]: o for o in contract["operations"]}
    assert ops["post_orders"]["request"] == "OrderIn"
    assert ops["post_orders"]["responses"] == {"201": "OrderOut", "422": "api_error"}
    assert ops["getOrder"]["responses"]["200"] == "Order"
    names = {s["name"] for s in contract["schemas"]}
    assert {"post_carts_request", "post_invoices_request", "OrderIn", "Order", "Line"} <= names
    inferred = next(s for s in contract["schemas"] if s["name"] == "post_invoices_request")
    assert inferred["status"] == "inferred" and inferred["required"] == ["amount"]
    assert "&id" not in to_yaml(contract)


def test_same_sources_give_the_same_digest(repo: Path) -> None:
    first, second = _report(repo), _report(repo)
    assert first["digest"] == second["digest"]
    assert stable_digest({**first, "generated_at": "x"}) == first["digest"]


@pytest.mark.parametrize(
    ("name", "content", "message"),
    [
        ("api.xml", "<x/>", "unsupported format"),
        ("openapi.json", "{not json", "malformed"),
        ("openapi.yaml", "info: {title: x}\n", "isn't an OpenAPI 3 or Swagger 2"),
        ("openapi.yaml", "openapi: 3.0.0\ninfo: {title: x}\n", "no 'paths'"),
    ],
)
def test_bad_specs_give_actionable_errors(
    tmp_path: Path, name: str, content: str, message: str
) -> None:
    path = tmp_path / name
    path.write_text(content)
    with pytest.raises(ApiContractError, match=message):
        load_spec(path)


def test_missing_spec_file(tmp_path: Path) -> None:
    with pytest.raises(ApiContractError, match="doesn't exist"):
        load_spec(tmp_path / "openapi.yaml")


def test_swagger_2_body_and_definitions(tmp_path: Path) -> None:
    (tmp_path / "swagger.json").write_text(
        json.dumps(
            {
                "swagger": "2.0",
                "info": {"title": "Pets"},
                "securityDefinitions": {"key": {"type": "apiKey", "in": "header", "name": "X-Key"}},
                "definitions": {
                    "Pet": {"type": "object", "properties": {"name": {"type": "string"}}}
                },
                "paths": {
                    "/pets": {
                        "post": {
                            "security": [{"key": []}],
                            "parameters": [
                                {
                                    "in": "body",
                                    "name": "pet",
                                    "schema": {"$ref": "#/definitions/Pet"},
                                }
                            ],
                            "responses": {
                                "201": {
                                    "description": "ok",
                                    "schema": {"$ref": "#/definitions/Pet"},
                                }
                            },
                        }
                    }
                },
            }
        )
    )
    rep = _report(tmp_path)
    op = _op(rep, "POST", "/pets")
    assert op["request"]["body_schema"]["value"] == "Pet"
    assert [a["value"] for a in op["auth"]] == ["key"]
    assert rep["specs"][0]["security_schemes"] == ["key"]


def test_nothing_found_is_an_actionable_error(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("nothing here")
    with pytest.raises(ApiContractError, match="pass an OpenAPI file with --spec"):
        _report(tmp_path)


def test_cli_writes_three_files_and_fails_on_high(repo: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    runner = CliRunner()
    done = runner.invoke(app, ["triage", "api-contract", "--project", str(repo), "--out", str(out)])
    assert done.exit_code == 0, done.output
    assert {p.name for p in out.iterdir()} == {
        "api-contract.json",
        "model-contract.yaml",
        "api-contract.md",
    }
    contract = yaml.safe_load((out / "model-contract.yaml").read_text())
    assert contract["contract_id"] == "model-api-orders_api"
    blocked = runner.invoke(
        app, ["triage", "api-contract", "--project", str(repo), "--fail-on", "high", "--json"]
    )
    assert blocked.exit_code == 1
    bad = runner.invoke(
        app, ["triage", "api-contract", "--project", str(repo), "--spec", str(repo / "x.xml")]
    )
    assert bad.exit_code == 2
