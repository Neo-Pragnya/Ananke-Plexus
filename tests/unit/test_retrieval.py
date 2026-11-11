"""CAP-context-retrieval-01..04: question → symbols → a bounded bundle, cached per snapshot, measured."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ananke.plexus.cli.app import app
from ananke.plexus.context import fresh_graph
from ananke.plexus.retrieval import _merge, ask, evaluate, plan, stem, words


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "shop").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "shop" / "__init__.py").write_text("")
    (tmp_path / "shop" / "pricing.py").write_text(
        'def apply_discount(total, tier):\n    """Lower the total for gold customers."""\n'
        "    return total * 0.8 if tier == 'gold' else total\n\n\n"
        "def order_total(items):\n    return apply_discount(sum(items), 'gold')\n"
    )
    (tmp_path / "shop" / "shipping.py").write_text("def ship(order):\n    return 'sent'\n")
    (tmp_path / "tests" / "test_pricing.py").write_text(
        "from shop.pricing import apply_discount\n\n\ndef test_apply_discount_gold():\n    assert apply_discount(10, 'gold') == 8\n"
    )
    return tmp_path


def test_words_split_names_and_stem() -> None:
    assert words("orderTotal and apply_discounts") == ["order", "total", "apply", "discount"]
    assert stem("promoted") == stem("promote") == stem("promotes") == "promot"
    assert stem("applied") == stem("apply") == "apply" and stem("shipped") == stem("ship") == "ship"


def test_plan_finds_the_symbol_and_says_why(repo: Path) -> None:
    found = plan("where is the gold discount applied", fresh_graph(repo))
    assert found[0]["name"] == "apply_discount" and found[0]["path"] == "shop/pricing.py"
    assert (
        found[0]["matched"]["name"] == ["apply", "discount"]
        and "gold" in found[0]["matched"]["doc"]
    )
    names = [c["name"] for c in found]
    assert names.index("apply_discount") < names.index(
        "test_apply_discount_gold"
    )  # tests rank lower
    assert plan("the and of", fresh_graph(repo)) == []


def test_ask_stays_in_budget_merges_and_adds_neighbours(repo: Path) -> None:
    out = ask(repo, "how is the order total discounted", budget=400, seeds=1)
    assert out["tokens"] <= 400 and out["passages"]
    assert out["passages"][0]["path"] == "shop/pricing.py"
    assert any(
        "calls apply_discount" in n or "called by order_total" in n for n in out["neighbours"]
    )
    assert _merge([("a.py", 1, 5), ("a.py", 4, 9), ("a.py", 20, 22), ("b.py", 1, 2)]) == [
        ("a.py", 1, 9),
        ("a.py", 20, 22),
        ("b.py", 1, 2),
    ]
    tight = ask(repo, "how is the order total discounted", budget=100)
    assert tight["tokens"] <= 100 or len(tight["passages"]) == 1  # one passage is always returned


def test_answers_are_cached_until_the_code_changes(repo: Path) -> None:
    first = ask(repo, "where is shipping done")
    assert not first["cached"] and ask(repo, "where is shipping done")["cached"]
    (repo / "shop" / "shipping.py").write_text("def ship(order):\n    return 'sent today'\n")
    assert not ask(repo, "where is shipping done")["cached"]


def test_evaluate_reports_recall_and_the_saving(repo: Path) -> None:
    out = evaluate(
        repo,
        [
            {"question": "where is the gold discount applied", "expect": ["apply_discount"]},
            {"question": "how is an order shipped", "expect": ["ship"]},
        ],
    )
    assert out["recall"] == 1.0 and out["baseline_tokens"] >= out["tokens"] > 0


def test_cli(repo: Path, tmp_path: Path) -> None:
    runner = CliRunner()
    res = runner.invoke(
        app, ["context", "ask", "where is the gold discount applied", "--project", str(repo)]
    )
    assert res.exit_code == 0 and "apply_discount" in res.output
    res = runner.invoke(app, ["context", "ask", "zebra quantum", "--project", str(repo)])
    assert res.exit_code == 1
    cases = tmp_path / "cases.json"
    cases.write_text(json.dumps([{"question": "how is an order shipped", "expect": ["ship"]}]))
    res = runner.invoke(app, ["context", "eval", str(cases), "--project", str(repo)])
    assert res.exit_code == 0 and json.loads(res.stdout)["recall"] == 1.0
    cases.write_text("{}")
    assert (
        runner.invoke(app, ["context", "eval", str(cases), "--project", str(repo)]).exit_code == 2
    )
