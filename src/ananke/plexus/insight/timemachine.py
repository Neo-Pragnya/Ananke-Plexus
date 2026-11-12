"""Architecture time machine (ADV02): the code graph at two revisions, and exactly which relationships changed.

Both sides are read the same way: a revision from ``git archive`` into a throwaway folder, the working tree as it is.
Nodes are compared by id (kind, file and name), edges by source, target and kind. A node that disappears from one file
while a node of the same kind and name appears in another is reported as a **likely move**, a hypothesis, never as an
observed fact. Components are compared by top-level folder, so a new dependency between two parts of the system
stands out.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from ananke.plexus.graph.models import GraphSnapshot
from ananke.plexus.insight import InsightError, digest, resolve_ref, snapshot

SCHEMA = "ananke/graph-diff@1"
MAX_LISTED = 200
_STRUCTURAL = {
    "imports",
    "calls",
    "inherits",
    "implements",
    "reads",
    "writes",
    "publishes",
    "subscribes",
    "tests",
}


def graph_of(root: Path) -> GraphSnapshot:
    from ananke.plexus.context import fresh_graph

    return fresh_graph(root.resolve(), save=False)


def _prefix(paths: list[str]) -> list[str]:
    """The deepest folder prefix that holds at least 60% of the non-test source files and doesn't split yet:
    ``src/pkg`` in a src layout, even with a few scripts elsewhere."""
    from ananke.plexus.intel._common import is_test_file

    folders = [
        p.split("/")[:-1]
        for p in paths
        if not is_test_file(p) and p.endswith((".py", ".ts", ".js", ".tsx"))
    ]
    prefix: list[str] = []
    while folders:
        under = [f for f in folders if f[: len(prefix)] == prefix and len(f) > len(prefix)]
        counts = Counter(f[len(prefix)] for f in under)
        if not counts:
            break
        name, n = counts.most_common(1)[0]
        if n < 0.6 * len(folders):
            break  # the code splits here: these folders are the components
        prefix = [*prefix, name]
    return prefix


def _component(path: str, prefix: list[str]) -> str:
    parts = [p for p in path.split("/") if p]
    if parts[: len(prefix)] == prefix and prefix:
        rest = parts[len(prefix) :]
        return "/".join(prefix[-1:] + rest[:1]) if len(rest) > 1 else "/".join(prefix[-1:])
    return parts[0] if len(parts) > 1 else "(root)"


def compare(before: GraphSnapshot, after: GraphSnapshot) -> dict[str, Any]:
    old_nodes = {n.node_id: n for n in before.nodes}
    new_nodes = {n.node_id: n for n in after.nodes}
    old_edges = {(e.source, e.target, e.kind): e for e in before.edges if e.kind in _STRUCTURAL}
    new_edges = {(e.source, e.target, e.kind): e for e in after.edges if e.kind in _STRUCTURAL}
    added_nodes = sorted(set(new_nodes) - set(old_nodes))
    removed_nodes = sorted(set(old_nodes) - set(new_nodes))
    moves = []
    gone = {
        (old_nodes[i].kind, old_nodes[i].name): i
        for i in removed_nodes
        if old_nodes[i].kind != "file"
    }
    for nid in added_nodes:
        node = new_nodes[nid]
        key = (node.kind, node.name)
        if node.kind != "file" and key in gone:
            moves.append(
                {
                    "kind": node.kind,
                    "name": node.name,
                    "from": old_nodes[gone[key]].path,
                    "to": node.path,
                    "status": "hypothesis: same kind and name, different file",
                }
            )
    added_edges = sorted(set(new_edges) - set(old_edges))
    removed_edges = sorted(set(old_edges) - set(new_edges))

    def path_of(nid: str, nodes: dict[str, Any]) -> str:
        node = nodes.get(nid)
        if node is not None and node.path:
            return str(node.path)
        return ""  # an external module has no file; internal imports arrive resolved to their file

    def comp_edges(
        edges: set[tuple[str, str, str]], nodes: dict[str, Any], edges_by_key: dict[Any, Any]
    ) -> Counter[tuple[str, str, str]]:
        out: Counter[tuple[str, str, str]] = Counter()
        for s, t, k in edges:
            resolved = getattr(edges_by_key.get((s, t, k)), "resolved_target", "") or ""
            if resolved.startswith("file:"):
                t = resolved
            a, b = _component(path_of(s, nodes), prefix), _component(path_of(t, nodes), prefix)
            if a != b and path_of(s, nodes) and path_of(t, nodes):
                out[(a, b, k)] += 1
        return out

    prefix = _prefix([n.path for n in new_nodes.values() if n.kind == "file"])
    before_links = comp_edges(set(old_edges), old_nodes, old_edges)
    after_links = comp_edges(set(new_edges), new_nodes, new_edges)
    new_links = sorted(set(after_links) - set(before_links))
    dropped_links = sorted(set(before_links) - set(after_links))

    def edge_row(
        key: tuple[str, str, str], edges: dict[Any, Any], nodes: dict[str, Any]
    ) -> dict[str, Any]:
        e = edges[key]
        return {
            "source": key[0],
            "target": key[1],
            "kind": key[2],
            "origin": e.origin,
            "file": path_of(key[0], nodes),
        }

    kinds_added = Counter(new_nodes[i].kind for i in added_nodes)
    kinds_removed = Counter(old_nodes[i].kind for i in removed_nodes)
    return {
        "summary": {
            "nodes_before": len(old_nodes),
            "nodes_after": len(new_nodes),
            "nodes_added": len(added_nodes),
            "nodes_removed": len(removed_nodes),
            "edges_added": len(added_edges),
            "edges_removed": len(removed_edges),
            "likely_moves": len(moves),
            "component_links_added": len(new_links),
            "component_links_removed": len(dropped_links),
        },
        "nodes_added_by_kind": dict(sorted(kinds_added.items())),
        "nodes_removed_by_kind": dict(sorted(kinds_removed.items())),
        "nodes_added": [_node_row(new_nodes[i]) for i in added_nodes[:MAX_LISTED]],
        "nodes_removed": [_node_row(old_nodes[i]) for i in removed_nodes[:MAX_LISTED]],
        "edges_added": [edge_row(k, new_edges, new_nodes) for k in added_edges[:MAX_LISTED]],
        "edges_removed": [edge_row(k, old_edges, old_nodes) for k in removed_edges[:MAX_LISTED]],
        "likely_moves": moves[:MAX_LISTED],
        "component_links_added": [
            {"from": a, "to": b, "kind": k, "edges": after_links[(a, b, k)]}
            for a, b, k in new_links
        ],
        "component_links_removed": [
            {"from": a, "to": b, "kind": k, "edges": before_links[(a, b, k)]}
            for a, b, k in dropped_links
        ],
    }


def _node_row(node: Any) -> dict[str, Any]:
    return {"id": node.node_id, "kind": node.kind, "name": node.name, "path": node.path}


def diff(root: Path, base: str, head: str | None = None) -> dict[str, Any]:
    """The graph at ``base`` against ``head`` (a revision) or, by default, the working tree."""
    root = root.resolve()
    if not (root / ".git").exists():
        raise InsightError(
            f"{root} isn't a git repository; the time machine reads past revisions from git"
        )
    base_commit = resolve_ref(root, base)
    with snapshot(root, base) as old_root:
        before = graph_of(old_root)
    if head is None:
        after = graph_of(root)
        head_label = "working tree"
        head_commit = ""
    else:
        head_commit = resolve_ref(root, head)
        head_label = head
        with snapshot(root, head) as new_root:
            after = graph_of(new_root)
    result = compare(before, after)
    report: dict[str, Any] = {
        "schema": SCHEMA,
        "base": {"ref": base, "commit": base_commit},
        "head": {"ref": head_label, "commit": head_commit},
        **result,
        "limits": [
            "Edges are what the code graph extracts (imports, calls, inheritance, data reads and writes, tests); "
            "a relationship made at run time (dependency injection, reflection) isn't seen.",
            f"Lists stop at {MAX_LISTED} entries; the summary counts everything.",
            "A likely move is a hypothesis from a matching kind and name; check it before relying on it.",
        ],
    }
    report["digest"] = digest(report)
    return report


def to_markdown(report: dict[str, Any]) -> str:
    s = report["summary"]
    lines = [
        f"# Architecture changes: {report['base']['ref']} → {report['head']['ref']}",
        "",
        f"Nodes {s['nodes_before']} → {s['nodes_after']} (+{s['nodes_added']} -{s['nodes_removed']}); "
        f"relationships +{s['edges_added']} -{s['edges_removed']}; {s['likely_moves']} likely moves.",
        "",
    ]
    if report["component_links_added"] or report["component_links_removed"]:
        lines += [
            "## Between components",
            "",
            "| Change | From | To | Kind | Edges |",
            "| --- | --- | --- | --- | --- |",
        ]
        lines += [
            f"| new | {c['from']} | {c['to']} | {c['kind']} | {c['edges']} |"
            for c in report["component_links_added"]
        ]
        lines += [
            f"| gone | {c['from']} | {c['to']} | {c['kind']} | {c['edges']} |"
            for c in report["component_links_removed"]
        ]
        lines.append("")
    if report["edges_added"]:
        lines += ["## Relationships added", ""]
        lines += [
            f"- `{e['source']}` {e['kind']} `{e['target']}`" for e in report["edges_added"][:50]
        ]
        lines.append("")
    if report["edges_removed"]:
        lines += ["## Relationships removed", ""]
        lines += [
            f"- `{e['source']}` {e['kind']} `{e['target']}`" for e in report["edges_removed"][:50]
        ]
        lines.append("")
    if report["likely_moves"]:
        lines += ["## Likely moves (hypotheses)", ""]
        lines += [
            f"- {m['kind']} `{m['name']}`: {m['from']} → {m['to']}" for m in report["likely_moves"]
        ]
        lines.append("")
    lines += [
        "## Limits",
        "",
        *(f"- {x}" for x in report["limits"]),
        "",
        f"Digest: `{report['digest']}`",
        "",
    ]
    return "\n".join(lines)
