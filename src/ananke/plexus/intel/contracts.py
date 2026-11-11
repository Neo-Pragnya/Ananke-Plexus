"""Where an API's contract has gaps: routes no test calls, routes nothing calls, calls to routes that don't exist,
and operations an OpenAPI file promises that the code doesn't serve (three-repo plan: CAP-api-contract-intelligence
-03 and -04).

It starts from the repository analysis's API section (routes, the frontend and service calls matched to them, the
OpenAPI comparison) and adds the tests: every HTTP call in a test file (``client.get("/orders/1")``,
``requests.post(f"{BASE}/orders")``, ``fetch("/api/x")``, ``request(app).get(...)``) is matched to a route by method
and path shape. Then, for each route with no test, it recommends contract tests from what is known about it: the
success case, a missing resource for each path parameter, and an invalid body for a method that takes one. Nothing is
run and no test is written; the recommendations name the route and evidence they come from.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ananke.plexus.intel._api_common import shape
from ananke.plexus.intel._common import is_test_file

SCHEMA = "api-contract-gaps/v1"
MAX_TEST_FILES = 3_000
_CALL = re.compile(
    r"""\.(get|post|put|patch|delete|head|options)\(\s*f?(['"`])((?:\{[^}'"`]*\}|\$\{[^}]*\})?/[^'"`?#\s]*)""",
    re.I,
)
_FETCH = re.compile(
    r"""\bfetch\(\s*(['"`])((?:\$\{[^}]*\})?/[^'"`?#\s]*)\1?(?P<rest>[^)]{0,200})"""
)
_FETCH_METHOD = re.compile(r"""method\s*:\s*['"](\w+)['"]""", re.I)
_LEAD = re.compile(r"^(\{[^}]*\}|\$\{[^}]*\})")
_BODY = {"POST", "PUT", "PATCH"}


def _segments(path: str) -> tuple[str, ...]:
    path = _LEAD.sub("", path)  # f"{BASE}/orders": the base isn't part of the route
    path = re.sub(r"\$\{[^}]*\}", "{x}", path)
    return shape(path)


def _matches(route: tuple[str, ...], call: tuple[str, ...]) -> bool:
    return len(route) == len(call) and all(
        r == "{}" or r == c or c == "{}" for r, c in zip(route, call, strict=True)
    )


def test_calls(root: Path, files: list[str]) -> list[dict[str, Any]]:
    """HTTP calls in test files: method, path and where."""
    calls: list[dict[str, Any]] = []
    for rel in sorted(f for f in files if is_test_file(f))[:MAX_TEST_FILES]:
        try:
            text = (root / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for no, line in enumerate(text.splitlines(), start=1):
            for m in _CALL.finditer(line):
                calls.append(
                    {"method": m.group(1).upper(), "path": m.group(3), "evidence": f"{rel}:{no}"}
                )
            for m in _FETCH.finditer(line):
                method = _FETCH_METHOD.search(m.group("rest") or "")
                calls.append(
                    {
                        "method": (method.group(1) if method else "GET").upper(),
                        "path": m.group(2),
                        "evidence": f"{rel}:{no}",
                    }
                )
    return calls


def _params(path: str) -> list[str]:
    return re.findall(r"\{([^}/]+)\}", path)


def recommend(route: dict[str, Any]) -> list[dict[str, str]]:
    """Contract tests for a route no test calls, from what's known about it. Suggestions, not results."""
    method, path = route["method"], route["path"]
    name = re.sub(r"[^a-z0-9]+", "_", f"{method} {path}".lower()).strip("_")
    tests = [
        {
            "name": f"test_{name}_succeeds",
            "checks": f"{method} {path} with valid input returns its success status and documented shape",
        }
    ]
    for param in _params(path):
        tests.append(
            {
                "name": f"test_{name}_unknown_{re.sub(r'[^a-z0-9]+', '_', param.lower())}",
                "checks": f"{method} {path} with a {param} that doesn't exist returns 404, not 500",
            }
        )
    if method in _BODY:
        tests.append(
            {
                "name": f"test_{name}_rejects_invalid_body",
                "checks": f"{method} {path} with a missing or wrong field returns 400 or 422",
            }
        )
    if method == "DELETE":
        tests.append(
            {
                "name": f"test_{name}_twice",
                "checks": f"a second {method} {path} doesn't fail with 500",
            }
        )
    return tests


def gaps(root: Path, api: dict[str, Any], files: list[str]) -> dict[str, Any]:
    routes = [r for r in api.get("routes") or [] if r.get("method") and r.get("path")]
    calls = test_calls(root, files)
    callers: dict[str, list[str]] = {}
    for client in api.get("clients") or []:
        if client.get("route"):
            callers.setdefault(str(client["route"]), []).append(
                str(client.get("evidence") or client.get("caller"))
            )
    rows = []
    for r in routes:
        rshape = _segments(r["path"])
        tested = [
            c["evidence"]
            for c in calls
            if (c["method"] == r["method"] or r["method"] == "ANY")
            and _matches(rshape, _segments(c["path"]))
        ]
        rows.append(
            {
                "id": r["id"],
                "method": r["method"],
                "path": r["path"],
                "evidence": r.get("evidence"),
                "called_by": sorted(callers.get(r["id"], [])),
                "tested_by": sorted(set(tested)),
            }
        )
    untested = [row for row in rows if not row["tested_by"]]
    unused = [row for row in rows if not row["called_by"]]
    stray = [
        c
        for c in calls
        if not any(
            (c["method"] == r["method"] or r["method"] == "ANY")
            and _matches(_segments(r["path"]), _segments(c["path"]))
            for r in routes
        )
    ]
    spec_missing = [
        {"spec": s["path"], "operation": op}
        for s in api.get("specs") or []
        for op in s.get("missing_from_code") or []
    ]
    recommendations = [
        {"route": row["id"], "evidence": row["evidence"], "tests": recommend(row)}
        for row in untested
    ]
    return {
        "schema": SCHEMA,
        "routes": rows,
        "summary": {
            "routes": len(rows),
            "tested": len(rows) - len(untested),
            "untested": len(untested),
            "not_called": len(unused),
            "calls_without_route": len(api.get("unmatched_clients") or []),
            "test_calls_without_route": len(stray),
            "spec_operations_missing": len(spec_missing),
        },
        "gaps": {
            "untested_routes": [row["id"] for row in untested],
            "routes_nothing_calls": [row["id"] for row in unused],
            "calls_without_route": [
                {"method": c.get("method"), "path": c.get("path"), "evidence": c.get("evidence")}
                for c in api.get("unmatched_clients") or []
            ],
            "test_calls_without_route": stray,
            "spec_operations_missing": spec_missing,
        },
        "recommended_tests": recommendations,
        "limits": [
            *(api.get("limits") or []),
            "Test calls are read from literal paths in test files; a path built at run time isn't matched.",
            "A route nothing in the project calls may still be used by another service or an external client.",
            "Recommended tests are suggestions from the route's method and path; nothing was run or written.",
        ],
    }


def to_markdown(report: dict[str, Any]) -> str:
    s = report["summary"]
    lines = [
        "# API contract gaps",
        "",
        f"{s['routes']} routes: {s['tested']} tested, {s['untested']} untested, {s['not_called']} that nothing in the "
        f"project calls. {s['calls_without_route']} call(s) and {s['test_calls_without_route']} test call(s) with no "
        f"route; {s['spec_operations_missing']} OpenAPI operation(s) the code doesn't serve.",
        "",
        "| Route | Called by | Tested by |",
        "|---|---|---|",
    ]
    for r in report["routes"]:
        lines.append(
            f"| `{r['method']} {r['path']}` | {len(r['called_by'])} | {', '.join(r['tested_by'][:2]) or '**none**'} |"
        )
    if report["recommended_tests"]:
        lines += ["", "## Recommended contract tests", ""]
        for rec in report["recommended_tests"]:
            lines.append(f"- `{rec['route']}` ({rec['evidence']})")
            lines += [f"  - `{t['name']}`: {t['checks']}" for t in rec["tests"]]
    g = report["gaps"]
    if g["calls_without_route"] or g["test_calls_without_route"] or g["spec_operations_missing"]:
        lines += ["", "## Mismatches", ""]
        lines += [
            f"- call with no route: `{c['method']} {c['path']}` ({c['evidence']})"
            for c in g["calls_without_route"]
        ]
        lines += [
            f"- test call with no route: `{c['method']} {c['path']}` ({c['evidence']})"
            for c in g["test_calls_without_route"]
        ]
        lines += [
            f"- {m['spec']} promises `{m['operation']}`, which the code doesn't serve"
            for m in g["spec_operations_missing"]
        ]
    lines += ["", "## Limits", ""] + [f"- {x}" for x in report["limits"]]
    return "\n".join(lines).replace("<", "&lt;") + "\n"


__all__ = ["SCHEMA", "gaps", "recommend", "test_calls", "to_markdown"]
