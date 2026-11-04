"""Python imports resolve to files: source roots, relative imports, aliases, and no guessing."""

from __future__ import annotations

from pathlib import Path

from ananke.plexus.graph.models import GraphEdge, GraphSnapshot
from ananke.plexus.graph.python_imports import ModuleIndex
from ananke.plexus.graph.service import (
    build_graph,
    graph_digest,
    impact_from_paths,
    persist_graph,
)


def write(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def links(snapshot: GraphSnapshot) -> set[tuple[str, str]]:
    return {
        (e.source.removeprefix("file:"), e.resolved_target.removeprefix("file:"))
        for e in snapshot.edges
        if e.kind == "imports" and e.resolved_target
    }


def imports_of(snapshot: GraphSnapshot, rel: str) -> list[GraphEdge]:
    return [e for e in snapshot.edges if e.kind == "imports" and e.source == f"file:{rel}"]


def calls(snapshot: GraphSnapshot) -> set[tuple[str, str]]:
    return {(e.source, e.target) for e in snapshot.edges if e.kind == "calls"}


def test_a_package_in_a_subfolder_makes_that_folder_a_source_root(tmp_path: Path) -> None:
    write(
        tmp_path,
        {
            "backend/app/__init__.py": "",
            "backend/app/db.py": "def get(): ...\n",
            "backend/app/main.py": "from app.db import get\n\ndef run():\n    return get()\n",
        },
    )
    snap = build_graph(tmp_path)
    assert links(snap) == {("backend/app/main.py", "backend/app/db.py")}
    edge = imports_of(snap, "backend/app/main.py")[0]
    assert edge.target == "module:app.db"
    assert edge.source_location == "backend/app/main.py:1"
    assert (
        "function:backend/app/main.py:run",
        "function:backend/app/db.py:get",
    ) in calls(snap)
    meta = next(n.metadata for n in snap.nodes if n.path == "backend/app/db.py")
    assert meta["source_root"] == "backend"
    assert meta["module"] == "backend.app.db"  # as before: ids and existing metadata don't move


def test_source_roots_come_from_the_code_not_from_names() -> None:
    index = ModuleIndex(
        [
            "services/api/orders/__init__.py",
            "services/api/orders/core.py",
            "scripts/tool.py",
            "tooling/setup.py",
        ],
        declared_roots=["tooling"],
    )
    assert index.source_roots() == ["services/api", "tooling"]


def test_relative_imports_resolve_at_every_level(tmp_path: Path) -> None:
    write(
        tmp_path,
        {
            "app/__init__.py": "",
            "app/db.py": "def session(): ...\n",
            "app/routers/__init__.py": "",
            "app/routers/orders.py": "from ..services.orders import create\n\ndef post():\n    return create()\n",
            "app/routers/peer.py": "from . import orders\nfrom .orders import post as send\n\ndef f():\n    orders.post()\n    send()\n",
            "app/services/__init__.py": "",
            "app/services/orders.py": "from ..db import session\n\ndef create():\n    return session()\n",
        },
    )
    snap = build_graph(tmp_path)
    assert links(snap) == {
        ("app/routers/orders.py", "app/services/orders.py"),
        ("app/routers/peer.py", "app/routers/orders.py"),
        ("app/services/orders.py", "app/db.py"),
    }
    assert {t for s, t in calls(snap) if s == "function:app/routers/peer.py:f"} == {
        "function:app/routers/orders.py:post"
    }
    assert ("function:app/routers/orders.py:post", "function:app/services/orders.py:create") in calls(snap)  # fmt: skip


def test_a_relative_import_beyond_the_repository_root_stays_unresolved(tmp_path: Path) -> None:
    write(tmp_path, {"mod.py": "from ...outside import thing\nfrom .. import other\n"})
    (tmp_path.parent / "outside.py").write_text("def thing(): ...\n", encoding="utf-8")
    snap = build_graph(tmp_path)
    assert links(snap) == set()
    assert {e.target for e in imports_of(snap, "mod.py")} == {"module:...outside", "module:.."}
    assert all(e.source_location for e in imports_of(snap, "mod.py"))


def test_from_a_package_import_a_module_links_the_module_only(tmp_path: Path) -> None:
    write(
        tmp_path,
        {
            "pkg/__init__.py": "VERSION = 1\n",
            "pkg/one.py": "def a(): ...\n",
            "pkg/two.py": "def b(): ...\n",
            "main.py": "from pkg import one, two as second\n\ndef go():\n    one.a()\n    second.b()\n",
            "version.py": "from pkg import VERSION\n",
        },
    )
    snap = build_graph(tmp_path)
    assert links(snap) == {
        ("main.py", "pkg/one.py"),
        ("main.py", "pkg/two.py"),
        ("version.py", "pkg/__init__.py"),
    }
    assert {t for s, t in calls(snap) if s == "function:main.py:go"} == {
        "function:pkg/one.py:a",
        "function:pkg/two.py:b",
    }


def test_import_as_and_dotted_chains_reach_calls_and_bases(tmp_path: Path) -> None:
    write(
        tmp_path,
        {
            "src/core/__init__.py": "",
            "src/core/deep/__init__.py": "",
            "src/core/deep/mod.py": "class Base:\n    def m(self): ...\n\ndef f(): ...\n",
            "tests/check.py": (
                "import core.deep.mod\n"
                "import core.deep.mod as short\n"
                "\n"
                "class Child(short.Base): ...\n"
                "\n"
                "def go():\n"
                "    core.deep.mod.f()\n"
                "    short.Base.m(None)\n"
            ),
        },
    )
    snap = build_graph(tmp_path)
    assert links(snap) == {("tests/check.py", "src/core/deep/mod.py")}
    assert {t for s, t in calls(snap) if s == "function:tests/check.py:go"} == {
        "function:src/core/deep/mod.py:f",
        "method:src/core/deep/mod.py:Base.m",
    }
    assert any(
        e.kind == "inherits"
        and e.source == "class:tests/check.py:Child"
        and e.target == "class:src/core/deep/mod.py:Base"
        and e.source_location
        for e in snap.edges
    )


def test_a_re_export_is_followed_to_its_definition(tmp_path: Path) -> None:
    write(
        tmp_path,
        {
            "pkg/__init__.py": "from .inner import work\n",
            "pkg/inner.py": "def work(): ...\n",
            "main.py": "from pkg import work\n\ndef go():\n    work()\n",
            "loop_a.py": "from loop_b import x\n",
            "loop_b.py": "from loop_a import x\n\ndef y():\n    x()\n",
        },
    )
    assert ("function:main.py:go", "function:pkg/inner.py:work") in calls(build_graph(tmp_path))


def test_the_same_root_wins_over_another_match(tmp_path: Path) -> None:
    write(
        tmp_path,
        {
            "demo1/agents/__init__.py": "",
            "demo1/agents/core.py": "def run(): ...\n",
            "demo1/main.py": "from agents.core import run\n",
            "demo2/agents/__init__.py": "",
            "demo2/agents/core.py": "def run(): ...\n",
            "demo2/main.py": "from agents.core import run\n",
        },
    )
    assert links(build_graph(tmp_path)) == {
        ("demo1/main.py", "demo1/agents/core.py"),
        ("demo2/main.py", "demo2/agents/core.py"),
    }


def test_two_equal_matches_are_recorded_as_ambiguous_not_guessed(tmp_path: Path) -> None:
    write(
        tmp_path,
        {
            "demo1/agents/__init__.py": "",
            "demo1/agents/core.py": "def run(): ...\n",
            "demo2/agents/__init__.py": "",
            "demo2/agents/core.py": "def run(): ...\n",
            "tools/use.py": "from agents.core import run\n\ndef go():\n    run()\n",
        },
    )
    snap = build_graph(tmp_path)
    edge = imports_of(snap, "tools/use.py")[0]
    assert edge.origin == "ambiguous"
    assert edge.resolved_target == ""
    assert edge.target == "module:agents.core"
    assert edge.source_location == "tools/use.py:1"
    assert not any(s == "function:tools/use.py:go" for s, _ in calls(snap))


def test_a_third_party_or_standard_library_name_is_not_a_local_file(tmp_path: Path) -> None:
    write(
        tmp_path,
        {
            "app/__init__.py": "",
            "app/redis.py": "import redis\n",  # a wrapper over the real client, not an import of itself
            "app/logging.py": "",
            "app/main.py": "import logging\nimport redis\n",
            "other/deep.py": "import utils\n",
            "elsewhere/utils.py": "",
        },
    )
    assert links(build_graph(tmp_path)) == set()


def test_a_build_copy_does_not_compete_with_the_source(tmp_path: Path) -> None:
    write(
        tmp_path,
        {
            "src/shop/__init__.py": "",
            "src/shop/cart.py": "",
            "build/lib/shop/__init__.py": "",
            "build/lib/shop/cart.py": "",
            "tests/test_cart.py": "import shop.cart\n",
        },
    )
    assert links(build_graph(tmp_path)) == {("tests/test_cart.py", "src/shop/cart.py")}


def test_a_folder_without_init_is_still_importable(tmp_path: Path) -> None:
    write(
        tmp_path,
        {
            "backend/app/db.py": "def get(): ...\n",
            "backend/main.py": "from app.db import get\nfrom app import db\n",
            "tests/use.py": "from app.db import get\n",
            "scripts/a.py": "import b\n",
            "scripts/b.py": "",
        },
    )
    assert links(build_graph(tmp_path)) == {
        ("backend/main.py", "backend/app/db.py"),
        ("tests/use.py", "backend/app/db.py"),
        ("scripts/a.py", "scripts/b.py"),
    }


def test_imports_inside_blocks_and_functions_count(tmp_path: Path) -> None:
    write(
        tmp_path,
        {
            "a.py": "def helper(): ...\n",
            "b.py": "try:\n    from a import helper\nexcept ImportError:\n    helper = None\n",
            "c.py": "def go():\n    from a import helper\n    return helper()\n",
        },
    )
    snap = build_graph(tmp_path)
    assert links(snap) == {("b.py", "a.py"), ("c.py", "a.py")}
    assert ("function:c.py:go", "function:a.py:helper") in calls(snap)


def test_a_notebook_imports_its_neighbour(tmp_path: Path) -> None:
    source = '{"cells": [{"cell_type": "code", "source": ["from helpers import f\\n", "f()"]}], "metadata": {}, "nbformat": 4}'
    write(tmp_path, {"study/nb.ipynb": source, "study/helpers.py": "def f(): ...\n"})
    snap = build_graph(tmp_path)
    assert links(snap) == {("study/nb.ipynb", "study/helpers.py")}
    assert ("file:study/nb.ipynb", "function:study/helpers.py:f") in calls(snap)


def test_existing_symbol_ids_do_not_change(tmp_path: Path) -> None:
    write(tmp_path, {"pkg/__init__.py": "", "pkg/m.py": "class C:\n    def f(self): ...\n\ndef g(): ...\n"})  # fmt: skip
    ids = {n.node_id for n in build_graph(tmp_path).nodes}
    assert {
        "file:pkg/m.py",
        "class:pkg/m.py:C",
        "method:pkg/m.py:C.f",
        "function:pkg/m.py:g",
    } <= ids


def test_impact_reaches_importers_through_resolved_links(tmp_path: Path) -> None:
    write(
        tmp_path,
        {
            "backend/app/__init__.py": "",
            "backend/app/db.py": "CONST = 1\n",
            "backend/app/one.py": "from app.db import CONST\n",
            "backend/app/two.py": "from .db import CONST\n",
            "backend/app/free.py": "",
        },
    )
    persist_graph(tmp_path, build_graph(tmp_path))
    report = impact_from_paths(tmp_path, ["backend/app/db.py"])
    assert report.impacted_files == ["backend/app/one.py", "backend/app/two.py"]


def test_the_code_view_draws_resolved_imports(tmp_path: Path) -> None:
    from ananke.plexus.app import AppFacade

    write(
        tmp_path,
        {
            "backend/app/__init__.py": "",
            "backend/app/db.py": "",
            "backend/app/main.py": "from app.db import x\nimport fastapi\n",
        },
    )
    persist_graph(tmp_path, build_graph(tmp_path))
    view = AppFacade(tmp_path).code_view()
    drawn = [e for e in view["edges"] if e["kind"] == "imports"]
    assert [(e["source"], e["target"], e["via_module"]) for e in drawn] == [
        ("file:backend/app/main.py", "file:backend/app/db.py", "app.db")
    ]
    assert view["edge_accounting"]["external_imports"] == 1


def test_rebuilds_give_the_same_digest(tmp_path: Path) -> None:
    write(tmp_path, {"a/x/__init__.py": "", "a/x/m.py": "from . import n\n", "a/x/n.py": "import x.m\n"})  # fmt: skip
    assert graph_digest(build_graph(tmp_path)) == graph_digest(build_graph(tmp_path))
