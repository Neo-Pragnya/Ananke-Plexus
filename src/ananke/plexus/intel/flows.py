"""End-to-end flows: how a request travels, from the code that calls it to the tables it touches.

A flow only joins up what's already known. Every edge comes from the code graph, the API section or the data model's usage, so
a step is never invented, and a route nothing calls or that calls nothing is just its route and handler. See
docs/reference/repo-intelligence.md for the shape this answers in.
"""

from __future__ import annotations

import ast
import json
import tomllib
import warnings
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from ananke.plexus.intel._common import is_fixture, is_test_file, read_text
from ananke.plexus.intel.context import RepoContext

MAX_DEPTH = 8  # calls followed from a handler
MAX_NODES = 60  # symbols in one flow
MAX_EDGES_READ = 2000  # edges looked at for one flow, so a function with thousands of calls can't cost every route that time
MAX_SCRIPTS = 200  # commands read from one manifest
MAX_CLIENTS = 20  # clients shown on one flow
MAX_CALLERS = 8  # callers shown for one client
MAX_FLOWS = 2000
MAX_ENTRYPOINTS = 200
MAX_ENTRY_FILES = 5000
_PARSE_FAILURES = (SyntaxError, ValueError, RecursionError, MemoryError)
_SYMBOLS = {"function", "method", "class"}


@dataclass
class _Knowledge:
    labels: dict[str, str] = field(default_factory=dict)
    kinds: dict[str, str] = field(default_factory=dict)
    where: dict[str, str] = field(default_factory=dict)
    calls: dict[str, list[tuple[str, str, str]]] = field(
        default_factory=dict
    )  # target, origin, location
    callers: dict[str, list[tuple[str, str, str]]] = field(default_factory=dict)
    usage: dict[str, list[dict[str, Any]]] = field(default_factory=dict)


def _line_of(location: str) -> int:
    tail = location.rpartition(":")[2]
    return int(tail) if tail.isdigit() else 0


def _knowledge(ctx: RepoContext) -> _Knowledge:
    known = _Knowledge()
    for node in ctx.graph.nodes:
        if node.kind in _SYMBOLS or node.kind == "file":
            known.labels[node.node_id] = node.name
            known.kinds[node.node_id] = node.kind
            line = node.metadata.get("line")
            known.where[node.node_id] = (
                f"{node.path}:{line}" if isinstance(line, int) else node.path
            )
    for edge in ctx.graph.edges:
        if edge.kind == "calls":
            known.calls.setdefault(edge.source, []).append(
                (edge.target, edge.origin, edge.source_location)
            )
            known.callers.setdefault(edge.target, []).append(
                (edge.source, edge.origin, edge.source_location)
            )
    for rows in (*known.calls.values(), *known.callers.values()):
        rows.sort()
    data = ctx.sections.get("data") or {}
    for row in data.get("usage") or []:
        known.usage.setdefault(row["symbol"], []).append(row)
    return known


class _Builder:
    """One flow's steps and edges, each listed once."""

    def __init__(self, known: _Knowledge) -> None:
        self.known = known
        self.steps: dict[str, dict[str, Any]] = {}
        self.edges: dict[tuple[str, str, str], dict[str, Any]] = {}
        self.symbols = 0

    def step(
        self, node: str, kind: str, label: str, evidence: str, access: str | None = None
    ) -> None:
        if node not in self.steps:
            self.steps[node] = {
                "node": node,
                "kind": kind,
                "label": label,
                "evidence": evidence,
                "access": access,
            }

    def symbol(self, node: str, kind: str | None = None) -> None:
        known = self.known
        self.step(
            node,
            kind or _step_kind(known.kinds.get(node, "function")),
            known.labels.get(node, node),
            known.where.get(node, ""),
        )

    def edge(self, source: str, target: str, kind: str, origin: str) -> None:
        self.edges.setdefault(
            (source, target, kind),
            {"source": source, "target": target, "kind": kind, "origin": origin},
        )

    def table(self, source: str, row: dict[str, Any]) -> None:
        node = f"table:{row['table']}"
        access = row["access"]
        have = self.steps.get(node)
        if have is None:
            self.step(node, "table", row["table"], row["evidence"], access)
        elif have["access"] != access:
            have["access"] = "read_write"
        self.edge(source, node, "reads" if access == "read" else "writes", row["origin"])

    def follow(self, start: str, lines: tuple[int, int] | None = None) -> bool:
        """Calls breadth-first from a symbol to the budget, then the tables each symbol it reached uses. True if cut short.

        ``lines`` narrows the first symbol to a handler written inside it, because the graph holds that handler's calls and
        usage under the symbol around it, each at the first place it happens.
        """
        known = self.known
        truncated = False
        queue: deque[tuple[str, int, tuple[int, int] | None]] = deque([(start, 0, lines)])
        seen = {start}
        reached: list[tuple[str, tuple[int, int] | None]] = [(start, lines)]
        read = 0
        while queue:
            node, depth, span = queue.popleft()
            for target, origin, location in known.calls.get(node, ()):
                read += 1
                if read > MAX_EDGES_READ or self.symbols > MAX_NODES:
                    truncated = True
                    queue.clear()
                    break
                if span is not None and not span[0] <= _line_of(location) <= span[1]:
                    continue
                if target in seen:
                    if target in self.steps:
                        self.edge(node, target, "calls", origin)
                    continue
                if depth >= MAX_DEPTH or self.symbols == MAX_NODES:
                    truncated = True
                    self.symbols += depth < MAX_DEPTH  # one past the budget stops the walk
                    continue
                seen.add(target)
                self.symbols += 1
                self.symbol(target)
                self.edge(node, target, "calls", origin)
                reached.append((target, None))
                queue.append((target, depth + 1, None))
        for node, span in reached:
            for row in known.usage.get(node, ())[:MAX_EDGES_READ]:
                if span is None or span[0] <= _line_of(row["evidence"]) <= span[1]:
                    self.table(node, row)
        return truncated

    def result(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        return list(self.steps.values()), sorted(
            self.edges.values(), key=lambda e: (e["source"], e["target"], e["kind"])
        )


def _step_kind(kind: str) -> str:
    return kind if kind in {"class", "file"} else "function"


def _route_flow(
    route: dict[str, Any], clients: list[dict[str, Any]], known: _Knowledge
) -> dict[str, Any]:
    builder = _Builder(known)
    truncated = len(clients) > MAX_CLIENTS
    identity = route["id"]
    name = identity.removeprefix("route:")
    for client in clients[:MAX_CLIENTS]:
        caller = client["caller"]
        for source, origin, _ in known.callers.get(caller, ())[:MAX_CALLERS]:
            builder.symbol(source)
            builder.edge(source, caller, "calls", origin)
        if len(known.callers.get(caller, ())) > MAX_CALLERS:
            truncated = True
        builder.symbol(caller)
        builder.step(
            client["id"],
            "client",
            f"{client['method']} {client['path']}",
            client["evidence"],
        )
        builder.edge(caller, client["id"], "calls", "extracted")
        builder.edge(client["id"], identity, "http", "inferred")
    builder.step(identity, "route", name, route["evidence"])
    handler = route.get("handler")
    if handler:
        lines = route.get("handler_lines")
        span = (lines[0], lines[1]) if lines else None
        builder.step(
            handler,
            "handler",
            route.get("handler_name") or known.labels.get(handler, handler),
            known.where.get(handler, route["evidence"]),
        )
        builder.edge(identity, handler, "handles", "extracted")
        truncated = builder.follow(handler, span) or truncated
    steps, edges = builder.result()
    return {
        "id": f"flow:{name}",
        "name": name,
        "entry": identity,
        "truncated": truncated,
        "steps": steps,
        "edges": edges,
    }


def _main_blocks(ctx: RepoContext, limits: list[str]) -> list[dict[str, Any]]:
    found = []
    paths = [p for p in ctx.files if p.endswith(".py") and not (is_test_file(p) or is_fixture(p))]
    if len(paths) > MAX_ENTRY_FILES:
        limits.append(
            f"only the first {MAX_ENTRY_FILES} Python files were read for __main__ blocks"
        )
        paths = paths[:MAX_ENTRY_FILES]
    for path in paths:
        text = read_text(ctx, path)
        if text is None or "__main__" not in text:
            continue
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                tree = ast.parse(text)
        except _PARSE_FAILURES:
            continue
        for item in tree.body:
            if isinstance(item, ast.If) and _is_main_test(item.test):
                found.append(
                    {
                        "id": f"entrypoint:main:{path}",
                        "kind": "main",
                        "name": path,
                        "node": f"file:{path}",
                        "evidence": f"{path}:{item.lineno}",
                    }
                )
                break
    return found


def _is_main_test(test: ast.expr) -> bool:
    if not isinstance(test, ast.Compare) or len(test.comparators) != 1:
        return False
    left, right = test.left, test.comparators[0]
    return (
        isinstance(left, ast.Name)
        and left.id == "__name__"
        and isinstance(right, ast.Constant)
        and right.value == "__main__"
    )


def _declared_scripts(
    ctx: RepoContext, known: _Knowledge, limits: list[str]
) -> list[dict[str, Any]]:
    """Commands a ``pyproject.toml`` declares, and ``bin`` in a ``package.json``."""
    found: list[dict[str, Any]] = []
    for path in ctx.named("pyproject.toml"):
        text = read_text(ctx, path)
        if text is None or is_fixture(path):
            continue
        try:
            scripts = tomllib.loads(text).get("project", {}).get("scripts", {})
        except (tomllib.TOMLDecodeError, RecursionError, AttributeError):
            limits.append(f"{path} doesn't parse, so its commands aren't listed")
            continue
        if not isinstance(scripts, dict):
            continue
        for name, spec in sorted(scripts.items())[:MAX_SCRIPTS]:
            if not isinstance(spec, str):
                continue
            module, _, attr = spec.partition(":")
            tail = "/" + module.replace(".", "/") + ".py"
            matches = [
                n
                for n in (f"function:{p}:{attr.partition('.')[0]}" for p in _ending(known, tail))
                if n in known.kinds
            ]
            node = matches[0] if len(matches) == 1 else f"file:{path}"
            found.append(
                {
                    "id": f"entrypoint:cli:{name}",
                    "kind": "cli",
                    "name": name,
                    "node": node,
                    "evidence": f"{path}:{_line_with(text, name)}",
                }
            )
    for path in ctx.named("package.json"):
        text = read_text(ctx, path)
        if text is None or is_fixture(path):
            continue
        try:
            manifest = json.loads(text)
        except (ValueError, RecursionError):
            continue
        declared = manifest.get("bin") if isinstance(manifest, dict) else None
        if isinstance(declared, str):
            declared = {str(manifest.get("name") or path): declared}
        if not isinstance(declared, dict):
            continue
        for name, target in sorted(declared.items())[:MAX_SCRIPTS]:
            found.append(
                {
                    "id": f"entrypoint:cli:{name}",
                    "kind": "cli",
                    "name": name,
                    "node": f"file:{path.rpartition('/')[0]}/{str(target).removeprefix('./')}".replace(
                        "file:/", "file:"
                    ),
                    "evidence": f"{path}:{_line_with(text, name)}",
                }
            )
    return found


def _ending(known: _Knowledge, tail: str) -> list[str]:
    return [
        node.removeprefix("file:")
        for node, kind in known.kinds.items()
        if kind == "file" and f"/{node.removeprefix('file:')}".endswith(tail)
    ]


def _line_with(text: str, word: str) -> int:
    where = text.find(word)
    return 1 if where < 0 else text.count("\n", 0, where) + 1


def _decorated_commands(ctx: RepoContext) -> list[dict[str, Any]]:
    """Functions under ``@x.command(...)`` in a file that imports Click or Typer."""
    importers = {
        edge.source.removeprefix("file:")
        for edge in ctx.graph.edges
        if edge.kind == "imports" and edge.target in {"module:click", "module:typer"}
    }
    found = []
    for node in ctx.graph.nodes:
        decorators = node.metadata.get("decorators")
        if (
            node.kind != "function"
            or node.path not in importers
            or not isinstance(decorators, list)
        ):
            continue
        if is_test_file(node.path) or is_fixture(node.path):
            continue
        if any(".command" in str(d)[:128] for d in decorators):
            found.append(
                {
                    "id": f"entrypoint:cli:{node.path}:{node.name}",
                    "kind": "cli",
                    "name": node.name,
                    "node": node.node_id,
                    "evidence": f"{node.path}:{node.metadata.get('line')}",
                }
            )
    return found


def _entrypoints(ctx: RepoContext, known: _Knowledge, limits: list[str]) -> list[dict[str, Any]]:
    rows = [
        *_main_blocks(ctx, limits),
        *_declared_scripts(ctx, known, limits),
        *_decorated_commands(ctx),
    ]
    rows.sort(key=lambda r: (r["kind"], r["id"]))
    if len(rows) > MAX_ENTRYPOINTS:
        limits.append(f"{len(rows) - MAX_ENTRYPOINTS} more entry points weren't listed")
        rows = rows[:MAX_ENTRYPOINTS]
    for row in rows:
        builder = _Builder(known)
        if row["node"] in known.kinds:
            builder.symbol(row["node"])
        else:
            builder.step(row["node"], "file", row["name"], row["evidence"])
        row["truncated"] = builder.follow(row["node"])
        row["steps"], row["edges"] = builder.result()
    if any(r["kind"] == "main" for r in rows):
        limits.append(
            "a __main__ block is listed as a start, but the graph doesn't hold calls made at the top of a module, so it shows no steps"
        )
    return rows


def analyze(ctx: RepoContext) -> dict[str, Any]:
    known = _knowledge(ctx)
    limits: list[str] = []
    api = ctx.sections.get("api") or {}
    routes = api.get("routes") or []
    if api.get("status") in {None, "failed"}:
        limits.append("the API section didn't answer, so there are no routes to follow")
    clients_of: dict[str, list[dict[str, Any]]] = {}
    for client in api.get("clients") or []:
        if client.get("route"):
            clients_of.setdefault(client["route"], []).append(client)
    if len(routes) > MAX_FLOWS:
        limits.append(f"only the first {MAX_FLOWS} of {len(routes)} routes have flows")
    flows = [_route_flow(r, clients_of.get(r["id"], []), known) for r in routes[:MAX_FLOWS]]
    flows.sort(key=lambda f: f["id"])
    cut = sum(1 for f in flows if f["truncated"])
    if cut:
        limits.append(
            f"{cut} flows reached the budget of {MAX_DEPTH} calls deep and {MAX_NODES} symbols, so they're marked truncated"
        )
    if any(r.get("handler_lines") for r in routes):
        limits.append(
            "a handler written inside another function shares its symbol, so its flow shows the calls and tables first seen within its lines, which can miss one its neighbour used first"
        )
    entrypoints = _entrypoints(ctx, known, limits)
    limits.append("background jobs, schedules and message consumers aren't traced as starts")
    found = bool(flows or entrypoints)
    if not found:
        limits.insert(0, "no routes or other entry points were found")
    return {
        "status": "found" if found else "none",
        "limits": limits,
        "flows": flows,
        "entrypoints": entrypoints,
    }
