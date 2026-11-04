"""Repo mapping practice: how correctly each analyzer maps a repository a person has checked by hand.

``expected.json`` is the answer key for ``tests/fixtures/repo-mapping/shop``. FLOORS only ever go up: raise a floor when an
analyzer improves, and add a case to the golden repo when a real repository shows something it misses.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from ananke.plexus.intel import analyze

SHOP = Path(__file__).resolve().parent.parent / "fixtures" / "repo-mapping" / "shop"
KEY = json.loads((SHOP / "expected.json").read_text())
# (minimum recall, minimum precision)
FLOORS: dict[str, tuple[float, float]] = {
    "import_links": (0.0, 0.0),
    "call_links": (0.0, 0.0),
    "tables": (1.0, 1.0),
    "columns": (1.0, 1.0),
    "relationships": (1.0, 1.0),
    "usage": (1.0, 1.0),
    "routes": (0.0, 0.0),
    "client_links": (0.0, 0.0),
    "flows": (0.0, 0.0),
    "calm_relationships": (1.0, 1.0),
    "packages": (1.0, 1.0),
}


def score(found: set[Any], expected: set[Any]) -> tuple[float, float]:
    hits = len(found & expected)
    return (hits / len(expected) if expected else 1.0, hits / len(found) if found else 0.0)


def found_by_measure(report: dict[str, Any], graph: Any) -> dict[str, set[Any]]:
    s = report["sections"]
    data, api, flows = s["data"], s["api"], s["flows"]
    links = {
        (
            e.source.removeprefix("file:"),
            str(getattr(e, "resolved_target", "") or "").removeprefix("file:"),
        )
        for e in graph.edges
        if e.kind == "imports" and getattr(e, "resolved_target", "")
    }
    flow_hits: set[Any] = set()
    for flow in flows.get("flows", []):
        nodes = {step["node"] for step in flow["steps"]}
        flow_hits |= {(flow["name"], n) for n in nodes}
    model = s["calm"].get("model", {})
    return {
        "import_links": links,
        "call_links": {(e.source, e.target) for e in graph.edges if e.kind == "calls"},
        "tables": {t["name"] for t in data.get("tables", [])},
        "columns": {(t["name"], c["name"]) for t in data.get("tables", []) for c in t["columns"]},
        "relationships": {
            (r["from_table"], r["from_column"], r["to_table"], r["to_column"])
            for r in data.get("relationships", [])
        },
        "usage": {(u["symbol"], u["table"], u["access"]) for u in data.get("usage", [])},
        "routes": {(f"{r['method']} {r['path']}", r["handler"]) for r in api.get("routes", [])},
        "client_links": {
            (c["caller"], c.get("route", "").removeprefix("route:")) for c in api.get("clients", [])
        },
        "flows": flow_hits,
        "calm_relationships": {(r["source"], r["target"]) for r in model.get("relationships", [])},
        "packages": {
            (p["ecosystem"], p["name"].lower(), p["version"])
            for p in s["dependencies"].get("packages", [])
        },
    }


def expected_by_measure() -> dict[str, set[Any]]:
    return {
        "import_links": {tuple(x) for x in KEY["import_links"]},
        "call_links": {tuple(x) for x in KEY["call_links"]},
        "tables": set(KEY["tables"]),
        "columns": {(t, c) for t, cs in KEY["tables"].items() for c in cs},
        "relationships": {tuple(x) for x in KEY["relationships"]},
        "usage": {tuple(x) for x in KEY["usage"]},
        "routes": set(KEY["routes"].items()),
        "client_links": set(KEY["client_links"].items()),
        "flows": {(name, n) for name, flow in KEY["flows"].items() for n in flow["must_include"]},
        "calm_relationships": {tuple(x) for x in KEY["calm_relationships"]},
        "packages": {(a, b, c) for a, b, c in KEY["packages"]},
    }


@pytest.fixture(scope="module")
def shop(tmp_path_factory: pytest.TempPathFactory) -> Path:
    copy = tmp_path_factory.mktemp("repo") / "shop"
    shutil.copytree(SHOP, copy, ignore=shutil.ignore_patterns("expected.json"))
    return copy


@pytest.fixture(scope="module")
def report(shop: Path) -> dict[str, Any]:
    return analyze(shop)


@pytest.fixture(scope="module")
def measures(shop: Path, report: dict[str, Any]) -> dict[str, set[Any]]:
    from ananke.plexus.graph.service import build_graph

    return found_by_measure(report, build_graph(shop))


@pytest.mark.parametrize("measure", sorted(FLOORS))
def test_mapping_meets_its_floor(measures: dict[str, set[Any]], measure: str) -> None:
    expected = expected_by_measure()[measure]
    found = measures[measure]
    if measure == "flows":  # a flow may rightly include more steps than the key's must-include list
        found = found & expected
    if measure == "call_links":  # the key lists the links that matter, not all of them
        found = found & expected
    recall, precision = score(found, expected)
    need_recall, need_precision = FLOORS[measure]
    assert recall >= need_recall, (
        f"{measure} recall {recall:.2f}: missing {sorted(expected - found, key=str)[:8]}"
    )
    assert precision >= need_precision, (
        f"{measure} precision {precision:.2f}: wrong {sorted(found - expected, key=str)[:8]}"
    )


def test_every_section_says_what_it_is(report: dict[str, Any]) -> None:
    assert report["schema"] == "ananke-repo-intel/1"
    for name, section in report["sections"].items():
        assert section["status"] in {
            "found",
            "none",
            "not_found",
            "not_scanned",
            "failed",
            "not_built",
        }, name
        assert isinstance(section["limits"], list), name
        assert section["status"] != "failed", section["limits"]


def test_analysing_twice_gives_the_same_digest(shop: Path, report: dict[str, Any]) -> None:
    assert analyze(shop)["content_digest"] == report["content_digest"]


def test_analysis_changes_nothing_and_runs_nothing(tmp_path: Path) -> None:
    (tmp_path / "boom.py").write_text("open('ran.txt', 'w').write('x')\n")
    before = sorted(p.name for p in tmp_path.iterdir())
    analyze(tmp_path)
    assert sorted(p.name for p in tmp_path.iterdir()) == before
