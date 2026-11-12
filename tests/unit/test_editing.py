"""FUT-02/03: references by type or from the graph, and symbol edits that are dry runs until written."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from ananke.plexus.editing import EditError, insert, references, rename, replace


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    (tmp_path / "shop").mkdir()
    (tmp_path / "shop" / "__init__.py").write_text("")
    (tmp_path / "shop" / "cart.py").write_text(
        "class Cart:\n"
        "    def total(self, items):\n"
        "        # total is the sum\n"
        "        return sum(items)\n"
        "\n"
        "\n"
        "def helper():\n"
        "    return 'total'\n"
    )
    (tmp_path / "shop" / "checkout.py").write_text(
        "from shop.cart import Cart\n\n\ndef pay(items):\n    cart = Cart()\n    return cart.total(items)\n"
    )
    return tmp_path


def test_replace_is_a_dry_run_until_written(project: Path) -> None:
    before = (project / "shop" / "cart.py").read_text()
    new = "def total(self, items):\n    return round(sum(items), 2)\n"
    dry = replace(project, "Cart.total", new)
    assert dry["written"] is False and "+        return round(sum(items), 2)" in dry["diff"]
    assert (project / "shop" / "cart.py").read_text() == before
    done = replace(project, "Cart.total", new, write=True)
    text = (project / "shop" / "cart.py").read_text()
    assert (
        done["written"]
        and "    def total(self, items):\n        return round(sum(items), 2)\n" in text
    )
    assert "def helper" in text


def test_replace_refuses_unsafe_edits(project: Path) -> None:
    with pytest.raises(EditError, match="must define exactly 'total'"):
        replace(project, "Cart.total", "def other(self):\n    return 1\n")
    with pytest.raises(EditError, match="doesn't parse"):
        replace(project, "Cart.total", "def total(self:\n")
    with pytest.raises(EditError, match="no function"):
        replace(project, "nowhere", "def nowhere():\n    pass\n")
    (project / "shop" / "other.py").write_text("def helper():\n    return 2\n")
    with pytest.raises(EditError, match="names 2 definitions"):
        replace(project, "helper", "def helper():\n    return 3\n")


def test_a_file_changed_since_reading_is_not_overwritten(project: Path) -> None:
    from ananke.plexus.editing import _commit

    stale = "class Cart:\n    pass\n"  # what was read; the file on disk now differs
    with pytest.raises(EditError, match="changed since it was read"):
        _commit(project, {"shop/cart.py": (stale, "class Cart:\n    x = 1\n")}, write=True)
    assert "def total" in (project / "shop" / "cart.py").read_text()


def test_insert_after_a_method_keeps_its_indentation(project: Path) -> None:
    insert(
        project, "def count(self, items):\n    return len(items)\n", after="Cart.total", write=True
    )
    text = (project / "shop" / "cart.py").read_text()
    assert (
        "        return sum(items)\n\n    def count(self, items):\n        return len(items)\n"
        in text
    )
    with pytest.raises(EditError, match="exactly one"):
        insert(project, "x = 1\n", write=False)


def test_rename_from_the_graph_refuses_when_uses_are_unresolved(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ananke.plexus import editing

    monkeypatch.setattr(editing, "_jedi", lambda: None)
    refs = references(project, "Cart.total")
    assert refs["method"] == "graph" and refs["limits"]
    dry = rename(project, "Cart.total", "grand_total")
    assert dry["unresolved"] == [
        "shop/checkout.py:6"
    ]  # cart.total(...) on an instance: the graph can't tie it
    with pytest.raises(EditError, match="weren't resolved by the graph"):
        rename(project, "Cart.total", "grand_total", write=True)
    assert "def total" in (project / "shop" / "cart.py").read_text()  # nothing half-renamed


def test_rename_from_the_graph_touches_names_not_strings_or_comments(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ananke.plexus import editing

    monkeypatch.setattr(editing, "_jedi", lambda: None)
    result = rename(project, "helper", "make_label", write=True)
    cart = (project / "shop" / "cart.py").read_text()
    assert "def make_label():" in cart and "return 'total'" in cart and "# total is the sum" in cart
    assert result["files"] == ["shop/cart.py"] and result["unresolved"] == []
    with pytest.raises(EditError, match="isn't a valid Python name"):
        rename(project, "make_label", "class")


@pytest.mark.skipif(
    importlib.util.find_spec("jedi") is None, reason="jedi (the typed extra) isn't installed"
)
def test_rename_by_type_with_jedi(project: Path) -> None:
    (project / "shop" / "refund.py").write_text("def total(x):\n    return -x\n\n\ny = total(3)\n")
    refs = references(project, "Cart.total")
    assert refs["method"] == "jedi"
    assert {(r["path"], r["line"]) for r in refs["references"]} == {
        ("shop/cart.py", 2),
        ("shop/checkout.py", 6),
    }
    rename(project, "Cart.total", "grand_total", write=True)
    assert (
        "def total(x):" in (project / "shop" / "refund.py").read_text()
    )  # a different total, by type
