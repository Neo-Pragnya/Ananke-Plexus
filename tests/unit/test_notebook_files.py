"""Code that lives in notebook cells counts, without running or creating anything."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from ananke.plexus.app import AppFacade
from ananke.plexus.context import callers, outline, symbol
from ananke.plexus.graph.notebook_files import find_written
from ananke.plexus.graph.service import build_graph
from ananke.plexus.intel import analyze


def _notebook(path: Path, *cells: tuple[str, str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "cells": [
            {"cell_type": kind, "source": text, "metadata": {}, "outputs": []}
            for kind, text in cells
        ],
        "metadata": {"kernelspec": {"language": "python"}},
        "nbformat": 4,
    }
    path.write_text(json.dumps(payload))
    return path


APP = """%%writefile app/service.py
from fastapi import FastAPI
from app.store import load_orders

app = FastAPI()

@app.get("/orders")
def orders():
    return load_orders()
"""
STORE = """%%writefile app/store.py
import pandas as pd

def load_orders():
    return pd.read_parquet("data/orders.parquet")
"""


@pytest.fixture
def course(tmp_path: Path) -> Path:
    root = tmp_path / "course"
    _notebook(
        root / "Week-2" / "build.ipynb",
        (
            "markdown",
            "# Build the service\n\nWe turn the analysis into an API.\n\n## The store\n\nReads orders.\n",
        ),
        ("code", APP),
        ("code", STORE),
        ("code", "%%writefile requirements.txt\nfastapi==0.115.0\n"),
        (
            "code",
            "%%writefile schema.sql\nCREATE TABLE orders (id INT PRIMARY KEY, total NUMERIC);\n",
        ),
    )
    _notebook(
        root / "Week-3" / "use.ipynb",
        ("markdown", "# Use it\n"),
        (
            "code",
            "import sys\nsys.path.append('../Week-2')\nfrom app.store import load_orders\nload_orders()\n",
        ),
    )
    return root


def test_written_files_are_found_where_the_cell_would_put_them(course: Path) -> None:
    found = find_written(course, sorted(course.rglob("*.ipynb")))
    assert {
        "Week-2/app/service.py",
        "Week-2/app/store.py",
        "Week-2/requirements.txt",
        "Week-2/schema.sql",
    } <= set(found.files)
    assert found.files["Week-2/app/store.py"].cell == 3


def test_nothing_is_created_or_run(course: Path) -> None:
    before = sorted(p for p in course.rglob("*"))
    _notebook(course / "evil.ipynb", ("code", "%%writefile pwned.py\nopen('ran', 'w').close()\n"))
    before = sorted(p for p in course.rglob("*"))
    analyze(course)
    after = sorted(p for p in course.rglob("*") if ".ananke" not in p.parts)
    assert after == before
    assert not (course / "ran").exists()


def test_the_graph_sees_written_code_and_links_it_to_its_notebook(course: Path) -> None:
    graph = build_graph(course)
    names = {n.name for n in graph.nodes if n.kind == "function"}
    assert {"orders", "load_orders"} <= names
    node = next(n for n in graph.nodes if n.path == "Week-2/app/store.py" and n.kind == "file")
    assert (
        node.metadata["written_by"] == "Week-2/build.ipynb" and node.metadata["written_cell"] == 3
    )
    assert any(
        e.kind == "generates"
        and e.source == "file:Week-2/build.ipynb"
        and e.target == "file:Week-2/app/store.py"
        for e in graph.edges
    )


def test_every_section_sees_written_files(course: Path) -> None:
    s = analyze(course)["sections"]
    assert any(r["path"] == "/orders" for r in s["api"]["routes"])
    assert any(t["name"] == "orders" for t in s["data"]["tables"])
    assert any(p["name"] == "fastapi" for p in s["dependencies"]["packages"])
    assert s["quality"]["totals"]["functions"] >= 2
    assert any(f["id"].endswith("/orders") for f in s["flows"]["flows"])


def test_a_notebook_is_explained_from_its_own_words(course: Path) -> None:
    n = analyze(course)["sections"]["notebooks"]
    entry = next(e for e in n["notebooks"] if e["path"] == "Week-2/build.ipynb")
    assert entry["title"] == "Build the service"
    assert entry["purpose"] == "We turn the analysis into an API."
    assert [o["title"] for o in entry["outline"]] == ["Build the service", "The store"]
    assert entry["cells"] == {"code": 4, "markdown": 1}
    assert {"Week-2/app/store.py", "Week-2/schema.sql"} <= {w["path"] for w in entry["written"]}
    store = next(w for w in entry["written"] if w["path"] == "Week-2/app/store.py")
    assert store["symbols"] == ["load_orders"] and store["cell"] == 3
    assert [g["folder"] for g in n["groups"]] == ["Week-2", "Week-3"]


def test_notebooks_are_linked_by_the_data_they_share(tmp_path: Path) -> None:
    _notebook(
        tmp_path / "a.ipynb",
        ("code", "import pandas as pd\npd.DataFrame().to_parquet('shared.parquet')\n"),
    )
    _notebook(
        tmp_path / "b.ipynb", ("code", "import pandas as pd\npd.read_parquet('shared.parquet')\n")
    )
    links = analyze(tmp_path)["sections"]["notebooks"]["links"]
    assert links == [
        {
            "from": "a.ipynb",
            "to": "b.ipynb",
            "via": "shared.parquet",
            "kind": "reads what it writes",
        }
    ]


def test_a_copy_is_marked_rather_than_counted_twice(course: Path) -> None:
    (course / "Week-2" / "build (Executed).ipynb").write_bytes(
        (course / "Week-2" / "build.ipynb").read_bytes()
    )
    entries: list[dict[str, Any]] = analyze(course)["sections"]["notebooks"]["notebooks"]
    marks = {e["path"]: e["same_as"] for e in entries}
    assert sorted(v for v in marks.values() if v) == ["Week-2/build (Executed).ipynb"] or sorted(
        v for v in marks.values() if v
    ) == ["Week-2/build.ipynb"]


def test_a_real_file_wins(course: Path) -> None:
    (course / "Week-2" / "app").mkdir()
    (course / "Week-2" / "app" / "store.py").write_text("def on_disk():\n    return 1\n")
    found = find_written(course, sorted(course.rglob("*.ipynb")))
    assert "Week-2/app/store.py" in found.on_disk and "Week-2/app/store.py" not in found.files
    assert "on_disk" in {n.name for n in build_graph(course).nodes}


def test_later_cells_replace_and_append_adds(tmp_path: Path) -> None:
    _notebook(
        tmp_path / "n.ipynb",
        ("code", "%%writefile x.py\nfirst = 1\n"),
        ("code", "%%writefile x.py\nsecond = 2\n"),
        ("code", "%%writefile -a x.py\nthird = 3\n"),
    )
    text = find_written(tmp_path, [tmp_path / "n.ipynb"]).text("x.py")
    assert text == "second = 2\nthird = 3\n"


@pytest.mark.parametrize(
    ("line", "reason"),
    [
        ("%%writefile ../../outside.py", "outside"),
        ("%%writefile /etc/passwd", "outside"),
        ("%%writefile {name}.py", "computed"),
        ("%%writefile $name.py", "computed"),
    ],
)
def test_paths_it_cant_trust_are_named_not_read(tmp_path: Path, line: str, reason: str) -> None:
    _notebook(tmp_path / "n.ipynb", ("code", f"{line}\nx = 1\n"))
    found = find_written(tmp_path, [tmp_path / "n.ipynb"])
    assert not found.files
    assert reason in " ".join(found.limits)


def test_a_hostile_notebook_cant_stop_the_analysis(tmp_path: Path) -> None:
    (tmp_path / "broken.ipynb").write_text("{not json")
    (tmp_path / "odd.ipynb").write_text('{"cells": 7}')
    _notebook(tmp_path / "bad.ipynb", ("code", "%%writefile bad.py\ndef broken(:\n"))
    report = analyze(tmp_path)
    assert report["sections"]["notebooks"]["status"] == "found"
    assert all(s.get("status") != "failed" for s in report["sections"].values())


def test_lookups_and_source_reading_work_on_written_files(course: Path) -> None:
    assert "load_orders" in outline(course, "Week-2/app/store.py")["text"]
    assert "read_parquet" in symbol(course, "load_orders")["text"]
    assert "orders" in callers(course, "load_orders")["text"]
    app = AppFacade(course)
    assert any("read_parquet" in line for line in app.read_source("Week-2/app/store.py")["lines"])


def test_a_folder_with_no_notebooks_says_so(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("x = 1\n")
    assert analyze(tmp_path)["sections"]["notebooks"]["status"] == "none"
