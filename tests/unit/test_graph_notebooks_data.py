"""Notebook and data edges, and the projection that budgets and collapses them."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ananke.plexus.app import AppError, AppFacade
from ananke.plexus.graph.service import (
    build_graph,
    graph_digest,
    impact_from_paths,
    persist_graph,
)


def notebook(*cells: tuple[str, str], language: str = "python") -> str:
    return json.dumps(
        {
            "nbformat": 4,
            "metadata": {"kernelspec": {"language": language}},
            "cells": [{"cell_type": t, "source": s.splitlines(True)} for t, s in cells],
        }
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("")
    (tmp_path / "pkg" / "core.py").write_text(
        "import pandas as pd\n\n\ndef load():\n    return pd.read_csv('data/in.csv')\n\n\n"
        "def save(df):\n    df.to_parquet('out/result.parquet')\n"
    )
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "in.csv").write_text("a\n1\n")
    (tmp_path / "nb").mkdir()
    (tmp_path / "nb" / "study.ipynb").write_text(
        notebook(
            ("markdown", "# Study\n"),
            ("code", "%matplotlib inline\nimport pandas as pd\nfrom pkg.core import load\n"),
            ("code", "!pip install nothing\ndf = load()\nraw = pd.read_csv('../data/in.csv')\n"),
            ("code", "def helper(x):\n    return load()\n"),
            ("code", "%%bash\necho hi\n"),
            ("code", "def broken(\n"),
        )
    )
    return tmp_path


def by_id(snapshot):  # type: ignore[no-untyped-def]
    return {n.node_id: n for n in snapshot.nodes}


def test_a_notebook_is_a_file_whose_symbols_and_calls_cite_their_cell(repo: Path) -> None:
    g = build_graph(repo)
    nodes = by_id(g)
    meta = nodes["file:nb/study.ipynb"].metadata
    assert meta["notebook"] is True and meta["code_cells"] == 5
    assert meta["unparsed_cells"] == [5, 6]
    helper = nodes["function:nb/study.ipynb:helper"]
    assert helper.metadata["cell"] == 4 and helper.metadata["line"] == 1
    call = next(
        e
        for e in g.edges
        if e.source == "file:nb/study.ipynb" and e.target == "function:pkg/core.py:load"
    )
    assert call.kind == "calls" and call.origin == "inferred"
    assert call.source_location == "nb/study.ipynb#cell=3:2"


def test_adding_a_notebook_and_data_never_rekeys_python_symbols(
    repo: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    plain = tmp_path_factory.mktemp("plain")
    (plain / "pkg").mkdir()
    (plain / "pkg" / "core.py").write_text((repo / "pkg" / "core.py").read_text())
    (plain / "pkg" / "__init__.py").write_text("")
    before = {n.node_id for n in build_graph(plain).nodes if n.kind != "data"}
    after = {n.node_id for n in build_graph(repo).nodes}
    assert before <= after


def test_data_reads_and_writes_come_from_literal_paths_only(repo: Path, tmp_path: Path) -> None:
    (repo / "pkg" / "more.py").write_text(
        "import pandas as pd\nfrom pathlib import Path\n\n\ndef f(name):\n"
        "    pd.read_csv(name)\n"
        "    pd.read_csv(f'{name}.csv')\n"
        "    pd.read_csv('https://example.com/x.csv')\n"
        "    pd.read_csv('/etc/secret.csv')\n"
        "    pd.read_csv('../../../outside.csv')\n"
        "    open('notes.py')\n"
        "    open('log.jsonl', 'a')\n"
        "    Path('cfg.json').read_text()\n"
    )
    g = build_graph(repo)
    data = {n.node_id: n for n in g.nodes if n.kind == "data"}
    assert set(data) == {
        "data:data/in.csv",
        "data:out/result.parquet",
        "data:log.jsonl",
        "data:cfg.json",
    }
    assert data["data:data/in.csv"].metadata["exists"] is True
    assert data["data:out/result.parquet"].metadata["exists"] is False
    flows = {(e.source, e.kind, e.target) for e in g.edges if e.kind in ("reads", "writes")}
    assert ("function:pkg/core.py:load", "reads", "data:data/in.csv") in flows
    assert ("function:pkg/core.py:save", "writes", "data:out/result.parquet") in flows
    assert ("file:nb/study.ipynb", "reads", "data:data/in.csv") in flows


def test_every_edge_built_from_source_has_a_location(repo: Path) -> None:
    g = build_graph(repo)
    assert g.edges and all(e.source_location for e in g.edges)


def test_rebuilding_gives_the_same_content_digest(repo: Path) -> None:
    first, second = build_graph(repo), build_graph(repo)
    second.snapshot_id = "a-later-time"
    assert graph_digest(first) == graph_digest(second)
    (repo / "pkg" / "core.py").write_text("def other():\n    pass\n")
    assert graph_digest(build_graph(repo)) != graph_digest(first)


def test_impact_reaches_notebooks_and_readers_of_changed_data(repo: Path) -> None:
    persist_graph(repo, build_graph(repo))
    assert "nb/study.ipynb" in impact_from_paths(repo, ["pkg/core.py"]).impacted_files
    readers = impact_from_paths(repo, ["data/in.csv"]).impacted_files
    assert {"nb/study.ipynb", "pkg/core.py"} <= set(readers)


@pytest.mark.parametrize(
    "content",
    ["not json", "[]", json.dumps({"nbformat": 3, "worksheets": []})],
    ids=["not-json", "not-an-object", "old-format"],
)
def test_a_broken_notebook_is_reported_and_does_not_stop_the_build(
    repo: Path, content: str
) -> None:
    (repo / "nb" / "broken.ipynb").write_text(content)
    nodes = by_id(build_graph(repo))
    assert nodes["file:nb/broken.ipynb"].metadata["parse_error"] is True
    assert "function:pkg/core.py:load" in nodes


def test_a_notebook_in_another_language_is_listed_but_not_parsed_as_python(repo: Path) -> None:
    (repo / "nb" / "r.ipynb").write_text(notebook(("code", "x <- 1\n"), language="R"))
    g = build_graph(repo)
    assert by_id(g)["file:nb/r.ipynb"].metadata["language"] == "R"
    assert not [n for n in g.nodes if n.path == "nb/r.ipynb" and n.kind != "file"]


def test_magics_that_wrap_python_are_read_and_others_are_named(repo: Path) -> None:
    (repo / "nb" / "m.ipynb").write_text(
        notebook(
            ("code", "%%time\ndef timed():\n    return 1\n"),
            ("code", "result = await thing()\n"),
            ("code", "%%writefile pkg/gen.py\ndef generated():\n    pass\n"),
        )
    )
    nodes = by_id(build_graph(repo))
    assert "function:nb/m.ipynb:timed" in nodes
    assert nodes["function:nb/m.ipynb:timed"].metadata["cell"] == 1
    assert "function:nb/m.ipynb:generated" not in nodes
    meta = nodes["file:nb/m.ipynb"].metadata
    assert meta["unparsed_cells"] == [3] and meta["cell_magics"] == {"%%writefile": 1}


def test_nothing_in_a_notebook_runs(repo: Path) -> None:
    marker = repo / "ran.txt"
    (repo / "nb" / "evil.ipynb").write_text(
        notebook(("code", f"open({str(marker)!r}, 'w').write('x')\n"))
    )
    build_graph(repo)
    assert not marker.exists()


@pytest.fixture
def facade(repo: Path) -> AppFacade:
    persist_graph(repo, build_graph(repo))
    return AppFacade(repo)


def test_collapse_keeps_every_edge_accounted_for(facade: AppFacade) -> None:
    full = facade.code_view()
    assert not full["truncated"]
    for level in ("file", "directory"):
        view = facade.code_view(collapse=level)
        a = view["edge_accounting"]
        assert sum(e["count"] for e in view["edges"]) == a["shown"]
        assert a["shown"] + a["inside_a_group"] == full["edge_accounting"]["shown"]
        assert a["external_imports"] == full["edge_accounting"]["external_imports"]
    grouped = facade.code_view(collapse="directory")
    assert {n["node_id"] for n in grouped["nodes"]} >= {"dir:pkg", "dir:nb", "dir:data"}
    link = next(e for e in grouped["edges"] if (e["source"], e["target"]) == ("dir:nb", "dir:pkg"))
    assert link["kinds"]["calls"] >= 1 and link["origins"]["inferred"] >= 1
    assert all("#cell=" in where for where in link["locations"] if where.startswith("nb/"))


def test_a_budget_keeps_the_most_connected_and_says_what_it_left_out(facade: AppFacade) -> None:
    full = facade.code_view()
    small = facade.code_view(budget=2)
    assert small["truncated"] and len(small["nodes"]) == 2 and small["total_nodes"] > 2
    assert small["edge_accounting"]["to_nodes_not_shown"] > 0
    a = small["edge_accounting"]
    assert a["shown"] + a["to_nodes_not_shown"] == full["edge_accounting"]["shown"]
    assert facade.code_view(budget=2)["nodes"] == small["nodes"]


def test_nodes_carry_a_source_location_and_imports_resolve_to_files(facade: AppFacade) -> None:
    view = facade.code_view()
    where = {n["node_id"]: n["source_location"] for n in view["nodes"]}
    assert where["function:pkg/core.py:load"] == "pkg/core.py:4"
    assert where["function:nb/study.ipynb:helper"] == "nb/study.ipynb#cell=4:1"
    assert where["file:pkg/core.py"] == "pkg/core.py"
    edge = next(e for e in view["edges"] if e.get("via_module") == "pkg.core")
    assert edge["source"] == "file:nb/study.ipynb" and edge["target"] == "file:pkg/core.py"
    assert view["edge_accounting"]["external_imports"] >= 1


def test_the_projection_changes_nothing_and_rejects_bad_requests(
    facade: AppFacade, repo: Path
) -> None:
    before = (repo / ".ananke" / "graph" / "metadata.json").read_bytes()
    facade.code_view(collapse="file")
    assert (repo / ".ananke" / "graph" / "metadata.json").read_bytes() == before
    for bad in ({"budget": 0}, {"budget": 10**9}, {"collapse": "banana"}, {"depth": 0}):
        with pytest.raises(AppError) as exc:
            facade.code_view(**bad)  # type: ignore[arg-type]
        assert exc.value.code == "INVALID_REQUEST"
