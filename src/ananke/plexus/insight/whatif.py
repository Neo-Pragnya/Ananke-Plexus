"""What-if change simulator (ADV03): what a change to a file or symbol would reach, before it's made.

The simulation walks the code graph backwards from what changes: who imports, calls, inherits from or tests it, then
who depends on those, up to a depth. Each impacted item carries the chain of edges that reaches it and a confidence
(the product of the edges' confidences; inferred and ambiguous edges count for less). Everything it says is
**simulated**: a prediction from the graph, never an observation.

It also names the routes whose handlers are reached, the tables they read or write, and the tests that would exercise
the change.

**Measured, not assumed:** ``backtest`` replays the repository's own history. For each recent commit that changed
several source files, it simulates a change to one of them and checks how many of the others the simulation
predicted, against a naive baseline (files in the same folder). The numbers say how far to trust the simulation in
this repository.
"""

from __future__ import annotations

import posixpath
from collections import deque
from pathlib import Path
from typing import Any

from ananke.plexus.graph.models import GraphSnapshot
from ananke.plexus.insight import InsightError, digest, git
from ananke.plexus.intel._common import is_test_file

SCHEMA = "ananke/whatif@1"
_BACKWARD = {"imports", "calls", "inherits", "implements", "tests", "depends_on"}
_ORIGIN_WEIGHT = {"extracted": 1.0, "declared": 1.0, "inferred": 0.7, "ambiguous": 0.4}
MAX_IMPACTED = 300
_SOURCE_SUFFIXES = (".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".ipynb")


def seeds(graph: GraphSnapshot, changes: list[str]) -> tuple[list[str], list[str]]:
    """Node ids for each change (a file path, a symbol name, ``Class.method`` or a node id), and the ones not found."""
    by_id = {n.node_id: n for n in graph.nodes}
    found: list[str] = []
    missing: list[str] = []
    for change in changes:
        hits = []
        if change in by_id:
            hits = [change]
        else:
            norm = change.strip("./")
            hits = [n.node_id for n in graph.nodes if n.kind == "file" and n.path == norm]
            if hits:  # a file stands for itself and everything defined in it
                hits += [n.node_id for n in graph.nodes if n.path == norm and n.kind != "file"]
            else:
                hits = [
                    n.node_id
                    for n in graph.nodes
                    if n.kind not in {"file", "module", "repository"}
                    and (n.name == change or n.node_id.endswith(f":{change}"))
                ]
        if hits:
            found += [h for h in hits if h not in found]
        else:
            missing.append(change)
    return found, missing


def simulate(
    graph: GraphSnapshot, start: list[str], depth: int = 3, *, both: bool = False
) -> dict[str, dict[str, Any]]:
    """Every node that reaches ``start`` within ``depth`` reverse edges (its dependants), with its chain and
    confidence. With ``both``, what ``start`` depends on is followed too, as a change often needs its callees to
    change with it."""
    into: dict[str, list[Any]] = {}
    for e in graph.edges:
        if e.kind in _BACKWARD:
            into.setdefault(e.target, []).append(e)
            if both:
                resolved = getattr(e, "resolved_target", "") or e.target
                flipped = e.model_copy(update={"source": resolved, "target": e.source})
                into.setdefault(e.source, []).append(flipped)
    nodes = {n.node_id: n for n in graph.nodes}
    # a file's own symbols are reached through the file, and the file through its symbols
    contains: dict[str, str] = {}
    for n in graph.nodes:
        if n.kind != "file" and n.path:
            contains[n.node_id] = f"file:{n.path}"
    best: dict[str, dict[str, Any]] = {}
    queue: deque[tuple[str, int, float, list[str]]] = deque((s, 0, 1.0, []) for s in start)
    while queue:
        nid, level, conf, chain = queue.popleft()
        if level >= depth:
            continue
        sources = list(into.get(nid, []))
        parent = contains.get(nid)
        if parent and parent in into:
            sources += into[parent]
        for e in sources:
            if e.source in start:
                continue
            weight = conf * e.confidence * _ORIGIN_WEIGHT.get(e.origin, 0.5)
            step = f"{e.source} {e.kind} {e.target}"
            seen = best.get(e.source)
            if seen is not None and seen["confidence"] >= weight:
                continue
            node = nodes.get(e.source)
            best[e.source] = {
                "id": e.source,
                "kind": node.kind if node else "",
                "path": node.path if node else "",
                "depth": level + 1,
                "confidence": round(weight, 3),
                "via": [*chain, step],
                "status": "simulated",
            }
            queue.append((e.source, level + 1, weight, [*chain, step]))
    return best


def run(
    root: Path,
    changes: list[str],
    depth: int = 3,
    graph: GraphSnapshot | None = None,
    *,
    both: bool = False,
) -> dict[str, Any]:
    from ananke.plexus.insight.timemachine import graph_of

    if not changes:
        raise InsightError(
            "name at least one change: a file path, a function or class name, or a node id"
        )
    if not 1 <= depth <= 8:
        raise InsightError("--depth takes 1 to 8")
    root = root.resolve()
    graph = graph or graph_of(root)
    start, missing = seeds(graph, changes)
    if not start:
        raise InsightError(
            f"none of {', '.join(changes)} is in the code graph; give a path relative to the project or a symbol "
            "name as `ananke context outline FILE` shows it"
        )
    impacted = simulate(graph, start, depth, both=both)
    rows = sorted(impacted.values(), key=lambda r: (-r["confidence"], r["depth"], r["id"]))
    files = sorted({r["path"] for r in rows if r["path"]} | {_path(graph, s) for s in start} - {""})
    tests = sorted({p for p in files if is_test_file(p)})
    routes, tables = _reach(root, impacted, start, graph)
    report: dict[str, Any] = {
        "schema": SCHEMA,
        "changes": changes,
        "not_found": missing,
        "seeds": start,
        "depth": depth,
        "directions": "dependants and dependencies" if both else "dependants",
        "summary": {
            "impacted": len(rows),
            "files": len(files),
            "tests": len(tests),
            "routes": len(routes),
            "tables": len(tables),
            "high_confidence": sum(1 for r in rows if r["confidence"] >= 0.5),
        },
        "impacted": rows[:MAX_IMPACTED],
        "files": files,
        "tests_to_run": tests,
        "routes": routes,
        "tables": tables,
        "limits": [
            "Simulated from the code graph: a prediction, not an observation. Run the tests it names to observe.",
            "Calls made by name at run time (reflection, dependency injection, string dispatch) aren't edges, so "
            "what they reach isn't predicted.",
            "Confidence multiplies along the chain; inferred edges count 0.7 and ambiguous ones 0.4.",
        ],
    }
    report["digest"] = digest(report)
    return report


def _path(graph: GraphSnapshot, nid: str) -> str:
    return next((n.path for n in graph.nodes if n.node_id == nid), "")


def _reach(
    root: Path, impacted: dict[str, Any], start: list[str], graph: GraphSnapshot
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    from ananke.plexus import intel

    hit = set(impacted) | set(start)
    sections = intel.analyze(root).get("sections") or {}
    routes = [
        {
            "route": r["id"],
            "handler": r.get("handler"),
            "evidence": r.get("evidence"),
            "status": "simulated",
        }
        for r in (sections.get("api") or {}).get("routes") or []
        if r.get("handler") in hit
    ]
    tables: dict[str, set[str]] = {}
    for e in graph.edges:
        if e.kind in {"reads", "writes"} and e.source in hit:
            tables.setdefault(e.target, set()).add(e.kind)
    for use in (sections.get("data") or {}).get("usage") or []:
        if isinstance(use, dict) and use.get("symbol") in hit:
            tables.setdefault(str(use.get("table")), set()).add(str(use.get("access") or "uses"))
    return routes, [
        {"table": t, "access": sorted(a), "status": "simulated"} for t, a in sorted(tables.items())
    ]


def backtest(root: Path, commits: int = 30, depth: int = 3) -> dict[str, Any]:
    """Replay recent commits: simulate a change to one changed source file and measure how many of the commit's other
    changed source files were predicted, against the same-folder baseline. Uses the current graph, so older commits
    are measured against today's structure (stated in the limits)."""
    from ananke.plexus.insight.timemachine import graph_of

    root = root.resolve()
    if not (root / ".git").exists():
        raise InsightError(f"{root} isn't a git repository; the backtest replays its history")
    if not 1 <= commits <= 500:
        raise InsightError("--backtest takes 1 to 500 commits")
    graph = graph_of(root)
    known = {n.path for n in graph.nodes if n.kind == "file"}
    log = git(
        root, "log", f"-{commits * 4}", "--no-merges", "--name-only", "--format=@@%H", timeout=120
    )
    cases: list[dict[str, Any]] = []
    for block in log.split("@@")[1:]:
        lines = [x.strip() for x in block.splitlines() if x.strip()]
        sha, changed = (
            lines[0],
            [p for p in lines[1:] if p.endswith(_SOURCE_SUFFIXES) and p in known],
        )
        if len(changed) < 2 or len(changed) > 40:
            continue
        sources = [p for p in changed if not is_test_file(p)] or changed
        seed = sorted(sources)[0]
        start, _ = seeds(graph, [seed])
        predicted = {r["path"] for r in simulate(graph, start, depth).values() if r["path"]} - {
            seed
        }
        both = {
            r["path"] for r in simulate(graph, start, depth, both=True).values() if r["path"]
        } - {seed}
        others = set(changed) - {seed}
        folder = posixpath.dirname(seed)
        baseline = {p for p in known if posixpath.dirname(p) == folder} - {seed}
        cases.append(
            {
                "commit": sha[:12],
                "seed": seed,
                "co_changed": len(others),
                "predicted": len(predicted),
                "hits": len(predicted & others),
                "both_predicted": len(both),
                "both_hits": len(both & others),
                "baseline_predicted": len(baseline),
                "baseline_hits": len(baseline & others),
            }
        )
        if len(cases) >= commits:
            break
    if not cases:
        raise InsightError(
            "no recent commit changed two or more source files the graph knows; nothing to replay"
        )

    def rate(hits: str, total: str) -> float:
        t = sum(int(c[total]) for c in cases)
        return round(sum(int(c[hits]) for c in cases) / t, 3) if t else 0.0

    result: dict[str, Any] = {
        "schema": "ananke/whatif-backtest@1",
        "commits": len(cases),
        "depth": depth,
        "recall": rate("hits", "co_changed"),
        "precision": rate("hits", "predicted"),
        "both_directions_recall": rate("both_hits", "co_changed"),
        "both_directions_precision": rate("both_hits", "both_predicted"),
        "baseline_recall": rate("baseline_hits", "co_changed"),
        "baseline_precision": rate("baseline_hits", "baseline_predicted"),
        "cases": cases,
        "limits": [
            "Measured with today's graph; files that moved since a commit aren't matched.",
            "Files changed together aren't always dependent, so recall has a ceiling below 1.",
        ],
    }
    result["digest"] = digest(result)
    return result


def to_markdown(report: dict[str, Any]) -> str:
    s = report["summary"]
    lines = [
        f"# What if {', '.join(report['changes'])} changes (simulated)",
        "",
        f"{s['impacted']} impacted ({s['high_confidence']} with confidence ≥ 0.5) in {s['files']} files; "
        f"{s['tests']} tests to run, {s['routes']} routes, {s['tables']} tables.",
        "",
    ]
    if report["not_found"]:
        lines += [f"Not in the graph: {', '.join(report['not_found'])}.", ""]
    if report["tests_to_run"]:
        lines += ["## Tests to run", "", *(f"- `{t}`" for t in report["tests_to_run"]), ""]
    if report["routes"]:
        lines += [
            "## Routes reached",
            "",
            *(f"- `{r['route']}` ({r['evidence']})" for r in report["routes"]),
            "",
        ]
    if report["tables"]:
        lines += [
            "## Tables touched",
            "",
            *(f"- `{t['table']}`: {', '.join(t['access'])}" for t in report["tables"]),
            "",
        ]
    lines += ["## Impacted", "", "| Confidence | Depth | Item | Via |", "| --- | --- | --- | --- |"]
    for r in report["impacted"][:60]:
        lines.append(f"| {r['confidence']} | {r['depth']} | `{r['id']}` | {r['via'][-1]} |")
    lines += [
        "",
        "## Limits",
        "",
        *(f"- {x}" for x in report["limits"]),
        "",
        f"Digest: `{report['digest']}`",
        "",
    ]
    return "\n".join(lines)
