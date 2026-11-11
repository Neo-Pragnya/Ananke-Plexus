"""CAP-multi-repository-topology-01..04: a pinned workspace, identities, calls across repositories, drift."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ananke.plexus.cli.app import app
from ananke.plexus.intel.topology import build, load_workspace, pin


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), "-c", "user.email=t@example.invalid", "-c", "user.name=t", *args],
        check=True,
    )


def _commit(root: Path, message: str) -> None:
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", message)


@pytest.fixture
def system(tmp_path: Path) -> tuple[Path, Path, Path]:
    orders = tmp_path / "orders"
    (orders / "app").mkdir(parents=True)
    (orders / "pyproject.toml").write_text('[project]\nname = "orders-service"\nversion = "1.0"\n')
    (orders / "docker-compose.yml").write_text("services:\n  orders:\n    build: .\n")
    (orders / "app" / "api.py").write_text(
        "from fastapi import FastAPI\n\napp = FastAPI()\n\n\n"
        '@app.get("/orders/{order_id}")\ndef get_order(order_id: int):\n    return {}\n'
    )
    web = tmp_path / "web"
    (web / "src").mkdir(parents=True)
    (web / "package.json").write_text('{"name": "shop-web"}')
    (web / "src" / "api.js").write_text(
        "export async function load(id) {\n  return fetch(`http://orders:8000/orders/${id}`);\n}\n"
        "export async function pay() {\n  return fetch('https://api.stripe.com/v1/charges', { method: 'POST' });\n}\n"
        "export async function gone() {\n  return fetch('http://orders:8000/refunds');\n}\n"
    )
    for repo in (orders, web):
        _git(repo, "init", "-q", "-b", "main")
        _commit(repo, "base")
    return tmp_path, orders, web


def test_identity_calls_externals_and_mismatches(system) -> None:
    home, orders, web = system
    pin(home, [orders, web])
    t = build(load_workspace(home)["roots"])
    repos = {r["name"]: r for r in t["repositories"]}
    assert repos["orders"]["packages"] == ["orders-service"] and repos["orders"]["services"] == [
        "orders"
    ]
    assert repos["web"]["packages"] == ["shop-web"] and not repos["web"]["moved"]
    (link,) = t["links"]
    assert link["from"].startswith("web:src/api.js") and link["to"].startswith("orders:app/api.py")
    assert link["route"] == "GET /orders/{order_id}" and not link["route_changed_since_pin"]
    assert [e["host"] for e in t["external"]] == ["api.stripe.com"]
    assert [m["call"] for m in t["mismatches"]] == ["GET /refunds"]


def test_a_route_changed_since_the_pin_shows_who_it_reaches(system) -> None:
    home, orders, web = system
    pin(home, [orders, web])
    api = orders / "app" / "api.py"
    api.write_text(api.read_text().replace("return {}", "return {'id': order_id}"))
    _commit(orders, "Return the id")
    t = build(load_workspace(home)["roots"])
    repos = {r["name"]: r for r in t["repositories"]}
    assert repos["orders"]["moved"] and repos["orders"]["changed_files"] == ["app/api.py"]
    assert [lk["from"].split(":")[0] for lk in t["impacted_by_changes"]] == ["web"]


def test_cli(system) -> None:
    home, orders, web = system
    runner = CliRunner()
    res = runner.invoke(
        app,
        [
            "triage",
            "topology",
            "--project",
            str(orders),
            "--project",
            str(web),
            "--pin",
            "--home",
            str(home),
            "--json",
        ],
    )
    assert res.exit_code == 0, res.output
    assert (
        len(json.loads(res.stdout)["links"]) == 1
        and (home / ".ananke" / "workspace.json").is_file()
    )
    res = runner.invoke(app, ["triage", "topology", "--home", str(home)])
    assert res.exit_code == 0 and "## Calls between repositories" in res.output
    assert runner.invoke(app, ["triage", "topology", "--home", str(home / "empty")]).exit_code == 2
