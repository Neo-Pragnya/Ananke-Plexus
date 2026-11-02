"""Native graph: symbol metadata, methods, inferred calls and bases, and dependents-first impact."""

from __future__ import annotations

from pathlib import Path

from ananke.plexus.graph.service import build_graph, impact_from_paths, persist_graph


def _project(root: Path) -> Path:
    (root / "shop").mkdir(parents=True)
    (root / "shop" / "__init__.py").write_text("")
    (root / "shop" / "prices.py").write_text(
        '"""Prices."""\nimport json\n\n\ndef price(item: str) -> int:\n    """Look up a price."""\n    return len(json.dumps(item))\n'
    )
    (root / "shop" / "base.py").write_text("class Model:\n    def save(self):\n        return 1\n")
    (root / "shop" / "cart.py").write_text(
        "from shop.prices import price\n"
        "from shop.base import Model\n"
        "import shop.prices as p\n\n\n"
        "class Cart(Model):\n"
        '    """A cart."""\n\n'
        "    def total(self, items):\n"
        "        return sum(price(i) for i in items) + self.tax()\n\n"
        "    def tax(self):\n"
        "        return p.price('tax')\n\n\n"
        "def build():\n"
        "    return Cart.tax(None)\n\n\n"
        "def checkout(cart):\n"
        "    print('external call is not an edge')\n"
        "    return cart.total([])\n"
    )
    return root


def test_symbols_carry_lines_docs_and_signatures(tmp_path: Path) -> None:
    snap = build_graph(_project(tmp_path))
    by_id = {n.node_id: n for n in snap.nodes}
    fn = by_id["function:shop/prices.py:price"]
    assert fn.metadata["line"] == 5 and fn.metadata["doc"] == "Look up a price."
    assert fn.metadata["signature"] == "def price(item: str) -> int"
    assert by_id["file:shop/prices.py"].metadata == {
        "module": "shop.prices",
        "loc": 7,
        "doc": "Prices.",
    }
    assert by_id["method:shop/cart.py:Cart.total"].kind == "method"


def test_calls_resolve_through_imports_aliases_and_self(tmp_path: Path) -> None:
    snap = build_graph(_project(tmp_path))
    calls = {(e.source, e.target) for e in snap.edges if e.kind == "calls"}
    assert (
        "method:shop/cart.py:Cart.total",
        "function:shop/prices.py:price",
    ) in calls  # from-import
    assert (
        "method:shop/cart.py:Cart.total",
        "method:shop/cart.py:Cart.tax",
    ) in calls  # self.method
    assert (
        "method:shop/cart.py:Cart.tax",
        "function:shop/prices.py:price",
    ) in calls  # module alias
    assert ("function:shop/cart.py:build", "method:shop/cart.py:Cart.tax") in calls  # Cls.method()
    assert not any(
        t.endswith(":print") for _, t in calls
    )  # builtins and unknown names are not guessed
    inherits = {(e.source, e.target) for e in snap.edges if e.kind == "inherits"}
    assert ("class:shop/cart.py:Cart", "class:shop/base.py:Model") in inherits
    assert all(
        e.origin == "inferred" and e.confidence < 1
        for e in snap.edges
        if e.kind in ("calls", "inherits")
    )
    # Existing ids and edges are unchanged.
    assert any(e.kind == "imports" and e.target == "module:shop.prices" for e in snap.edges)


def test_impact_lists_dependents_not_dependencies(tmp_path: Path) -> None:
    root = _project(tmp_path)
    persist_graph(root, build_graph(root))
    report = impact_from_paths(root, ["shop/prices.py"])
    assert "shop/cart.py" in report.impacted_files  # cart imports and calls prices
    assert "shop/base.py" not in report.impacted_files
    assert "function:shop/prices.py:price" in report.impacted_symbols


def test_unparseable_files_are_kept_and_marked(tmp_path: Path) -> None:
    (tmp_path / "broken.py").write_text("def nope(:\n")
    snap = build_graph(tmp_path)
    [node] = snap.nodes
    assert node.metadata["parse_error"] is True


def test_virtualenvs_hidden_dirs_and_dependencies_are_skipped(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("x = 1\n")
    for d in (".acceptance-venv/lib", "venv/lib", "node_modules/pkg", ".git/hooks", "build_env"):
        (tmp_path / d).mkdir(parents=True)
        (tmp_path / d / "dep.py").write_text("y = 2\n")
    (tmp_path / "venv" / "pyvenv.cfg").write_text("home = /usr\n")
    (tmp_path / "build_env" / "pyvenv.cfg").write_text("home = /usr\n")
    paths = sorted(n.path for n in build_graph(tmp_path).nodes if n.kind == "file")
    assert paths == ["app.py"]
