"""Pre-rendered SVG diagrams — no external JS (spec §77, §114).

Layout is a simple deterministic layered DAG: a node's layer is its longest distance from
a root. Text is XML-escaped. Only SVG presentation attributes are used (no ``style=``)
so the pages can keep a strict Content-Security-Policy.
"""

from __future__ import annotations

from xml.sax.saxutils import escape, quoteattr

NODE_W, NODE_H = 210, 38
GAP_X, GAP_Y = 36, 62
_KIND_FILL = {
    "skill": "#e0f2fe",
    "agent": "#ede9fe",
    "tool": "#dcfce7",
    "policy": "#fee2e2",
    "evaluator": "#fef9c3",
}
_MISSING_FILL = "#f3f4f6"


def _layers(roots: list[str], edges: dict[str, list[str]]) -> dict[str, int]:
    depth: dict[str, int] = {}

    def visit(node: str, level: int, trail: tuple[str, ...]) -> None:
        if node in trail:
            return  # cycle guard
        if depth.get(node, -1) >= level:
            return
        depth[node] = level
        for child in edges.get(node, []):
            visit(child, level + 1, (*trail, node))

    for r in roots:
        visit(r, 0, ())
    return depth


def render_graph_svg(
    roots: list[str],
    edges: dict[str, list[str]],
    labels: dict[str, str],
    kinds: dict[str, str],
    *,
    title: str = "Dependency graph",
    missing: set[str] | None = None,
) -> str:
    """Return an ``<svg>`` string for the graph reachable from ``roots``."""
    missing = missing or set()
    depth = _layers(roots, edges)
    if not depth:
        return ""
    by_layer: dict[int, list[str]] = {}
    for node, level in sorted(depth.items(), key=lambda kv: (kv[1], kv[0])):
        by_layer.setdefault(level, []).append(node)
    widest = max(len(v) for v in by_layer.values())
    width = widest * NODE_W + (widest + 1) * GAP_X
    height = len(by_layer) * NODE_H + (len(by_layer) + 1) * GAP_Y - GAP_Y // 2
    pos: dict[str, tuple[float, float]] = {}
    for level, nodes in by_layer.items():
        row_w = len(nodes) * NODE_W + (len(nodes) - 1) * GAP_X
        x0 = (width - row_w) / 2
        y = GAP_Y // 2 + level * (NODE_H + GAP_Y)
        for i, node in enumerate(nodes):
            pos[node] = (x0 + i * (NODE_W + GAP_X), y)
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" class="diagram" role="img" '
        f'aria-label={quoteattr(title)} viewBox="0 0 {width} {height}" width="{width}" height="{height}">',
        f"<title>{escape(title)}</title>",
        '<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" '
        'markerHeight="7" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" '
        'fill="#6b7280"/></marker></defs>',
    ]
    for src, targets in sorted(edges.items()):
        if src not in pos:
            continue
        for dst in sorted(targets):
            if dst not in pos:
                continue
            sx, sy = pos[src][0] + NODE_W / 2, pos[src][1] + NODE_H
            dx, dy = pos[dst][0] + NODE_W / 2, pos[dst][1]
            mid = (sy + dy) / 2
            parts.append(
                f'<path d="M {sx:.1f} {sy:.1f} C {sx:.1f} {mid:.1f}, {dx:.1f} {mid:.1f}, {dx:.1f} {dy - 1:.1f}" '
                'fill="none" stroke="#6b7280" stroke-width="1.5" marker-end="url(#arrow)"/>'
            )
    for node, (x, ny) in sorted(pos.items(), key=lambda kv: kv[0]):
        fill = _MISSING_FILL if node in missing else _KIND_FILL.get(kinds.get(node, ""), "#f3f4f6")
        dash = ' stroke-dasharray="4 3"' if node in missing else ""
        label = labels.get(node, node)
        shown = label if len(label) <= 30 else label[:29] + "…"
        parts.append(
            f'<g><rect x="{x:.1f}" y="{ny:.1f}" width="{NODE_W}" height="{NODE_H}" rx="8" fill="{fill}" '
            f'stroke="#374151" stroke-width="1.2"{dash}/>'
            f'<text x="{x + NODE_W / 2:.1f}" y="{ny + NODE_H / 2 + 4:.1f}" text-anchor="middle" '
            f'font-family="system-ui, sans-serif" font-size="12" fill="#111827">{escape(shown)}</text>'
            f"<title>{escape(label)}</title></g>"
        )
    parts.append("</svg>")
    return "".join(parts)


def render_lineage_svg(versions: list[tuple[str, str]], title: str = "Version lineage") -> str:
    """Horizontal chain of ``(version, state)`` nodes; ``state`` colours the box."""
    if not versions:
        return ""
    fills = {
        "active": "#dcfce7",
        "deprecated": "#fef9c3",
        "yanked": "#fee2e2",
        "quarantined": "#fecaca",
        "archived": "#e5e7eb",
    }
    w, gap = 96, 26
    width = len(versions) * w + (len(versions) + 1) * gap
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" class="diagram" role="img" aria-label={quoteattr(title)} '
        f'viewBox="0 0 {width} 70" width="{width}" height="70"><title>{escape(title)}</title>'
        '<defs><marker id="larrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" '
        'orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="#6b7280"/></marker></defs>'
    ]
    for i, (version, state) in enumerate(versions):
        x = gap + i * (w + gap)
        if i:
            parts.append(
                f'<line x1="{x - gap + 2}" y1="35" x2="{x - 2}" y2="35" stroke="#6b7280" '
                'stroke-width="1.5" marker-end="url(#larrow)"/>'
            )
        parts.append(
            f'<g><rect x="{x}" y="15" width="{w}" height="40" rx="8" fill="{fills.get(state, "#f3f4f6")}" '
            f'stroke="#374151" stroke-width="1.2"/><text x="{x + w / 2}" y="39" text-anchor="middle" '
            f'font-family="system-ui, sans-serif" font-size="12" fill="#111827">{escape(version[:14])}</text>'
            f"<title>{escape(version)} ({escape(state)})</title></g>"
        )
    parts.append("</svg>")
    return "".join(parts)
