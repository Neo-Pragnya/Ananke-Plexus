"""The same repository gives the same graph on every operating system.

CI runs this on Linux, macOS and Windows. A path written with the platform's separator, rather than ``/``, broke
imports and packages on Windows while every other test passed, so the numbers here are exact.
"""

from __future__ import annotations

import collections
import shutil
from pathlib import Path

import pytest

from ananke.plexus.graph.service import build_graph
from ananke.plexus.intel import analyze

SHOP = Path(__file__).resolve().parent.parent / "fixtures" / "repo-mapping" / "shop"


@pytest.fixture
def shop(tmp_path: Path) -> Path:
    root = tmp_path / "shop"
    shutil.copytree(SHOP, root, ignore=shutil.ignore_patterns(".ananke", "__pycache__"))
    return root


def test_every_path_and_id_uses_forward_slashes(shop: Path) -> None:
    graph = build_graph(shop)
    texts = [n.node_id for n in graph.nodes] + [n.path for n in graph.nodes]
    texts += [e.source for e in graph.edges] + [e.target for e in graph.edges]
    texts += [str(getattr(e, "resolved_target", "") or "") for e in graph.edges]
    assert not [t for t in texts if "\\" in t]


def test_the_graph_is_the_same_on_every_platform(shop: Path) -> None:
    graph = build_graph(shop)
    nodes = collections.Counter(n.kind for n in graph.nodes)
    edges = collections.Counter(e.kind for e in graph.edges)
    resolved = [
        e for e in graph.edges if e.kind == "imports" and getattr(e, "resolved_target", None)
    ]
    assert dict(nodes) == {"file": 19, "function": 18, "class": 4, "method": 2, "data": 1}
    assert dict(edges) == {"owns": 24, "imports": 24, "calls": 12, "reads": 1}
    assert len(resolved) == 13
    files = {n.node_id for n in graph.nodes if n.kind == "file"}
    assert all(e.resolved_target in files for e in resolved)
    assert "file:backend/app/services/orders.py" in files


def test_the_analysis_names_files_the_same_way(shop: Path) -> None:
    report = analyze(shop)
    paths = [f["path"] for f in report["sections"]["quality"]["files"]]
    assert paths and not [p for p in paths if "\\" in p]
    assert "backend/app/services/orders.py" in paths
