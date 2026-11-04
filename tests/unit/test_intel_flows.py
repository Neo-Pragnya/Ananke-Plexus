"""End-to-end flows: every step and edge must come from the graph, the API section or the data model's usage."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

from ananke.plexus.intel import api, data_model, flows
from ananke.plexus.intel.context import RepoContext


def build(root: Path, files: dict[str, str]) -> RepoContext:
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    ctx = RepoContext.open(root)
    ctx.sections["data"] = data_model.analyze(ctx)
    ctx.sections["api"] = api.analyze(ctx)
    return ctx


def read(root: Path, files: dict[str, str]) -> dict[str, Any]:
    return flows.analyze(build(root, files))


def flow_named(section: dict[str, Any], name: str) -> dict[str, Any]:
    return next(f for f in section["flows"] if f["name"] == name)


def nodes(flow: dict[str, Any]) -> list[str]:
    return [s["node"] for s in flow["steps"]]


def edges(flow: dict[str, Any]) -> set[tuple[str, str, str]]:
    return {(e["source"], e["target"], e["kind"]) for e in flow["edges"]}


FULL = {
    "svc.py": (
        "import sqlite3\n"
        "def low():\n"
        "    sqlite3.connect('d').execute('SELECT id FROM items')\n"
        "def mid():\n"
        "    low()\n"
        "    sqlite3.connect('d').execute('INSERT INTO items (id) VALUES (1)')\n"
    ),
    "app.py": (
        "from fastapi import FastAPI\n"
        "from svc import mid\n"
        "app = FastAPI()\n"
        "@app.post('/items')\n"
        "def create():\n"
        "    return mid()\n"
        "@app.get('/quiet')\n"
        "def quiet():\n"
        "    return 1\n"
    ),
    "web/api.ts": "export function makeItem() {\n  return fetch('/items', { method: 'POST' });\n}\n",
    "web/page.ts": (
        "import { makeItem } from './api';\n"
        "export class Page {\n"
        "  submit() { return makeItem(); }\n"
        "}\n"
    ),
}


def test_a_flow_runs_from_caller_to_table(tmp_path: Path) -> None:
    section = read(tmp_path, FULL)
    flow = flow_named(section, "POST /items")
    assert flow["id"] == "flow:POST /items"
    assert flow["entry"] == "route:POST /items"
    assert flow["truncated"] is False
    assert nodes(flow) == [
        "method:web/page.ts:Page.submit",
        "function:web/api.ts:makeItem",
        "client:web/api.ts:2",
        "route:POST /items",
        "function:app.py:create",
        "function:svc.py:mid",
        "function:svc.py:low",
        "table:items",
    ]
    assert edges(flow) == {
        ("method:web/page.ts:Page.submit", "function:web/api.ts:makeItem", "calls"),
        ("function:web/api.ts:makeItem", "client:web/api.ts:2", "calls"),
        ("client:web/api.ts:2", "route:POST /items", "http"),
        ("route:POST /items", "function:app.py:create", "handles"),
        ("function:app.py:create", "function:svc.py:mid", "calls"),
        ("function:svc.py:mid", "function:svc.py:low", "calls"),
        ("function:svc.py:mid", "table:items", "writes"),
        ("function:svc.py:low", "table:items", "reads"),
    }
    kinds = {s["node"]: s["kind"] for s in flow["steps"]}
    assert kinds["client:web/api.ts:2"] == "client"
    assert kinds["route:POST /items"] == "route"
    assert kinds["function:app.py:create"] == "handler"
    assert kinds["table:items"] == "table"
    table = next(s for s in flow["steps"] if s["node"] == "table:items")
    assert table["access"] == "read_write"
    assert all(step["evidence"] for step in flow["steps"])


def test_every_edge_exists_somewhere(tmp_path: Path) -> None:
    ctx = build(tmp_path, FULL)
    section = flows.analyze(ctx)
    calls = {(e.source, e.target) for e in ctx.graph.edges if e.kind == "calls"}
    usage = {(u["symbol"], f"table:{u['table']}") for u in ctx.sections["data"]["usage"]}
    routes = {(r["id"], r["handler"]) for r in ctx.sections["api"]["routes"]}
    links = {(c["id"], c.get("route")) for c in ctx.sections["api"]["clients"]}
    callers = {(c["caller"], c["id"]) for c in ctx.sections["api"]["clients"]}
    for flow in section["flows"]:
        listed = set(nodes(flow))
        for edge in flow["edges"]:
            pair = (edge["source"], edge["target"])
            assert edge["source"] in listed and edge["target"] in listed
            allowed = {
                "calls": calls | callers,
                "reads": usage,
                "writes": usage,
                "handles": routes,
                "http": links,
            }[edge["kind"]]
            assert pair in allowed, edge
            assert edge["origin"] in {"extracted", "inferred"}


def test_a_route_with_nothing_else_is_its_route_and_handler(tmp_path: Path) -> None:
    flow = flow_named(read(tmp_path, FULL), "GET /quiet")
    assert nodes(flow) == ["route:GET /quiet", "function:app.py:quiet"]
    assert edges(flow) == {("route:GET /quiet", "function:app.py:quiet", "handles")}


def test_a_route_with_no_known_handler_is_just_its_route(tmp_path: Path) -> None:
    section = read(
        tmp_path,
        {
            "s.js": (
                "const express = require('express');\nconst app = express();\n"
                "app.get('/h', someGlobal);\n"
            )
        },
    )
    flow = flow_named(section, "GET /h")
    assert nodes(flow) == ["route:GET /h"]
    assert flow["edges"] == []


def test_cycles_end(tmp_path: Path) -> None:
    section = read(
        tmp_path,
        {
            "m.py": (
                "from fastapi import FastAPI\n"
                "app = FastAPI()\n"
                "def a():\n    b()\n"
                "def b():\n    c()\n"
                "def c():\n    a()\n"
                "@app.get('/x')\ndef x():\n    a()\n    x()\n"
            )
        },
    )
    flow = flow_named(section, "GET /x")
    assert flow["truncated"] is False
    assert nodes(flow) == [
        "route:GET /x",
        "function:m.py:x",
        "function:m.py:a",
        "function:m.py:b",
        "function:m.py:c",
    ]
    assert ("function:m.py:c", "function:m.py:a", "calls") in edges(flow)


def test_depth_budget_marks_the_flow(tmp_path: Path) -> None:
    chain = "".join(f"def f{i}():\n    f{i + 1}()\n" for i in range(flows.MAX_DEPTH + 5))
    section = read(
        tmp_path,
        {
            "m.py": (
                "from fastapi import FastAPI\napp = FastAPI()\n"
                f"{chain}def f{flows.MAX_DEPTH + 5}(): ...\n"
                "@app.get('/deep')\ndef deep():\n    f0()\n"
            )
        },
    )
    flow = flow_named(section, "GET /deep")
    assert flow["truncated"] is True
    assert f"function:m.py:f{flows.MAX_DEPTH - 1}" in nodes(flow)
    assert f"function:m.py:f{flows.MAX_DEPTH}" not in nodes(flow)
    assert any("truncated" in limit for limit in section["limits"])


def test_node_budget_marks_the_flow(tmp_path: Path) -> None:
    count = flows.MAX_NODES + 10
    leaves = "".join(f"def g{i}(): ...\n" for i in range(count))
    body = "".join(f"    g{i}()\n" for i in range(count))
    section = read(
        tmp_path,
        {
            "m.py": f"from fastapi import FastAPI\napp = FastAPI()\n{leaves}@app.get('/wide')\ndef wide():\n{body}"
        },
    )
    flow = flow_named(section, "GET /wide")
    assert flow["truncated"] is True
    assert sum(1 for n in nodes(flow) if n.startswith("function:m.py:g")) == flows.MAX_NODES


def test_a_nested_handler_only_shows_its_own_calls(tmp_path: Path) -> None:
    section = read(
        tmp_path,
        {
            "m.py": (
                "import sqlite3\n"
                "from fastapi import FastAPI\n"
                "def one(): ...\n"
                "def two(): ...\n"
                "def create():\n"
                "    app = FastAPI()\n"
                "    @app.get('/one')\n"
                "    def h1():\n"
                "        one()\n"
                "    @app.get('/two')\n"
                "    def h2():\n"
                "        two()\n"
                "        sqlite3.connect('d').execute('SELECT id FROM t2')\n"
                "    return app\n"
            )
        },
    )
    first = flow_named(section, "GET /one")
    second = flow_named(section, "GET /two")
    assert "function:m.py:one" in nodes(first)
    assert "function:m.py:two" not in nodes(first)
    assert "table:t2" not in nodes(first)
    assert "function:m.py:two" in nodes(second)
    assert "table:t2" in nodes(second)
    assert next(s for s in first["steps"] if s["kind"] == "handler")["label"] == "h1"
    assert any("inside another function" in limit for limit in section["limits"])


def test_unmatched_clients_are_on_no_flow(tmp_path: Path) -> None:
    section = read(tmp_path, {**FULL, "web/x.ts": "export const x = () => fetch('/gone');\n"})
    assert not any(n.startswith("client:web/x.ts") for f in section["flows"] for n in nodes(f))


def test_many_clients_of_one_route_mark_the_flow(tmp_path: Path) -> None:
    callers = "".join(
        f"export const c{i} = () => fetch('/items', {{ method: 'POST' }});\n"
        for i in range(flows.MAX_CLIENTS + 3)
    )
    section = read(tmp_path, {**FULL, "web/many.ts": callers})
    flow = flow_named(section, "POST /items")
    assert flow["truncated"] is True
    assert sum(1 for n in nodes(flow) if n.startswith("client:")) == flows.MAX_CLIENTS


def test_order_is_stable(tmp_path: Path) -> None:
    ctx = build(tmp_path, FULL)
    assert flows.analyze(ctx) == flows.analyze(ctx)
    names = [f["id"] for f in flows.analyze(ctx)["flows"]]
    assert names == sorted(names)


def test_no_routes_is_none_and_says_so(tmp_path: Path) -> None:
    section = read(tmp_path, {"m.py": "x = 1\n"})
    assert section["status"] == "none"
    assert section["flows"] == []
    assert section["limits"][0].startswith("no routes")


def test_a_failed_api_section_is_said(tmp_path: Path) -> None:
    ctx = build(tmp_path, {"m.py": "x = 1\n"})
    ctx.sections["api"] = {"status": "failed", "limits": ["boom"]}
    ctx.sections.pop("data")
    section = flows.analyze(ctx)
    assert section["status"] == "none"
    assert any("didn't answer" in limit for limit in section["limits"])


# ---------------------------------------------------------------- entry points


def test_entrypoints(tmp_path: Path) -> None:
    section = read(
        tmp_path,
        {
            "pyproject.toml": '[project]\nname = "p"\nversion = "1"\n[project.scripts]\ntool = "pkg.cli:main"\nother = "gone.mod:run"\n',
            "pkg/__init__.py": "",
            "pkg/work.py": "def work(): ...\n",
            "pkg/cli.py": "from .work import work\ndef main():\n    work()\n",
            "run.py": "def go(): ...\nif __name__ == '__main__':\n    go()\n",
            "not_main.py": "x = '__main__'\nif x == '__main__':\n    pass\n",
            "cmds.py": (
                "import click\n"
                "from pkg.work import work\n"
                "@click.group()\ndef cli(): ...\n"
                "@cli.command()\ndef sub():\n    work()\n"
            ),
            "bin/package.json": '{"name": "n", "bin": {"tool-js": "./bin/x.js"}}',
            "tests/test_x.py": "if __name__ == '__main__':\n    pass\n",
        },
    )
    rows = {e["id"]: e for e in section["entrypoints"]}
    assert set(rows) == {
        "entrypoint:main:run.py",
        "entrypoint:cli:tool",
        "entrypoint:cli:other",
        "entrypoint:cli:tool-js",
        "entrypoint:cli:cmds.py:sub",
    }
    assert rows["entrypoint:main:run.py"]["evidence"] == "run.py:2"
    assert rows["entrypoint:main:run.py"]["node"] == "file:run.py"
    assert rows["entrypoint:cli:tool"]["node"] == "function:pkg/cli.py:main"
    assert "function:pkg/work.py:work" in [s["node"] for s in rows["entrypoint:cli:tool"]["steps"]]
    assert rows["entrypoint:cli:other"]["node"] == "file:pyproject.toml"
    assert rows["entrypoint:cli:cmds.py:sub"]["node"] == "function:cmds.py:sub"
    assert section["status"] == "found"
    assert any("__main__ block" in limit for limit in section["limits"])
    assert any("background jobs" in limit for limit in section["limits"])


def test_broken_manifests_are_limits(tmp_path: Path) -> None:
    section = read(
        tmp_path,
        {
            "pyproject.toml": "[[[not toml",
            "package.json": "{not json",
            "a/package.json": "[1, 2]",
            "b/package.json": '{"bin": 3}',
            "bad.py": "if __name__ == '__main__':\n  def (:\n",
        },
    )
    assert any("pyproject.toml doesn't parse" in limit for limit in section["limits"])


# ---------------------------------------------------------------- hostile input


def best_of_three(ctx: RepoContext) -> float:
    times = []
    for _ in range(3):
        start = time.perf_counter()
        flows.analyze(ctx)
        times.append(time.perf_counter() - start)
    return min(times)


HOSTILE: dict[str, dict[str, str]] = {
    "one_huge_function_many_routes": {
        "m.py": "from fastapi import FastAPI\napp = FastAPI()\n"
        + "".join(f"def g{i}(): ...\n" for i in range(1200))
        + "def big():\n"
        + "".join(f"    g{i}()\n" for i in range(1200))
        + "".join(f"@app.get('/r{i}')\ndef r{i}():\n    big()\n" for i in range(800))
    },
    "everything_calls_everything_in_a_ring": {
        "m.py": "from fastapi import FastAPI\napp = FastAPI()\n"
        + "".join(f"def f{i}():\n    f{(i + 1) % 2000}()\n" for i in range(2000))
        + "@app.get('/x')\ndef x():\n    f0()\n"
    },
    "many_callers_of_a_client": {
        "a.ts": "export function target() { return fetch('/x'); }\n"
        + "".join(f"export function c{i}() {{ return target(); }}\n" for i in range(2000)),
        "m.py": "from fastapi import FastAPI\napp = FastAPI()\n@app.get('/x')\ndef x(): ...\n",
    },
    "many_scripts": {
        "pyproject.toml": "[project]\nname='p'\nversion='1'\n[project.scripts]\n"
        + "".join(f'c{i} = "m:f"\n' for i in range(4000)),
    },
    "main_blocks": {
        "m.py": "x = '__main__'\n" * 5000 + "if __name__ == '__main__':\n    pass\n",
    },
}


@pytest.mark.parametrize("name", sorted(HOSTILE))
def test_hostile_repositories_stay_fast(tmp_path: Path, name: str) -> None:
    ctx = build(tmp_path, HOSTILE[name])
    assert best_of_three(ctx) < 1.0
