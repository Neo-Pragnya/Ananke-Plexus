"""A small map that can't go stale, and the right tool for each question."""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

from ananke.plexus.context import (
    ROUTES,
    ContextError,
    benchmark,
    estimate_tokens,
    pack,
    repo_map,
    serena_setup,
    source_state,
    status,
)
from ananke.plexus.graph.providers.graphifyy import GraphifyyAdapter

SHOP = Path(__file__).resolve().parent.parent / "fixtures" / "repo-mapping" / "shop"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "__init__.py").write_text("")
    (root / "pkg" / "core.py").write_text("def add(a, b):\n    return a + b\n")
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@example.com",
            "commit",
            "-qm",
            "i",
        ],
        check=True,
    )
    return root


def fake_tool(folder: Path, name: str, script: str) -> None:
    folder.mkdir(exist_ok=True)
    path = folder / name
    path.write_text("#!/bin/sh\n" + script)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def test_the_map_shows_what_matters_within_its_budget(tmp_path: Path) -> None:
    copy = tmp_path / "shop"
    shutil.copytree(SHOP, copy)
    result = repo_map(copy, budget=600)
    text = result["map"]
    assert result["tokens_estimate"] <= 660  # a small allowance for the closing lines
    for wanted in ("backend", "POST /api/orders", "orders", "Next:"):
        assert wanted in text


def test_a_tight_budget_says_what_it_cut(repo: Path) -> None:
    for i in range(80):
        (repo / "pkg" / f"mod{i}.py").write_text("def f():\n    return 1\n")
    assert "more" in repo_map(repo, budget=200)["map"]


def test_the_map_rebuilds_when_the_code_changes_and_only_then(repo: Path) -> None:
    first = repo_map(repo)
    assert first["cached"] is False
    assert repo_map(repo)["cached"] is True
    (repo / "pkg" / "core.py").write_text(
        "def add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return a - b\n"
    )
    changed = repo_map(repo)
    assert changed["cached"] is False and changed["source_state"] != first["source_state"]
    # an edit to a file that was already edited must count too
    before = source_state(repo)
    (repo / "pkg" / "core.py").write_text("def only():\n    return 1\n")
    assert source_state(repo) != before
    (repo / "new.py").write_text("x = 1\n")
    assert repo_map(repo)["cached"] is False


def test_its_own_cache_doesnt_count_as_a_change(repo: Path) -> None:
    repo_map(repo)
    assert repo_map(repo)["cached"] is True


def test_a_corrupt_cache_is_rebuilt(repo: Path) -> None:
    repo_map(repo)
    (repo / ".ananke" / "context" / "map-1500.json").write_text("not json")
    assert repo_map(repo)["cached"] is False


@pytest.mark.parametrize("budget", [0, 199, 20_001])
def test_a_silly_budget_is_refused(repo: Path, budget: int) -> None:
    with pytest.raises(ContextError) as exc:
        repo_map(repo, budget=budget)
    assert exc.value.code == "INVALID_REQUEST"


def test_a_hostile_file_cant_run_or_stop_the_map(repo: Path) -> None:
    (repo / "boom.py").write_text("open('ran.txt', 'w').write('x')\n")
    (repo / "bad.py").write_text("def broken(\n")
    assert repo_map(repo)["map"]
    assert not (repo / "ran.txt").exists()


def test_status_runs_nothing_and_says_how_to_get_each_tool(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", "")
    tools = status(repo)["tools"]
    assert tools["map"]["ready"] is True
    assert tools["repomix"]["ready"] is False and "context" in tools["repomix"]["setup"]
    assert tools["graphify"]["ready"] is False and "update" in tools["graphify"]["setup"]
    assert "GPL" in tools["serena"]["licence"]


def test_a_graphify_graph_older_than_the_code_is_called_stale(repo: Path) -> None:
    out = repo / "graphify-out"
    out.mkdir()
    (out / "graph.json").write_text("{}")
    os.utime(out / "graph.json", (1, 1))
    assert status(repo)["tools"]["graphify"]["fresh"] is False
    os.utime(out / "graph.json")
    assert status(repo)["tools"]["graphify"]["fresh"] is True


def test_every_question_has_a_route_and_a_reason() -> None:
    assert len(ROUTES) >= 6 and all(all(part for part in route) for route in ROUTES)


def test_serena_is_pinned_runs_apart_and_opens_no_browser(repo: Path) -> None:
    setup = serena_setup(repo)
    args = setup["mcp_server"]["args"]
    assert "serena-agent==2.1.0" in args and args[args.index("--open-web-dashboard") + 1] == "false"
    assert "--language python" in setup["each_repository"]


def test_ananke_doesnt_depend_on_serena() -> None:
    project = (Path(__file__).resolve().parents[2] / "pyproject.toml").read_text()
    assert "serena" not in project.lower()


def test_a_pack_must_say_what_to_pack(repo: Path) -> None:
    with pytest.raises(ContextError) as exc:
        pack(repo, include=[""])
    assert exc.value.code == "INVALID_REQUEST"


def test_a_pack_runs_repomix_with_the_scope_and_without_noise(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bin_dir = tmp_path / "bin"
    log = tmp_path / "args.txt"
    fake_tool(
        bin_dir,
        "repomix",
        f'printf "%s\\n" "$@" > "{log}"\nwhile [ $# -gt 0 ]; do [ "$1" = "--output" ] && echo packed > "$2"; shift; done\n',
    )
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    result = pack(repo, include=["pkg/**/*.py"])
    args = log.read_text().split()
    assert "--compress" in args and "pkg/**/*.py" in args and any("min.js" in a for a in args)
    assert result["tokens_estimate"] == estimate_tokens("packed\n")


def test_a_failed_pack_says_so(repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bin_dir = tmp_path / "bin"
    fake_tool(bin_dir, "repomix", 'echo "it broke" >&2\nexit 3\n')
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    with pytest.raises(ContextError) as exc:
        pack(repo, include=["*.py"], output=tmp_path / "nothing" / "pack.xml")
    assert exc.value.code == "TOOL_FAILED" and "it broke" in exc.value.message


def test_a_missing_tool_is_a_clear_error(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", "")
    with pytest.raises(ContextError) as exc:
        pack(repo, include=["*.py"])
    assert exc.value.code == "TOOL_MISSING"


def test_benchmark_compares_a_first_look_each_way(repo: Path) -> None:
    (repo / "big.py").write_text("x = 1\n" * 5000)
    subprocess.run(["git", "-C", str(repo), "add", "big.py"], check=True)
    result = benchmark(repo)
    assert result["all_source"] > result["map"] and result["biggest_file"]["path"] == "big.py"


def test_the_graphify_adapter_reads_what_the_real_tool_writes(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    graph = {
        "nodes": [
            {
                "id": "a",
                "label": "a()",
                "file_type": "code",
                "source_file": "pkg/core.py",
                "community": 1,
            }
        ],
        "links": [
            {
                "source": "a",
                "target": "b",
                "relation": "calls",
                "confidence": "INFERRED",
                "source_file": "pkg/core.py",
            }
        ],
    }
    bin_dir = tmp_path / "bin"
    fake_tool(
        bin_dir,
        "graphify",
        f'mkdir -p "$2/graphify-out"\ncat > "$2/graphify-out/graph.json" <<\'J\'\n{json.dumps(graph)}\nJ\n',
    )
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    adapter = GraphifyyAdapter()
    assert adapter.available()
    snapshot = adapter.build(repo)
    assert snapshot.nodes[0].name == "a()" and snapshot.nodes[0].path == "pkg/core.py"
    assert snapshot.edges[0].kind == "calls" and snapshot.edges[0].origin == "inferred"
