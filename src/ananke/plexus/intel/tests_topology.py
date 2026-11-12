"""Test topology (three-repo plan: CAP-repo-autodocs-01): how the repository is tested, from its files.

* **Suites:** test files grouped by kind, from their folder and name (unit, integration, end-to-end, acceptance,
  contract, performance, other), with the framework each uses (pytest, unittest, Jest, Vitest, Mocha, Playwright,
  Cypress, behave or pytest-bdd, Go testing) and its test count.
* **What each suite exercises:** the project's own files its tests import, from the code graph.
* **Untested code:** source files no test file imports, directly or through one helper. That's *not imported by
  a test*, not *untested*: a test can reach code through an HTTP call or a subprocess, so it's labelled inferred.
* **Shared setup:** ``conftest.py``, fixtures folders and test configuration files.

Nothing is run. Coverage, when there's a report, is in the coverage section and is the stronger evidence.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from ananke.plexus.intel._common import is_test_file
from ananke.plexus.intel.context import RepoContext

MAX_LISTED = 50
_KINDS = (
    (
        "end-to-end",
        re.compile(r"(^|/)(e2e|end[-_]?to[-_]?end|playwright|cypress)(/|$)|\.e2e\.", re.I),
    ),
    ("acceptance", re.compile(r"(^|/)(acceptance|features|bdd|behave)(/|$)|\.feature$", re.I)),
    ("integration", re.compile(r"(^|/)(integration|it|integ)(/|$)|_integration|\.int\.", re.I)),
    ("contract", re.compile(r"(^|/)(contract|pact|api_contract)(/|$)", re.I)),
    ("performance", re.compile(r"(^|/)(perf|performance|load|benchmarks?)(/|$)", re.I)),
    ("unit", re.compile(r"(^|/)(unit|units)(/|$)", re.I)),
)
_FRAMEWORKS = (
    ("pytest-bdd", re.compile(r"\bfrom pytest_bdd\b|\bimport pytest_bdd\b")),
    ("behave", re.compile(r"\bfrom behave\b")),
    ("playwright", re.compile(r"@playwright/test|from playwright\b")),
    ("cypress", re.compile(r"\bcy\.(visit|get)\(")),
    ("vitest", re.compile(r"from ['\"]vitest['\"]")),
    ("jest", re.compile(r"\bjest\.(fn|mock)\(|from ['\"]@jest/globals['\"]")),
    ("mocha", re.compile(r"\bfrom ['\"]mocha['\"]|require\(['\"]mocha['\"]\)")),
    ("unittest", re.compile(r"\bunittest\.TestCase\b")),
    ("pytest", re.compile(r"\bimport pytest\b|\bfrom pytest\b|^def test_", re.M)),
    ("go testing", re.compile(r"\*testing\.T\b")),
)
_COUNT = (
    re.compile(r"^\s*(?:async\s+)?def (test_\w+)", re.M),
    re.compile(r"^\s*(?:it|test)(?:\.each\([^)]*\))?\(\s*['\"`]", re.M),
    re.compile(r"^\s*Scenario(?: Outline)?:", re.M),
    re.compile(r"^func (Test\w+)\(", re.M),
)
_SETUP = re.compile(
    r"(^|/)(conftest\.py|pytest\.ini|tox\.ini|jest\.config\.[jt]s|vitest\.config\.[jt]s|"
    r"playwright\.config\.[jt]s|cypress\.config\.[jt]s|karma\.conf\.js)$|(^|/)fixtures?/"
)
_SOURCE = (".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".mjs")


def _kind(path: str) -> str:
    for name, pattern in _KINDS:
        if pattern.search(path):
            return name
    return "unit" if re.search(r"(^|/)tests?/", path) else "other"


def analyze(ctx: RepoContext) -> dict[str, Any]:
    tests = [
        p
        for p in ctx.files
        if (is_test_file(p) or p.endswith(".feature")) and p.endswith((*_SOURCE, ".feature"))
    ]
    setup = sorted(p for p in ctx.files if _SETUP.search(p))
    if not tests:
        return {
            "status": "none",
            "limits": ["no test files were found"],
            "suites": [],
            "files": 0,
            "tests": 0,
            "untested": [],
            "setup": setup,
        }
    imports: dict[str, set[str]] = defaultdict(set)
    for e in ctx.graph.edges:
        if e.kind == "imports":
            target = getattr(e, "resolved_target", "") or ""
            if target.startswith("file:") and e.source.startswith("file:"):
                imports[e.source[5:]].add(target[5:])
    suites: dict[tuple[str, str], dict[str, Any]] = {}
    exercised_all: set[str] = set()
    for path in sorted(tests):
        text = ctx.text(path) or ""
        framework = next((name for name, pattern in _FRAMEWORKS if pattern.search(text)), "unknown")
        count = sum(len(p.findall(text)) for p in _COUNT)
        if path.endswith(".feature"):
            framework = "gherkin"
        elif framework == "unknown" and path.endswith(".py") and count:
            framework = "pytest"  # plain test functions or methods: what pytest collects
        direct = {f for f in imports.get(path, set()) if not is_test_file(f)}
        helpers = {f for f in imports.get(path, set()) if is_test_file(f) or "/fixtures" in f}
        through = {g for h in helpers for g in imports.get(h, set()) if not is_test_file(g)}
        exercised = direct | through
        exercised_all |= exercised
        key = (_kind(path), framework)
        suite = suites.setdefault(
            key, {"kind": key[0], "framework": key[1], "files": [], "tests": 0, "exercises": set()}
        )
        suite["files"].append(path)
        suite["tests"] += count
        suite["exercises"] |= exercised
    sources = sorted(
        p
        for p in ctx.files
        if p.endswith(_SOURCE)
        and not is_test_file(p)
        and not p.rsplit("/", 1)[-1].startswith(("__init__", "setup.", "conftest"))
        and "/fixtures/" not in f"/{p}"
        and p.rsplit("/", 1)[-1] not in {"manage.py"}
    )
    untested = [p for p in sources if p not in exercised_all]
    rows = [
        {
            "kind": s["kind"],
            "framework": s["framework"],
            "files": len(s["files"]),
            "tests": s["tests"],
            "examples": s["files"][:5],
            "exercises": sorted(s["exercises"])[:MAX_LISTED],
            "exercises_count": len(s["exercises"]),
        }
        for s in sorted(suites.values(), key=lambda s: (s["kind"], s["framework"]))
    ]
    limits = [
        "A source file 'not imported by a test' may still be tested through HTTP, a subprocess or a notebook; "
        "it's inferred from imports, and coverage is the stronger evidence.",
        "Test counts are read from definitions (def test_, it(), test(), Scenario:); generated or parametrised cases "
        "count once.",
    ]
    if len(untested) > MAX_LISTED:
        limits.append(
            f"only the first {MAX_LISTED} of {len(untested)} source files no test imports are listed"
        )
    return {
        "status": "found",
        "limits": limits,
        "files": len(tests),
        "tests": sum(r["tests"] for r in rows),
        "suites": rows,
        "source_files": len(sources),
        "imported_by_tests": len(sources) - len(untested),
        "untested": untested[:MAX_LISTED],
        "untested_count": len(untested),
        "setup": setup[:MAX_LISTED],
    }
