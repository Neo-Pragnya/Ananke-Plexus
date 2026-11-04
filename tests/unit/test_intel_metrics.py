"""The overview, CALM, quality, coverage and dependency analyzers: the golden repo's answers, edge cases and hostile input."""

from __future__ import annotations

import ast
import json
import shutil
import time
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from ananke.plexus.architecture.calm import validate_calm
from ananke.plexus.intel import analyze, calm, coverage, dependencies, overview, quality
from ananke.plexus.intel.context import RepoContext

SHOP = Path(__file__).resolve().parent.parent / "fixtures" / "repo-mapping" / "shop"
KEY = json.loads((SHOP / "expected.json").read_text())
FAST_SECONDS = 2.0


def write(root: Path, files: dict[str, str | bytes]) -> Path:
    for name, content in files.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            target.write_bytes(content)
        else:
            target.write_text(content)
    return root


def open_repo(root: Path, **kwargs: Any) -> RepoContext:
    return RepoContext.open(root, **kwargs)


def open_repo_without_graph(root: Path) -> RepoContext:
    files = [p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()]
    return RepoContext(root.resolve(), sorted(files), SimpleNamespace(edges=[]))


def components_of(root: Path) -> dict[str, dict[str, Any]]:
    return {c["name"]: c for c in overview.analyze(open_repo(root))["components"]}


@pytest.fixture(scope="module")
def shop(tmp_path_factory: pytest.TempPathFactory) -> Path:
    copy = tmp_path_factory.mktemp("repo") / "shop"
    shutil.copytree(SHOP, copy, ignore=shutil.ignore_patterns("expected.json"))
    return copy


@pytest.fixture(scope="module")
def sections(shop: Path) -> dict[str, Any]:
    return analyze(shop)["sections"]


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    calls: list[Any] = []

    def refuse(*args: Any, **kwargs: Any) -> Any:
        calls.append(args)
        raise AssertionError("the network was used")

    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    return calls


class Response:
    def __init__(self, payload: Any) -> None:
        self.raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()

    def __enter__(self) -> Response:
        return self

    def __exit__(self, *args: Any) -> None:
        return None

    def read(self, size: int = -1) -> bytes:
        return self.raw[:size] if size >= 0 else self.raw


# Golden repo


def test_golden_components_have_their_kinds(sections: dict[str, Any]) -> None:
    found = {c["name"]: c["kind"] for c in sections["overview"]["components"]}
    for name, kind in KEY["components"].items():
        assert found[name] == kind
    # The key lists what runs. A notebooks and a tests folder are components too.
    assert found["notebooks"] == "notebooks"
    assert found["tests"] == "tests"


def test_golden_components_say_why(sections: dict[str, Any]) -> None:
    by_name = {c["name"]: c for c in sections["overview"]["components"]}
    assert "worker" in by_name["worker"]["evidence"]
    assert "postgres" in by_name["db"]["evidence"]
    assert "docker-compose.yml:11" in by_name["db"]["evidence"]
    assert "fastapi" in by_name["backend"]["evidence"]
    assert all(c["id"] == f"component:{c['name']}" for c in by_name.values())
    assert by_name["db"]["language"] == "sql"
    assert by_name["db"]["path"] == "db"


def test_golden_languages_and_source_roots(sections: dict[str, Any]) -> None:
    summary = sections["overview"]
    assert set(summary["languages"]) == set(KEY["languages"])
    assert summary["source_roots"] == KEY["source_roots"]
    assert summary["files"] > summary["languages"]["python"] > 0
    assert summary["loc"] > 0


def test_golden_quality(sections: dict[str, Any]) -> None:
    section = sections["quality"]
    assert section["status"] == "found"
    assert KEY["quality"]["hotspot_includes"] in {h["symbol"] for h in section["hotspots"]}
    assert section["totals"]["test_functions"] == KEY["quality"]["test_functions"]
    assert section["totals"]["test_files"] == KEY["quality"]["test_files"]
    assert "tests/test_orders.py" not in {f["path"] for f in section["files"]}
    assert section["hotspots"][0]["complexity"] == 4
    assert any("estimate" in limit for limit in section["limits"])


def test_golden_coverage(sections: dict[str, Any]) -> None:
    section = sections["coverage"]
    assert section["status"] == "found"
    assert section["source"] == "coverage.xml"
    assert section["line_rate"] == KEY["coverage"]["line_rate"]
    assert section["branch_rate"] == KEY["coverage"]["branch_rate"]
    path, rate = KEY["coverage"]["file"]
    by_path = {f["path"]: f for f in section["files"]}
    assert by_path[path]["line_rate"] == rate
    assert (by_path[path]["lines_covered"], by_path[path]["lines_total"]) == (3, 4)


# Overview


def test_flat_package_is_one_component(tmp_path: Path) -> None:
    write(
        tmp_path,
        {
            "pyproject.toml": '[project]\nname = "tiny"\ndependencies = ["click"]\n',
            "tiny/__init__.py": "",
            "tiny/core.py": "def f():\n    return 1\n",
        },
    )
    found = overview.analyze(open_repo(tmp_path))
    assert [(c["name"], c["path"], c["kind"]) for c in found["components"]] == [
        ("tiny", ".", "library")
    ]
    assert found["components"][0]["files"] == 2
    assert found["source_roots"] == ["."]


def test_loose_scripts_with_no_manifest_are_one_component(tmp_path: Path) -> None:
    write(tmp_path, {"a.py": "x = 1\n", "b.py": "y = 2\n"})
    found = overview.analyze(open_repo(tmp_path))["components"]
    assert len(found) == 1
    assert found[0]["path"] == "."
    assert found[0]["kind"] == "library"


def test_empty_repo_has_nothing(tmp_path: Path) -> None:
    section = overview.analyze(open_repo(tmp_path))
    assert section["status"] == "none"
    assert section["components"] == []


def test_a_worker_needs_a_signal(tmp_path: Path) -> None:
    write(
        tmp_path,
        {
            "tasks/requirements.txt": "celery==5.3.0\n",
            "tasks/run.py": "x = 1\n",
            "job-board/requirements.txt": "click==8.0.0\n",
            "job-board/run.py": "x = 1\n",
            "api/requirements.txt": "flask==3.0.0\ncelery==5.3.0\n",
            "api/run.py": "x = 1\n",
        },
    )
    found = components_of(tmp_path)
    assert found["tasks"]["kind"] == "worker"
    assert "celery" in found["tasks"]["evidence"]
    assert found["job-board"]["kind"] == "library"
    assert found["api"]["kind"] == "service"


def test_frontend_needs_a_signal(tmp_path: Path) -> None:
    write(
        tmp_path,
        {
            "app/package.json": '{"dependencies": {"react": "18.0.0"}}',
            "app/index.js": "export const a = 1;\n",
            "client/client.py": "x = 1\n",
            "client/requirements.txt": "httpx==0.27.0\n",
            "server/package.json": '{"dependencies": {"express": "4.0.0"}}',
            "server/index.js": "export const a = 1;\n",
        },
    )
    found = components_of(tmp_path)
    assert found["app"]["kind"] == "frontend"
    assert found["client"]["kind"] == "library"
    assert found["server"]["kind"] == "service"


def test_dockerfile_means_service(tmp_path: Path) -> None:
    write(tmp_path, {"svc/Dockerfile": "FROM scratch\n", "svc/main.go": "package main\n"})
    found = components_of(tmp_path)["svc"]
    assert found["kind"] == "service"
    assert found["language"] == "go"
    assert "svc/Dockerfile" in found["evidence"]


def test_compose_context_with_nested_dockerfile(tmp_path: Path) -> None:
    write(
        tmp_path,
        {
            "docker-compose.yml": "services:\n  api:\n    build:\n      context: .\n      dockerfile: services/api/Dockerfile\n",
            "services/api/Dockerfile": "FROM scratch\n",
            "services/api/main.py": "x = 1\n",
        },
    )
    assert components_of(tmp_path)["api"]["path"] == "services/api"


def test_compose_building_outside_the_repo_is_a_limit(tmp_path: Path) -> None:
    write(tmp_path, {"docker-compose.yml": "services:\n  api:\n    build: ../../elsewhere\n"})
    section = overview.analyze(open_repo(tmp_path))
    assert any("outside the repository" in limit for limit in section["limits"])
    assert all(".." not in c["path"] for c in section["components"])


def test_dev_override_doesnt_duplicate_a_service(tmp_path: Path) -> None:
    write(
        tmp_path,
        {
            "docker-compose.yml": "services:\n  api:\n    build: ./api\n",
            "docker-compose.dev.yml": "services:\n  api-dev:\n    build: ./api\n",
            "api/main.py": "x = 1\n",
        },
    )
    assert set(components_of(tmp_path)) == {"api"}


def test_tool_images_arent_databases() -> None:
    assert overview.is_database_image("postgres:16-alpine")
    assert overview.is_database_image("bitnami/redis:7")
    assert not overview.is_database_image("rediscommander/redis-commander:latest")
    assert not overview.is_database_image("nginx:1")
    assert not overview.is_database_image(None)


def test_malformed_compose_is_a_limit(tmp_path: Path) -> None:
    write(tmp_path, {"docker-compose.yml": "services: [unclosed\n  - : :\n", "a.py": "x = 1\n"})
    section = overview.analyze(open_repo(tmp_path))
    assert section["status"] == "found"
    assert any("docker-compose.yml couldn't be read" in limit for limit in section["limits"])


def test_yaml_alias_bomb_in_compose_is_harmless(tmp_path: Path) -> None:
    bomb = ["a0: &a0 [x, x, x, x, x, x, x, x, x]"]
    bomb += [f"a{i}: &a{i} [{', '.join([f'*a{i - 1}'] * 9)}]" for i in range(1, 12)]
    bomb += [
        "services:",
        "  api:",
        "    command: [*a11]",
        "    depends_on: *a11",
        "    image: *a11",
    ]
    write(tmp_path, {"docker-compose.yml": "\n".join(bomb) + "\n"})
    started = time.monotonic()
    section = overview.analyze(open_repo(tmp_path))
    assert time.monotonic() - started < FAST_SECONDS
    assert section["components"][0]["name"] == "api"


def test_fixtures_arent_components_or_counted(tmp_path: Path) -> None:
    write(
        tmp_path,
        {
            "a.py": "x = 1\n",
            "tests/fixtures/app/docker-compose.yml": "services:\n  ghost:\n    image: postgres:16\n",
            "tests/fixtures/app/code.ts": "export const a = 1;\n",
            "tests/test_a.py": "def test_a():\n    pass\n",
        },
    )
    section = overview.analyze(open_repo(tmp_path))
    assert "ghost" not in {c["name"] for c in section["components"]}
    assert "typescript" not in section["languages"]
    assert any("fixture" in limit for limit in section["limits"])


def test_folder_of_separate_projects_isnt_a_component(tmp_path: Path) -> None:
    files: dict[str, str | bytes] = {
        "pyproject.toml": '[project]\nname = "lib"\n',
        "src/lib/__init__.py": "def f():\n    pass\n",
    }
    for i in range(3):
        files[f"collected/items/p{i}/requirements.txt"] = "click==8.0.0\n"
        files[f"collected/items/p{i}/run.py"] = "if True:\n    pass\n"
    write(tmp_path, files)
    ctx = open_repo(tmp_path)
    ctx.sections["overview"] = overview.analyze(ctx)
    assert ctx.sections["overview"]["separate_projects"] == ["collected"]
    root = next(c for c in ctx.sections["overview"]["components"] if c["path"] == ".")
    assert root["files"] == 1
    measured = quality.analyze(ctx)
    assert {f["path"] for f in measured["files"]} == {"src/lib/__init__.py"}
    assert any("separate projects" in limit for limit in measured["limits"])
    # Their dependencies are still real.
    assert {p["manifest"] for p in dependencies.analyze(ctx)["packages"]} == {
        f"collected/items/p{i}/requirements.txt" for i in range(3)
    }


def test_malformed_notebook_is_a_limit(tmp_path: Path) -> None:
    write(tmp_path, {"nb.ipynb": "{not json"})
    section = overview.analyze(open_repo(tmp_path))
    assert any("nb.ipynb couldn't be read" in limit for limit in section["limits"])


def test_deeply_nested_notebook_is_a_limit(tmp_path: Path) -> None:
    write(tmp_path, {"nb.ipynb": "[" * 200_000})
    limits: list[str] = []
    assert overview._notebook(open_repo_without_graph(tmp_path), "nb.ipynb", limits) == (
        "python",
        0,
    )
    assert any("nb.ipynb" in limit for limit in limits)


def test_notebook_only_repo(tmp_path: Path) -> None:
    cell = {"cell_type": "code", "source": ["a = 1\n", "b = 2\n"]}
    write(
        tmp_path,
        {"nb.ipynb": json.dumps({"metadata": {"kernelspec": {"language": "R"}}, "cells": [cell]})},
    )
    section = overview.analyze(open_repo(tmp_path))
    assert section["languages"] == {"r": 1}
    assert section["components"][0]["kind"] == "notebooks"
    assert section["components"][0]["loc"] == 2


# CALM


def test_golden_calm_shape(sections: dict[str, Any]) -> None:
    section = sections["calm"]
    assert section["status"] == "found"
    assert section["origin"] == "inferred"
    model = section["model"]
    assert model["schema_version"]
    assert model["system"]["name"] == "shop"
    names = {c["name"] for c in model["components"]}
    assert {"frontend", "backend", "worker", "db"} <= names
    assert "tests" not in names
    assert {(r["source"], r["target"]) for r in model["relationships"]} == {
        tuple(pair) for pair in KEY["calm_relationships"]
    }
    for relation in model["relationships"]:
        assert relation["type"]
        assert relation["evidence"].startswith("docker-compose.yml:")
    by_pair = {(r["source"], r["target"]): r for r in model["relationships"]}
    assert by_pair[("frontend", "backend")]["evidence"] == "docker-compose.yml:7"
    assert by_pair[("backend", "db")]["type"] == "data"


def test_inferred_model_passes_validate_calm(shop: Path, tmp_path: Path) -> None:
    copy = tmp_path / "shop"
    shutil.copytree(shop, copy)
    model = analyze(copy)["sections"]["calm"]["model"]
    target = copy / ".ananke" / "architecture" / "system.calm.json"
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps(model))
    result = validate_calm(copy)
    assert result["ok"], result["issues"]
    assert result["relationship_count"] == len(KEY["calm_relationships"])


def test_calm_writes_nothing(tmp_path: Path) -> None:
    write(tmp_path, {"a.py": "x = 1\n"})
    before = sorted(p.as_posix() for p in tmp_path.rglob("*"))
    analyze(tmp_path)
    assert sorted(p.as_posix() for p in tmp_path.rglob("*")) == before
    assert not (tmp_path / ".ananke").exists()


def test_declared_model_gives_drift_and_stays_untouched(tmp_path: Path) -> None:
    declared = {
        "schema_version": "0.1",
        "system": {"name": "x"},
        "components": [{"name": "api"}, {"name": "legacy"}],
        "relationships": [{"source": "legacy", "target": "api", "kind": "http"}],
    }
    text = json.dumps(declared)
    write(
        tmp_path,
        {
            ".ananke/architecture/system.calm.json": text,
            "docker-compose.yml": "services:\n  api:\n    build: ./api\n    depends_on: [db]\n  db:\n    image: mysql:8\n",
            "api/main.py": "x = 1\n",
        },
    )
    section = analyze(tmp_path)["sections"]["calm"]
    drift = section["drift"]
    assert drift["components_only_declared"] == ["legacy"]
    assert drift["components_only_inferred"] == ["db"]
    assert drift["relationships_only_declared"] == [["legacy", "api"]]
    assert drift["relationships_only_inferred"] == [["api", "db"]]
    assert (tmp_path / ".ananke/architecture/system.calm.json").read_text() == text


def test_malformed_declared_model_is_a_limit(tmp_path: Path) -> None:
    write(tmp_path, {".ananke/architecture/system.calm.json": "{nope", "a.py": "x = 1\n"})
    section = analyze(tmp_path)["sections"]["calm"]
    assert section["status"] == "found"
    assert "drift" not in section
    assert any("couldn't be read" in limit for limit in section["limits"])


def test_calm_with_nothing_is_none(tmp_path: Path) -> None:
    section = analyze(tmp_path)["sections"]["calm"]
    assert section["status"] == "none"
    assert "model" not in section


def _edge(source: str, target: str, **more: Any) -> SimpleNamespace:
    return SimpleNamespace(source=source, target=target, kind="imports", source_location="", **more)


def test_imports_across_components_are_links(tmp_path: Path) -> None:
    write(
        tmp_path,
        {
            "alpha/requirements.txt": "click==8.0.0\n",
            "alpha/a.py": "x = 1\n",
            "beta/requirements.txt": "click==8.0.0\n",
            "beta/util/b.py": "y = 2\n",
        },
    )
    # With the graph's resolved target.
    ctx = open_repo(tmp_path)
    ctx.graph = SimpleNamespace(
        edges=[_edge("file:alpha/a.py", "module:b", resolved_target="file:beta/util/b.py")]
    )
    relations = calm.analyze(ctx)["model"]["relationships"]
    assert [(r["source"], r["target"], r["type"]) for r in relations] == [
        ("alpha", "beta", "imports")
    ]
    # And without one, by a dotted name that only one repository file has.
    ctx.graph = SimpleNamespace(edges=[_edge("file:alpha/a.py", "module:util.b")])
    relations = calm.analyze(ctx)["model"]["relationships"]
    assert [(r["source"], r["target"]) for r in relations] == [("alpha", "beta")]
    # A third party's name is no link.
    ctx.graph = SimpleNamespace(edges=[_edge("file:alpha/a.py", "module:os.path")])
    assert calm.analyze(ctx)["model"]["relationships"] == []


# Quality


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("pass", 1),
        ("if a:\n        pass", 2),
        ("if a:\n        pass\n    elif b:\n        pass\n    else:\n        pass", 3),
        ("for x in a:\n        pass", 2),
        ("while a:\n        pass", 2),
        ("try:\n        pass\n    except A:\n        pass\n    except B:\n        pass", 3),
        ("return a and b and c", 3),
        ("return a or (b and c)", 3),
        ("return [x for x in a]", 1),
        ("return [x for x in a if x if y]", 3),
        ("return a if b else c", 2),
        ("return lambda: a if b else c", 2),
        ("match a:\n        case 1:\n            pass\n        case _:\n            pass", 2),
        ("match a:\n        case 1 if b:\n            pass\n        case 2:\n            pass", 4),
        ("def inner():\n        if a:\n            pass\n    return inner", 1),
    ],
)
def test_complexity_counts_branches(body: str, expected: int) -> None:
    tree = ast.parse(f"def f():\n    {body}\n")
    assert quality.complexity_of(tree.body[0]) == expected


def test_symbols_lengths_and_documentation(tmp_path: Path) -> None:
    source = '''"""Module."""


def plain():
    """Documented."""
    if a:
        return 1
    return 2


class Thing:
    """Documented."""

    def method(self):
        if self:
            return 1

    async def other(self):
        def inner():
            if a:
                return 1
        return inner
'''
    write(tmp_path, {"pkg/mod.py": source})
    section = quality.analyze(open_repo(tmp_path))
    symbols = {h["symbol"]: h for h in section["hotspots"]}
    assert set(symbols) == {
        "function:pkg/mod.py:plain",
        "method:pkg/mod.py:Thing.method",
        "function:pkg/mod.py:Thing.other.inner",
    }
    assert symbols["function:pkg/mod.py:plain"]["line"] == 4
    assert symbols["function:pkg/mod.py:plain"]["length"] == 5
    totals = section["totals"]
    assert totals["functions"] == 4
    assert totals["classes"] == 1
    assert totals["documented_ratio"] == round(2 / 5, 4)
    assert totals["max_complexity"] == 2
    assert section["files"] == [
        {"path": "pkg/mod.py", "loc": 16, "functions": 4, "max_complexity": 2}
    ]


def test_tests_are_counted_apart(tmp_path: Path) -> None:
    write(
        tmp_path,
        {
            "app.py": "def real():\n    pass\n",
            "test_top.py": "def test_a():\n    pass\n\ndef helper():\n    pass\n",
            "pkg/thing_test.py": "class TestThing:\n    def test_b(self):\n        pass\n",
            "tests/helpers.py": "def not_a_test():\n    if 1:\n        pass\n",
            "web/a.test.ts": "it('x', () => {});\ntest('y', () => {});\n",
            "web/b.spec.ts": "test.skip('z', () => {});\n",
            "src/app/spec/real.py": "def still_code():\n    pass\n",
            "src/app/tests/t.py": "def test_c():\n    pass\n",
        },
    )
    section = quality.analyze(open_repo(tmp_path))
    assert section["totals"]["test_files"] == 6
    assert section["totals"]["test_functions"] == 6
    assert {f["path"] for f in section["files"]} == {"app.py", "src/app/spec/real.py"}
    assert section["totals"]["functions"] == 2
    assert section["hotspots"] == []


def test_malformed_python_is_a_limit(tmp_path: Path) -> None:
    write(tmp_path, {"bad.py": "def broken(:\n", "good.py": "def ok():\n    pass\n"})
    section = quality.analyze(open_repo(tmp_path))
    assert section["status"] == "found"
    assert any("bad.py" in limit for limit in section["limits"])
    assert [f["path"] for f in section["files"]] == ["good.py"]


def test_deeply_nested_python_is_a_limit(tmp_path: Path) -> None:
    write(tmp_path, {"deep.py": "x = " + "(" * 100_000 + ")" * 100_000 + "\n"})
    section = quality.analyze(open_repo(tmp_path))
    assert any("deep.py" in limit for limit in section["limits"])


def test_scripts_are_estimated_and_say_so(tmp_path: Path) -> None:
    source = """// if if if if in a comment
export function load(a: number) {
  const text = "if (a) && b || c";
  if (a > 1 && a < 5) {
    return a ? 1 : 2;
  }
  return a?.b ?? 3;
}

export const short = (x) => x + 1;

export class Page {
  private count: number;

  async submit(order: object) {
    for (const a of []) {
      if (a) { return 1; }
    }
  }
}
"""
    write(tmp_path, {"src/page.ts": source})
    section = quality.analyze(open_repo(tmp_path))
    symbols = {h["symbol"]: h for h in section["hotspots"]}
    load = symbols["function:src/page.ts:load"]
    assert load["complexity"] == 5  # 1, if, &&, ternary, ??
    assert load["estimated"] is True
    assert load["line"] == 2
    assert load["length"] == 7
    assert symbols["method:src/page.ts:Page.submit"]["complexity"] == 3
    assert section["totals"]["functions"] == 3
    assert section["totals"]["classes"] == 1
    assert any("estimate" in limit for limit in section["limits"])


def test_minified_scripts_are_skipped_with_a_limit(tmp_path: Path) -> None:
    write(tmp_path, {"bundle.js": "var a=1;" * 2000})
    section = quality.analyze(open_repo(tmp_path))
    assert any("minified" in limit for limit in section["limits"])
    assert section["files"] == []


def test_other_languages_are_a_limit_not_a_zero(tmp_path: Path) -> None:
    write(tmp_path, {"main.go": "package main\n"})
    section = quality.analyze(open_repo(tmp_path))
    assert section["status"] == "none"
    assert any("1 go" in limit for limit in section["limits"])


# Coverage


COBERTURA = """<?xml version="1.0" ?>
<coverage line-rate="0.5" branch-rate="0" branches-valid="0">
  <sources><source>/somewhere/else/src</source></sources>
  <packages><package><classes>
    <class filename="pkg/a.py"><lines><line number="1" hits="2"/><line number="2" hits="0"/></lines></class>
  </classes></package></packages>
</coverage>
"""


def test_no_report_is_not_found_and_never_zero(tmp_path: Path) -> None:
    write(tmp_path, {"a.py": "x = 1\n"})
    section = coverage.analyze(open_repo(tmp_path))
    assert section["status"] == "not_found"
    assert section["line_rate"] is None
    assert section["branch_rate"] is None
    assert section["files"] == []
    assert any("--cov-report=xml" in limit for limit in section["limits"])


def test_coverage_data_file_gets_a_hint(tmp_path: Path) -> None:
    write(tmp_path, {".coverage": b"SQLite format 3"})
    section = coverage.analyze(open_repo(tmp_path))
    assert section["status"] == "not_found"
    assert any("coverage xml" in limit for limit in section["limits"])


def test_unmeasured_branches_are_unknown_not_zero(tmp_path: Path) -> None:
    write(tmp_path, {"coverage.xml": COBERTURA, "src/pkg/a.py": "x = 1\ny = 2\n"})
    section = coverage.analyze(open_repo(tmp_path))
    assert section["line_rate"] == 0.5
    assert section["branch_rate"] is None
    # The report's source folder is from another machine: the path still maps by its trailing folders.
    assert section["files"] == [
        {"path": "src/pkg/a.py", "line_rate": 0.5, "lines_covered": 1, "lines_total": 2}
    ]


@pytest.mark.parametrize(
    "document",
    [
        # External entity
        '<?xml version="1.0"?><!DOCTYPE c [<!ENTITY x SYSTEM "file:///etc/passwd">]>'
        '<coverage line-rate="1"><a>&x;</a></coverage>',
        # Billion laughs
        '<!DOCTYPE lolz [<!ENTITY a "lol"><!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;">'
        '<!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;&b;">]><coverage line-rate="1"><a>&c;</a></coverage>',
        # A DOCTYPE alone, in other case
        '<!doctype coverage><coverage line-rate="1"/>',
        # An entity declaration outside a DOCTYPE still isn't let through
        '<coverage line-rate="1"><!ENTITY x "y"></coverage>',
    ],
)
def test_xml_with_entities_or_doctype_is_refused(tmp_path: Path, document: str) -> None:
    write(tmp_path, {"coverage.xml": document})
    section = coverage.analyze(open_repo(tmp_path))
    assert section["status"] == "not_found"
    assert section["line_rate"] is None
    assert any("refused" in limit or "malformed" in limit for limit in section["limits"])


@pytest.mark.parametrize("encoding", ["utf-16", "utf-16-le", "utf-16-be", "cp037", "utf-7"])
def test_xml_in_another_encoding_cant_hide_a_doctype(tmp_path: Path, encoding: str) -> None:
    header = f'<?xml version="1.0" encoding="{encoding}"?>'
    document = (
        header + '<!DOCTYPE c [<!ENTITY x "y">]><coverage line-rate="1"><a>&x;</a></coverage>'
    )
    write(tmp_path, {"coverage.xml": document.encode(encoding)})
    section = coverage.analyze(open_repo(tmp_path))
    assert section["status"] == "not_found"


@pytest.mark.parametrize(
    ("name", "content"),
    [
        ("coverage.xml", "<coverage line-rate="),
        ("coverage.xml", "<report/>"),
        ("coverage.json", "{nope"),
        ("coverage.json", "[1, 2]"),
        ("coverage.json", '{"files": [], "totals": {}}'),
        ("coverage.json", '{"files": {}, "totals": {"num_statements": 0}}'),
        ("coverage-final.json", '{"a.js": 3}'),
        (
            "coverage-summary.json",
            '{"total": {"lines": {"total": 0, "covered": 0, "pct": "Unknown"}}}',
        ),
        ("lcov.info", "garbage\nnot lcov\n"),
    ],
)
def test_broken_or_empty_reports_never_read_as_zero(
    tmp_path: Path, name: str, content: str
) -> None:
    write(tmp_path, {name: content, "a.py": "x = 1\n"})
    section = coverage.analyze(open_repo(tmp_path))
    assert section["status"] == "not_found", section
    assert section["line_rate"] is None
    assert any(name in limit for limit in section["limits"])


def test_coverage_json(tmp_path: Path) -> None:
    report = {
        "meta": {"version": "7"},
        "files": {"pkg/a.py": {"summary": {"covered_lines": 3, "num_statements": 4}}},
        "totals": {
            "covered_lines": 3,
            "num_statements": 4,
            "covered_branches": 1,
            "num_branches": 2,
        },
    }
    write(tmp_path, {"coverage.json": json.dumps(report), "pkg/a.py": "x = 1\n"})
    section = coverage.analyze(open_repo(tmp_path))
    assert (section["line_rate"], section["branch_rate"]) == (0.75, 0.5)
    assert section["files"][0]["path"] == "pkg/a.py"


def test_lcov_in_an_ignored_coverage_folder(tmp_path: Path) -> None:
    lcov = "TN:\nSF:src/a.ts\nDA:1,1\nDA:2,0\nBRF:4\nBRH:1\nend_of_record\n"
    write(tmp_path, {"web/coverage/lcov.info": lcov, "web/src/a.ts": "export const a = 1;\n"})
    section = coverage.analyze(open_repo(tmp_path))
    assert section["status"] == "found"
    assert section["source"] == "web/coverage/lcov.info"
    assert (section["line_rate"], section["branch_rate"]) == (0.5, 0.25)
    assert section["files"][0]["path"] == "web/src/a.ts"


def test_istanbul_final_counts_lines(tmp_path: Path) -> None:
    report = {
        "/ci/build/web/src/a.ts": {
            "path": "/ci/build/web/src/a.ts",
            "statementMap": {
                "0": {"start": {"line": 1}},
                "1": {"start": {"line": 1}},
                "2": {"start": {"line": 2}},
            },
            "s": {"0": 1, "1": 0, "2": 0},
            "b": {"0": [1, 0]},
        }
    }
    write(tmp_path, {"web/coverage/coverage-final.json": json.dumps(report), "web/src/a.ts": "a\n"})
    section = coverage.analyze(open_repo(tmp_path))
    assert (section["line_rate"], section["branch_rate"]) == (0.5, 0.5)
    assert section["files"][0]["path"] == "web/src/a.ts"
    assert (section["files"][0]["lines_covered"], section["files"][0]["lines_total"]) == (1, 2)


def test_istanbul_summary(tmp_path: Path) -> None:
    report = {
        "total": {"lines": {"total": 10, "covered": 5}, "branches": {"total": 0, "covered": 0}},
        "web/src/a.ts": {"lines": {"total": 10, "covered": 5}},
    }
    write(tmp_path, {"coverage-summary.json": json.dumps(report), "web/src/a.ts": "a\n"})
    section = coverage.analyze(open_repo(tmp_path))
    assert section["line_rate"] == 0.5
    assert section["branch_rate"] is None
    assert section["files"][0]["path"] == "web/src/a.ts"


def test_reports_of_several_parts_combine_by_lines(tmp_path: Path) -> None:
    write(
        tmp_path,
        {
            "api/coverage.xml": '<coverage line-rate="1"><packages><package><classes><class filename="a.py"><lines><line number="1" hits="1"/></lines></class></classes></package></packages></coverage>',
            "api/a.py": "x = 1\n",
            "web/coverage/lcov.info": "SF:b.ts\nDA:1,0\nDA:2,0\nDA:3,0\nend_of_record\n",
            "web/b.ts": "x\n",
        },
    )
    section = coverage.analyze(open_repo(tmp_path))
    assert section["line_rate"] == 0.25
    assert section["sources"] == ["api/coverage.xml", "web/coverage/lcov.info"]
    assert {f["path"] for f in section["files"]} == {"api/a.py", "web/b.ts"}


def test_preferred_report_wins_in_one_folder(tmp_path: Path) -> None:
    write(
        tmp_path,
        {
            "coverage.xml": '<coverage line-rate="0.9"/>',
            "coverage.json": '{"files": {}, "totals": {"covered_lines": 1, "num_statements": 10}}',
        },
    )
    section = coverage.analyze(open_repo(tmp_path))
    assert section["source"] == "coverage.xml"
    assert section["line_rate"] == 0.9


def test_report_older_than_the_code_is_noted(tmp_path: Path) -> None:
    import os

    write(tmp_path, {"coverage.xml": COBERTURA, "pkg/a.py": "x = 1\n"})
    os.utime(tmp_path / "coverage.xml", (1000, 1000))
    section = coverage.analyze(open_repo(tmp_path))
    assert section["status"] == "found"
    assert any("out of date" in limit for limit in section["limits"])


def test_report_that_links_outside_the_repo_isnt_read(tmp_path: Path) -> None:
    outside = tmp_path / "outside.xml"
    outside.write_text('<coverage line-rate="0.9"/>')
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "coverage.xml").symlink_to(outside)
    section = coverage.analyze(open_repo(repo))
    assert section["status"] == "not_found"
    assert any("outside the repository" in limit for limit in section["limits"])


def test_unknown_absolute_paths_arent_reported(tmp_path: Path) -> None:
    report = '<coverage line-rate="1"><packages><package><classes><class filename="/home/someone/x/secret.py"><lines><line number="1" hits="1"/></lines></class></classes></package></packages></coverage>'
    write(tmp_path, {"coverage.xml": report})
    section = coverage.analyze(open_repo(tmp_path))
    assert section["files"] == []
    assert any("aren't in this repository" in limit for limit in section["limits"])
    assert "someone" not in json.dumps(section)


# Dependencies: the inventory


def packages_of(root: Path) -> dict[tuple[str, str | None], dict[str, Any]]:
    section = dependencies.analyze(open_repo(root))
    return {(p["name"], p["version"]): p for p in section["packages"]}


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("flask==2.0.0", ("flask", "2.0.0")),
        ("Flask_Login == 2.0", ("flask-login", "2.0")),
        ("uvicorn[standard]==0.20", ("uvicorn", "0.20")),
        ("a===1.0", ("a", "1.0")),
        ("a>=1,<2", ("a", None)),
        ("a~=1.0", ("a", None)),
        ("a==1.*", ("a", None)),
        ("a", ("a", None)),
        ('b==1.0; python_version < "3.9"', ("b", "1.0")),
        ("c==1.0 --hash=sha256:abc", ("c", "1.0")),
        ("d @ https://example.com/d.whl", ("d", None)),
        ("e (==1.0)", ("e", "1.0")),
        ("./local/path", None),
        ("folder/thing", None),
        ("", None),
    ],
)
def test_parse_requirement(line: str, expected: Any) -> None:
    assert dependencies.parse_requirement(line) == expected


def test_requirements_flags_and_includes(tmp_path: Path) -> None:
    write(
        tmp_path,
        {
            "requirements.txt": "# comment\n-r requirements-dev.txt\n-e .\nhttps://x.example/y.whl\nflask==2.0.0 \\\n  --hash=sha256:abc\nnumpy>=1\n",
            "requirements-dev.txt": "pytest==8.0.0\n",
            "more.txt": "ignored==1.0\n",
        },
    )
    found = packages_of(tmp_path)
    assert set(found) == {("flask", "2.0.0"), ("numpy", None), ("pytest", "8.0.0")}
    assert found[("pytest", "8.0.0")]["dev"] is True
    assert found[("flask", "2.0.0")]["dev"] is False
    assert found[("flask", "2.0.0")]["direct"] is True


def test_unpinned_packages_are_a_limit(tmp_path: Path) -> None:
    write(tmp_path, {"requirements.txt": "numpy>=1\n"})
    section = dependencies.analyze(open_repo(tmp_path))
    assert section["packages"][0]["version"] is None
    assert any("numpy" in limit and "can't be checked" in limit for limit in section["limits"])


def test_pyproject_tables(tmp_path: Path) -> None:
    write(
        tmp_path,
        {
            "pyproject.toml": """
[project]
name = "me"
dependencies = ["flask==2.0.0", "click>=8"]
[project.optional-dependencies]
all = ["me[extra]"]
extra = ["rich==13.0.0"]
dev = ["ruff==0.1.0"]
[dependency-groups]
test = ["pytest==8.0.0"]
[tool.poetry.dependencies]
python = "^3.11"
requests = "2.31.0"
httpx = "^0.27"
[tool.poetry.group.lint.dependencies]
black = {version = "24.1.0"}
""",
        },
    )
    found = packages_of(tmp_path)
    assert set(found) == {
        ("flask", "2.0.0"),
        ("click", None),
        ("rich", "13.0.0"),
        ("ruff", "0.1.0"),
        ("pytest", "8.0.0"),
        ("requests", "2.31.0"),
        ("httpx", None),
        ("black", "24.1.0"),
    }
    assert found[("ruff", "0.1.0")]["dev"] is True
    assert found[("rich", "13.0.0")]["dev"] is False
    assert found[("black", "24.1.0")]["dev"] is True


def test_uv_lock_beats_ranges_and_knows_direct_from_dev(tmp_path: Path) -> None:
    write(
        tmp_path,
        {
            "pyproject.toml": '[project]\nname = "me"\ndependencies = ["flask>=2"]\n[dependency-groups]\ndev = ["pytest>=8"]\n',
            "uv.lock": """
[[package]]
name = "me"
version = "0.1.0"
source = { virtual = "." }
dependencies = [{ name = "flask" }]
[package.dev-dependencies]
dev = [{ name = "pytest" }]

[[package]]
name = "flask"
version = "3.0.0"
source = { registry = "https://pypi.org/simple" }
dependencies = [{ name = "werkzeug" }]

[[package]]
name = "werkzeug"
version = "3.0.1"
source = { registry = "https://pypi.org/simple" }

[[package]]
name = "pytest"
version = "8.1.0"
source = { registry = "https://pypi.org/simple" }
dependencies = [{ name = "pluggy" }]

[[package]]
name = "pluggy"
version = "1.4.0"
source = { registry = "https://pypi.org/simple" }

[[package]]
name = "local"
version = "1.0"
source = { path = "./local" }
""",
        },
    )
    found = packages_of(tmp_path)
    assert set(found) == {
        ("flask", "3.0.0"),
        ("werkzeug", "3.0.1"),
        ("pytest", "8.1.0"),
        ("pluggy", "1.4.0"),
    }
    flags = {name: (p["direct"], p["dev"]) for (name, _), p in found.items()}
    assert flags == {
        "flask": (True, False),
        "werkzeug": (False, False),
        "pytest": (True, True),
        "pluggy": (False, True),
    }
    assert found[("flask", "3.0.0")]["manifest"] == "uv.lock"


def test_workspace_member_uses_the_lock_above_it(tmp_path: Path) -> None:
    write(
        tmp_path,
        {
            "uv.lock": '[[package]]\nname = "flask"\nversion = "3.0.0"\nsource = { registry = "x" }\n',
            "packages/a/pyproject.toml": '[project]\nname = "a"\ndependencies = ["flask>=2"]\n',
        },
    )
    found = packages_of(tmp_path)
    assert set(found) == {("flask", "3.0.0")}
    assert found[("flask", "3.0.0")]["direct"] is True


def test_requirements_dont_take_a_lock_from_another_folder(tmp_path: Path) -> None:
    write(
        tmp_path,
        {
            "uv.lock": '[[package]]\nname = "flask"\nversion = "3.0.0"\nsource = { registry = "x" }\n',
            "tools/requirements.txt": "flask>=2\n",
        },
    )
    assert set(packages_of(tmp_path)) == {("flask", "3.0.0"), ("flask", None)}


def test_a_pin_that_differs_from_the_lock_is_kept(tmp_path: Path) -> None:
    write(
        tmp_path,
        {
            "requirements.txt": "flask==2.0.0\n",
            "poetry.lock": '[[package]]\nname = "flask"\nversion = "3.0.0"\n',
        },
    )
    assert set(packages_of(tmp_path)) == {("flask", "2.0.0"), ("flask", "3.0.0")}


def test_poetry_lock(tmp_path: Path) -> None:
    write(
        tmp_path,
        {
            "pyproject.toml": '[tool.poetry.dependencies]\npython = "^3.11"\nflask = "^2"\n',
            "poetry.lock": """
[[package]]
name = "flask"
version = "2.3.0"
groups = ["main"]

[[package]]
name = "pytest"
version = "8.0.0"
groups = ["dev"]

[[package]]
name = "mine"
version = "1"
[package.source]
type = "directory"
""",
        },
    )
    found = packages_of(tmp_path)
    assert set(found) == {("flask", "2.3.0"), ("pytest", "8.0.0")}
    assert found[("flask", "2.3.0")]["direct"] is True
    assert found[("pytest", "8.0.0")]["dev"] is True


def test_pipfile_lock(tmp_path: Path) -> None:
    write(
        tmp_path,
        {
            "Pipfile": '[packages]\nflask = "*"\n[dev-packages]\npytest = "==8.0.0"\n',
            "Pipfile.lock": json.dumps(
                {
                    "default": {"flask": {"version": "==2.3.0"}, "click": {"version": "==8.0.0"}},
                    "develop": {"pytest": {"version": "==8.0.0"}},
                }
            ),
        },
    )
    found = packages_of(tmp_path)
    assert set(found) == {("flask", "2.3.0"), ("click", "8.0.0"), ("pytest", "8.0.0")}
    assert found[("flask", "2.3.0")]["direct"] is True
    assert found[("click", "8.0.0")]["direct"] is False
    assert found[("pytest", "8.0.0")]["dev"] is True


def test_package_json_versions(tmp_path: Path) -> None:
    write(
        tmp_path,
        {
            "package.json": json.dumps(
                {
                    "dependencies": {
                        "exact": "1.2.3",
                        "pre": "1.2.3-beta.1",
                        "caret": "^1.2.3",
                        "tag": "latest",
                        "git": "github:a/b",
                        "sibling": "workspace:*",
                        "local": "file:../x",
                    },
                    "devDependencies": {"dev-one": "=2.0.0"},
                }
            )
        },
    )
    found = packages_of(tmp_path)
    assert set(found) == {
        ("exact", "1.2.3"),
        ("pre", "1.2.3-beta.1"),
        ("caret", None),
        ("tag", None),
        ("git", None),
        ("dev-one", "2.0.0"),
    }
    assert found[("dev-one", "2.0.0")]["dev"] is True


def test_package_lock_v3(tmp_path: Path) -> None:
    lock = {
        "lockfileVersion": 3,
        "packages": {
            "": {"dependencies": {"a": "^1"}, "devDependencies": {"b": "^2"}},
            "node_modules/a": {"version": "1.5.0"},
            "node_modules/b": {"version": "2.5.0", "dev": True},
            "node_modules/a/node_modules/c": {"version": "3.0.0"},
            "node_modules/@s/d": {"version": "4.0.0"},
            "node_modules/alias": {"name": "real", "version": "5.0.0"},
            "node_modules/me": {"link": True, "resolved": "packages/me"},
        },
    }
    write(
        tmp_path,
        {
            "package.json": json.dumps(
                {"dependencies": {"a": "^1"}, "devDependencies": {"b": "^2"}}
            ),
            "package-lock.json": json.dumps(lock),
        },
    )
    found = packages_of(tmp_path)
    assert set(found) == {
        ("a", "1.5.0"),
        ("b", "2.5.0"),
        ("c", "3.0.0"),
        ("@s/d", "4.0.0"),
        ("real", "5.0.0"),
    }
    assert found[("a", "1.5.0")]["direct"] is True
    assert found[("c", "3.0.0")]["direct"] is False
    assert found[("b", "2.5.0")]["dev"] is True


def test_package_lock_v1_tree(tmp_path: Path) -> None:
    lock = {
        "lockfileVersion": 1,
        "dependencies": {
            "a": {"version": "1.0.0", "dependencies": {"c": {"version": "3.0.0"}}},
            "b": {"version": "2.0.0", "dev": True},
            "g": {"version": "github:x/y#abc"},
        },
    }
    write(tmp_path, {"package-lock.json": json.dumps(lock)})
    found = packages_of(tmp_path)
    assert set(found) == {("a", "1.0.0"), ("b", "2.0.0"), ("c", "3.0.0"), ("g", None)}


def test_package_lock_v1_deep_tree_doesnt_overflow(tmp_path: Path) -> None:
    entry: dict[str, Any] = {"version": "1.0.0"}
    text = json.dumps(entry)
    for _ in range(500):
        text = '{"version": "1.0.0", "dependencies": {"x": ' + text + "}}"
    write(tmp_path, {"package-lock.json": '{"dependencies": {"x": ' + text + "}}"})
    section = dependencies.analyze(open_repo(tmp_path))
    assert section["status"] == "not_scanned"
    assert len(section["packages"]) == 1


def test_yarn_v1(tmp_path: Path) -> None:
    lock = """# yarn lockfile v1


"@s/a@^1.0.0", "@s/a@^1.1.0":
  version "1.2.0"
  resolved "https://example.com/a.tgz"
  dependencies:
    b "^2"

b@^2:
  version "2.5.0"
"""
    write(tmp_path, {"yarn.lock": lock, "package.json": '{"dependencies": {"b": "^2"}}'})
    found = packages_of(tmp_path)
    assert set(found) == {("@s/a", "1.2.0"), ("b", "2.5.0")}
    assert found[("b", "2.5.0")]["direct"] is True


def test_yarn_berry_is_a_limit(tmp_path: Path) -> None:
    write(tmp_path, {"yarn.lock": '__metadata:\n  version: 6\n\n"a@npm:^1":\n  version: 1.0.0\n'})
    section = dependencies.analyze(open_repo(tmp_path))
    assert section["status"] == "none"
    assert any("Yarn Berry" in limit for limit in section["limits"])


@pytest.mark.parametrize(
    ("lock", "expected"),
    [
        (
            "lockfileVersion: '9.0'\nimporters:\n  .:\n    dependencies:\n      a:\n        specifier: ^1\n        version: 1.5.0\n"
            "    devDependencies:\n      '@s/b':\n        specifier: ^2\n        version: 2.5.0\npackages:\n  a@1.5.0:\n    resolution: {}\n"
            "  '@s/b@2.5.0(peer@1.0.0)':\n    resolution: {}\n  c@3.0.0:\n    resolution: {}\n",
            {("a", "1.5.0"), ("@s/b", "2.5.0"), ("c", "3.0.0")},
        ),
        (
            "lockfileVersion: 5.4\ndependencies:\n  a: 1.5.0\npackages:\n  /a/1.5.0:\n    dev: false\n  /c/3.0.0:\n    dev: true\n",
            {("a", "1.5.0"), ("c", "3.0.0")},
        ),
    ],
)
def test_pnpm(tmp_path: Path, lock: str, expected: set[Any]) -> None:
    write(tmp_path, {"pnpm-lock.yaml": lock})
    found = packages_of(tmp_path)
    assert set(found) == expected
    assert next(p for (n, _), p in found.items() if n == "a")["direct"] is True


@pytest.mark.parametrize(
    "name",
    [
        "requirements.txt",
        "pyproject.toml",
        "uv.lock",
        "poetry.lock",
        "Pipfile",
        "Pipfile.lock",
        "package.json",
        "package-lock.json",
        "yarn.lock",
        "pnpm-lock.yaml",
    ],
)
@pytest.mark.parametrize("content", ["\x00\xff{[nope:\n= =", "[[[", "{", "- - -\n: :", ""])
def test_malformed_manifests_never_raise(tmp_path: Path, name: str, content: str) -> None:
    write(tmp_path, {name: content})
    section = dependencies.analyze(open_repo(tmp_path))
    assert section["status"] in {"none", "not_scanned"}
    assert isinstance(section["limits"], list)


@pytest.mark.parametrize(
    ("name", "content"),
    [
        ("pyproject.toml", "[project\n"),
        ("package.json", "{nope"),
        ("uv.lock", "[[package"),
        ("package-lock.json", "["),
        ("pnpm-lock.yaml", "a: [b\nc: d: e"),
    ],
)
def test_malformed_manifests_are_limits(tmp_path: Path, name: str, content: str) -> None:
    write(tmp_path, {name: content})
    section = dependencies.analyze(open_repo(tmp_path))
    assert any(f"{name} couldn't be read" in limit for limit in section["limits"])


def test_wrongly_typed_fields_are_harmless(tmp_path: Path) -> None:
    write(
        tmp_path,
        {
            "package.json": '{"dependencies": ["a"], "devDependencies": 3}',
            "package-lock.json": '{"packages": {"": 3, "node_modules/a": 4, "node_modules/b": {"version": 5}}}',
            "pyproject.toml": '[project]\nname = 3\ndependencies = "x"\n[project.optional-dependencies]\na = 3\n',
            "uv.lock": "package = 3\n",
            "Pipfile.lock": '{"default": [], "develop": {"a": 3}}',
        },
    )
    section = dependencies.analyze(open_repo(tmp_path))
    assert section["status"] == "not_scanned"
    assert {(p["name"], p["version"]) for p in section["packages"]} >= {("b", None)}


def test_fixture_manifests_arent_dependencies(tmp_path: Path) -> None:
    write(tmp_path, {"tests/fixtures/app/requirements.txt": "ghost==1.0.0\n"})
    assert dependencies.analyze(open_repo(tmp_path))["status"] == "none"


def test_a_manifest_that_links_outside_isnt_read(tmp_path: Path) -> None:
    outside = tmp_path / "secret.txt"
    outside.write_text("private-token==1.0.0\n")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "requirements.txt").symlink_to(outside)
    section = dependencies.analyze(open_repo(repo))
    assert section["packages"] == []
    assert any("outside the repository" in limit for limit in section["limits"])


def test_other_ecosystems_are_a_limit(tmp_path: Path) -> None:
    write(tmp_path, {"go.mod": "module x\n", "requirements.txt": "a==1.0\n"})
    section = dependencies.analyze(open_repo(tmp_path))
    assert any("go.mod" in limit for limit in section["limits"])


# Dependencies: the CVE lookup


def test_golden_inventory_uses_no_network_by_default(
    shop: Path, monkeypatch: pytest.MonkeyPatch, no_network: list[Any]
) -> None:
    section = analyze(shop)["sections"]["dependencies"]
    assert no_network == []
    assert section["status"] == "not_scanned"
    assert section["vulnerabilities"] is None, "a skipped scan must not look clean"
    assert any("weren't checked" in limit for limit in section["limits"])
    assert {(p["ecosystem"], p["name"], p["version"]) for p in section["packages"]} == {
        tuple(p) for p in KEY["packages"]
    }
    by_name = {p["name"]: p for p in section["packages"]}
    assert by_name["typescript"]["dev"] is True
    assert by_name["fastapi"]["manifest"] == "backend/requirements.txt"


def osv_stub(monkeypatch: pytest.MonkeyPatch, replies: dict[str, Any]) -> list[Any]:
    """``replies`` by URL ending; a value that's an exception is raised."""
    seen: list[Any] = []

    def urlopen(request: Any, timeout: float | None = None) -> Response:
        assert timeout is not None
        assert request.full_url.startswith("https://api.osv.dev/")
        seen.append(request)
        for ending, reply in replies.items():
            if request.full_url.endswith(ending):
                reply = reply(request) if callable(reply) else reply
                if isinstance(reply, BaseException):
                    raise reply
                return Response(reply)
        raise urllib.error.HTTPError(request.full_url, 404, "no", None, None)  # type: ignore[arg-type]

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    return seen


GHSA = {
    "id": "GHSA-aaaa",
    "aliases": ["CVE-2020-1", "PYSEC-2020-1"],
    "summary": "A flaw",
    "database_specific": {"severity": "MODERATE"},
    "affected": [
        {
            "package": {"ecosystem": "PyPI", "name": "Flask"},
            "ranges": [
                {"type": "GIT", "events": [{"introduced": "0"}, {"fixed": "abcdef"}]},
                {"type": "ECOSYSTEM", "events": [{"introduced": "0"}, {"fixed": "1.2.7"}]},
                {
                    "type": "ECOSYSTEM",
                    "events": [
                        {"introduced": "2.0"},
                        {"fixed": "2.4.1"},
                        {"introduced": "3.0"},
                        {"fixed": "3.0.2"},
                    ],
                },
            ],
        },
        {
            "package": {"ecosystem": "PyPI", "name": "other"},
            "ranges": [{"type": "ECOSYSTEM", "events": [{"fixed": "2.0.1"}]}],
        },
    ],
}
PYSEC = {
    "id": "PYSEC-2020-1",
    "aliases": ["CVE-2020-1"],
    "details": "A flaw, at length",
    "severity": [{"type": "CVSS_V3", "score": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H"}],
    "affected": [],
}


def test_scan_reports_merges_aliases_and_chooses_the_fix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write(tmp_path, {"requirements.txt": "flask==2.0.0\nclean==1.0\nunpinned>=1\n"})
    batch = {"results": [{"vulns": []}]}
    batch = {
        "results": [
            {},  # clean, sorted first
            {"vulns": [{"id": "GHSA-aaaa"}, {"id": "PYSEC-2020-1"}, {"id": "GONE"}]},
        ]
    }
    seen = osv_stub(
        monkeypatch,
        {
            "/querybatch": batch,
            "/vulns/GHSA-aaaa": GHSA,
            "/vulns/PYSEC-2020-1": PYSEC,
            "/vulns/GONE": {"id": "GONE", "withdrawn": "2021-01-01T00:00:00Z"},
        },
    )
    section = dependencies.analyze(open_repo(tmp_path, check_vulnerabilities=True))
    assert section["status"] == "scanned"
    assert section["scanner"] == "osv"
    assert section["vulnerabilities"] == [
        {
            "ecosystem": "PyPI",
            "package": "flask",
            "version": "2.0.0",
            "id": "GHSA-aaaa",
            "aliases": ["CVE-2020-1", "PYSEC-2020-1"],
            "severity": "critical",  # the worse of the two records' views
            "fixed_in": "2.4.1",  # the lowest fix above 2.0.0, not 1.2.7
            "summary": "A flaw",
        }
    ]
    body = json.loads(seen[0].data)
    assert {q["package"]["name"] for q in body["queries"]} == {"flask", "clean"}
    assert all("version" in q for q in body["queries"])
    agent = seen[0].get_header("User-agent")
    assert agent == dependencies.USER_AGENT
    assert "@" not in agent and "/Users" not in agent
    assert any("unpinned" in limit for limit in section["limits"])


def test_scan_follows_pages(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write(tmp_path, {"requirements.txt": "a==1.0\n"})

    def batch(request: Any) -> dict[str, Any]:
        query = json.loads(request.data)["queries"][0]
        if "page_token" not in query:
            return {"results": [{"vulns": [{"id": "ONE"}], "next_page_token": "more"}]}
        assert query["page_token"] == "more"
        return {"results": [{"vulns": [{"id": "TWO"}]}]}

    osv_stub(
        monkeypatch,
        {"/querybatch": batch, "/vulns/ONE": {"id": "ONE"}, "/vulns/TWO": {"id": "TWO"}},
    )
    section = dependencies.analyze(open_repo(tmp_path, check_vulnerabilities=True))
    assert {v["id"] for v in section["vulnerabilities"]} == {"ONE", "TWO"}


def test_scan_that_never_stops_paging_is_a_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write(tmp_path, {"requirements.txt": "a==1.0\n"})
    osv_stub(monkeypatch, {"/querybatch": {"results": [{"next_page_token": "again"}]}})
    section = dependencies.analyze(open_repo(tmp_path, check_vulnerabilities=True))
    assert section["status"] == "failed"
    assert section["vulnerabilities"] is None


@pytest.mark.parametrize(
    "reply",
    [
        urllib.error.URLError("down"),
        TimeoutError("slow"),
        b"not json",
        b"[]",
        {"results": []},
        {"results": "nope"},
        b"x" * (dependencies.OSV_MAX_RESPONSE + 1),
    ],
)
def test_scan_failure_never_reads_as_clean(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reply: Any
) -> None:
    write(tmp_path, {"requirements.txt": "a==1.0\n"})
    osv_stub(monkeypatch, {"/querybatch": reply})
    section = dependencies.analyze(open_repo(tmp_path, check_vulnerabilities=True))
    assert section["status"] == "failed"
    assert section["vulnerabilities"] is None
    assert any("failed" in limit for limit in section["limits"])
    assert section["packages"], "what's installed is still known"


def test_missing_details_keep_the_finding(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write(tmp_path, {"requirements.txt": "a==1.0\n"})
    osv_stub(
        monkeypatch,
        {
            "/querybatch": {"results": [{"vulns": [{"id": "X-1"}]}]},
            "/vulns/X-1": urllib.error.URLError("down"),
        },
    )
    section = dependencies.analyze(open_repo(tmp_path, check_vulnerabilities=True))
    assert section["status"] == "scanned"
    found = section["vulnerabilities"][0]
    assert (found["id"], found["severity"], found["fixed_in"]) == ("X-1", "unknown", None)
    assert any("X-1" in limit for limit in section["limits"])


def test_no_packages_means_no_network(tmp_path: Path, no_network: list[Any]) -> None:
    write(tmp_path, {"a.py": "x = 1\n"})
    section = dependencies.analyze(open_repo(tmp_path, check_vulnerabilities=True))
    assert section["status"] == "none"
    assert no_network == []


@pytest.mark.parametrize(
    ("detail", "expected"),
    [
        ({"database_specific": {"severity": "CRITICAL"}}, "critical"),
        ({"database_specific": {"severity": "MODERATE"}}, "medium"),
        ({"database_specific": {"severity": "LOW"}}, "low"),
        (
            {
                "severity": [
                    {"type": "CVSS_V3", "score": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H"}
                ]
            },
            "critical",
        ),
        (
            {
                "severity": [
                    {"type": "CVSS_V3", "score": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:N/I:N/A:H"}
                ]
            },
            "high",
        ),
        (
            {
                "severity": [
                    {"type": "CVSS_V3", "score": "CVSS:3.1/AV:N/AC:L/PR:L/UI:N/S:C/C:L/I:L/A:N"}
                ]
            },
            "medium",
        ),
        (
            {
                "severity": [
                    {"type": "CVSS_V3", "score": "CVSS:3.1/AV:L/AC:H/PR:H/UI:R/S:U/C:L/I:N/A:N"}
                ]
            },
            "low",
        ),
        (
            {
                "severity": [
                    {
                        "type": "CVSS_V4",
                        "score": "CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N",
                    }
                ]
            },
            "unknown",
        ),
        ({"severity": [{"type": "CVSS_V3", "score": "garbage"}]}, "unknown"),
        ({"severity": "nope", "database_specific": "nope"}, "unknown"),
        ({}, "unknown"),
    ],
)
def test_severity(detail: dict[str, Any], expected: str) -> None:
    assert dependencies.severity_of(detail) == expected


def test_version_order() -> None:
    key = dependencies._version_key
    assert key("1.0a1") < key("1.0") < key("1.0.post1")
    assert key("1.0") < key("1.0.1") < key("1.10")
    assert key("1.0") == key("1.0.0")
    assert key("v2.0.0") > key("1.9.9")


# Hostile input: everything finishes quickly, whatever the shape.


HOSTILE = [
    "a" * 200_000,
    "-" * 200_000,
    ("a-" * 100_000) + "!",
    ("a." * 100_000) + "!",
    " " * 200_000 + "x",
    "=" * 200_000,
    "1." * 100_000,
    "a" + "[" * 100_000,
    "@" * 200_000,
]


@pytest.mark.parametrize("text", HOSTILE, ids=range(len(HOSTILE)))
def test_parsers_dont_backtrack_on_long_input(text: str) -> None:
    started = time.monotonic()
    dependencies.parse_requirement(text)
    dependencies._npm_version(text)
    dependencies._poetry_version(text)
    dependencies._version_key(text)
    dependencies._pnpm_key(text)
    dependencies._yarn_names(text)
    dependencies._is_dev_group(text)
    overview.is_database_image(text)
    assert time.monotonic() - started < FAST_SECONDS


@pytest.mark.parametrize(
    "line",
    [
        "const a" + " " * 4_500,
        "const a: " + "b" * 4_500,
        "function" + " " * 4_500 + "*" + " " * 400,
        "  " + "async " * 800,
        "  " + "public " * 700 + "m(" + "x" * 300,
        "  m(" + "(" * 4_500,
        "?" * 4_900,
        "class" + " " * 4_900,
        " " * 4_900 + "it",
    ],
    ids=range(9),
)
def test_script_patterns_dont_backtrack(tmp_path: Path, line: str) -> None:
    source = "\n".join(["class A {", *[line] * 100, "}"])
    started = time.monotonic()
    quality._measure_script("a.ts", source)
    assert time.monotonic() - started < FAST_SECONDS


def test_unterminated_strings_and_comments_are_linear() -> None:
    for text in (
        "'" * 300_000,
        "/*" * 150_000,
        "`" + "\\" * 300_000,
        '"\n' * 150_000,
        "/" * 300_000,
    ):
        started = time.monotonic()
        quality.blank_strings_and_comments(text)
        assert time.monotonic() - started < FAST_SECONDS


def test_analyze_survives_a_hostile_repo(tmp_path: Path) -> None:
    write(
        tmp_path,
        {
            "coverage.xml": "<coverage>" + "<a>" * 50_000,
            "coverage.json": "[" * 200_000,
            "package.json": "[" * 200_000,
            "requirements.txt": "a" * 200_000 + "\n" + "-" * 200_000 + "\n",
            "pyproject.toml": "a = " + "[" * 200_000,
            "docker-compose.yml": "a: " + "[" * 200_000,
            "bad.py": "\x00",
            "bad.ts": "'" * 100_000,
        },
    )
    sections = analyze(tmp_path)["sections"]
    for name in ("overview", "calm", "quality", "coverage", "dependencies"):
        assert sections[name]["status"] != "failed", sections[name]["limits"]
    assert sections["coverage"]["line_rate"] is None
