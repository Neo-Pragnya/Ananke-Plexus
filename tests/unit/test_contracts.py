"""CAP-api-contract-intelligence-03/04: routes against callers, tests and the OpenAPI file; recommended tests."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from typer.testing import CliRunner

from ananke.plexus.cli.app import app
from ananke.plexus.intel.contracts import gaps, recommend
from ananke.plexus.intel.contracts import test_calls as read_test_calls

API = {
    "routes": [
        {
            "id": "route:GET /orders/{order_id}",
            "method": "GET",
            "path": "/orders/{order_id}",
            "evidence": "api/orders.py:9",
        },
        {
            "id": "route:POST /orders",
            "method": "POST",
            "path": "/orders",
            "evidence": "api/orders.py:14",
        },
        {
            "id": "route:DELETE /orders/{order_id}",
            "method": "DELETE",
            "path": "/orders/{order_id}",
            "evidence": "api/orders.py:20",
        },
        {
            "id": "route:GET /health",
            "method": "GET",
            "path": "/health",
            "evidence": "api/main.py:3",
        },
    ],
    "clients": [
        {"route": "route:POST /orders", "evidence": "web/src/api.ts:5", "caller": "createOrder"}
    ],
    "unmatched_clients": [{"method": "GET", "path": "/invoices", "evidence": "web/src/api.ts:9"}],
    "specs": [{"path": "openapi.yaml", "missing_from_code": ["PATCH /orders/{id}"]}],
    "limits": [],
}


def _repo(tmp_path: Path) -> list[str]:
    (tmp_path / "tests").mkdir()
    (tmp_path / "web").mkdir()
    (tmp_path / "tests" / "test_orders.py").write_text(
        "def test_get(client):\n"
        "    assert client.get('/orders/42').status_code == 200\n"
        '    client.post(f"{BASE}/orders", json={})\n'
        "    client.get('/nowhere')\n"
    )
    (tmp_path / "web" / "orders.test.ts").write_text(
        "await fetch(`/orders/${id}`, { method: 'DELETE' })\n"
    )
    (tmp_path / "web" / "app.ts").write_text("client.get('/orders/1')\n")  # not a test: not counted
    return ["tests/test_orders.py", "web/orders.test.ts", "web/app.ts"]


def test_test_calls_are_read_from_test_files_only(tmp_path: Path) -> None:
    calls = read_test_calls(tmp_path, _repo(tmp_path))
    assert {(c["method"], c["path"]) for c in calls} == {
        ("GET", "/orders/42"),
        ("POST", "{BASE}/orders"),
        ("GET", "/nowhere"),
        ("DELETE", "/orders/${id}"),
    }
    assert all(not c["evidence"].startswith("web/app.ts") for c in calls)


def test_routes_are_matched_to_tests_by_method_and_shape(tmp_path: Path) -> None:
    out = gaps(tmp_path, API, _repo(tmp_path))
    rows = {r["id"]: r for r in out["routes"]}
    assert rows["route:GET /orders/{order_id}"]["tested_by"] == ["tests/test_orders.py:2"]
    assert rows["route:POST /orders"]["tested_by"] == [
        "tests/test_orders.py:3"
    ]  # the base in an f-string is dropped
    assert rows["route:DELETE /orders/{order_id}"]["tested_by"] == ["web/orders.test.ts:1"]
    assert out["gaps"]["untested_routes"] == ["route:GET /health"]
    assert "route:POST /orders" not in out["gaps"]["routes_nothing_calls"]
    assert [c["path"] for c in out["gaps"]["test_calls_without_route"]] == ["/nowhere"]
    assert out["gaps"]["calls_without_route"][0]["path"] == "/invoices"
    assert out["gaps"]["spec_operations_missing"] == [
        {"spec": "openapi.yaml", "operation": "PATCH /orders/{id}"}
    ]
    assert out["summary"]["tested"] == 3 and out["summary"]["untested"] == 1


def test_recommendations_follow_the_route() -> None:
    names = [
        t["name"]
        for t in recommend({"method": "PUT", "path": "/orders/{order_id}/items/{item_id}"})
    ]
    assert names == [
        "test_put_orders_order_id_items_item_id_succeeds",
        "test_put_orders_order_id_items_item_id_unknown_order_id",
        "test_put_orders_order_id_items_item_id_unknown_item_id",
        "test_put_orders_order_id_items_item_id_rejects_invalid_body",
    ]
    assert len(recommend({"method": "GET", "path": "/health"})) == 1


def test_cli_on_a_real_project(tmp_path: Path) -> None:
    shop = Path(__file__).resolve().parents[3] / "Dev-Guru" / "acceptance" / "fixtures" / "shop"
    if (
        not shop.is_dir()
    ):  # the fixture lives in the sibling Dev Guru repository; CI uses the synthetic cases above
        return
    root = tmp_path / "shop"
    shutil.copytree(shop, root)
    for pinned in root.rglob("*.pins"):
        pinned.rename(pinned.with_suffix(""))
    (root / "tests" / "test_api.py").write_text(
        "def test_c(client):\n    client.get('/api/customers/7')\n"
    )
    res = CliRunner().invoke(app, ["triage", "contracts", "--project", str(root), "--json"])
    assert res.exit_code == 0, res.output
    out = json.loads(res.stdout)
    tested = {r["id"]: r["tested_by"] for r in out["routes"]}
    assert tested["route:GET /api/customers/{customer_id}"] == ["tests/test_api.py:2"]
    assert out["summary"]["untested"] == out["summary"]["routes"] - 1
