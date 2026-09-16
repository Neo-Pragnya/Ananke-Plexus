"""Graph build, query, and impact services."""

import ast
import json
from datetime import UTC, datetime
from pathlib import Path

from ananke.plexus.graph.models import GraphEdge, GraphNode, GraphSnapshot, ImpactReport


def _iter_python_files(repository_root: Path) -> list[Path]:
    return sorted(
        path
        for path in repository_root.rglob("*.py")
        if ".venv" not in path.parts and "__pycache__" not in path.parts
    )


def build_graph(repository_root: Path) -> GraphSnapshot:
    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []

    for path in _iter_python_files(repository_root):
        rel_path = str(path.relative_to(repository_root))
        file_id = f"file:{rel_path}"
        nodes.append(GraphNode(node_id=file_id, kind="file", name=path.name, path=rel_path))

        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue

        for item in tree.body:
            if isinstance(item, ast.FunctionDef):
                fn_id = f"function:{rel_path}:{item.name}"
                nodes.append(
                    GraphNode(node_id=fn_id, kind="function", name=item.name, path=rel_path)
                )
                edges.append(GraphEdge(source=file_id, target=fn_id, kind="owns"))
            elif isinstance(item, ast.ClassDef):
                cls_id = f"class:{rel_path}:{item.name}"
                nodes.append(GraphNode(node_id=cls_id, kind="class", name=item.name, path=rel_path))
                edges.append(GraphEdge(source=file_id, target=cls_id, kind="owns"))

            if isinstance(item, ast.Import):
                for alias in item.names:
                    target = f"module:{alias.name}"
                    edges.append(GraphEdge(source=file_id, target=target, kind="imports"))
            elif isinstance(item, ast.ImportFrom) and item.module:
                target = f"module:{item.module}"
                edges.append(GraphEdge(source=file_id, target=target, kind="imports"))

    snapshot_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return GraphSnapshot(snapshot_id=snapshot_id, nodes=nodes, edges=edges)


def persist_graph(repository_root: Path, snapshot: GraphSnapshot) -> Path:
    graph_dir = repository_root / ".ananke" / "graph"
    graph_dir.mkdir(parents=True, exist_ok=True)
    out_path = graph_dir / "metadata.json"
    out_path.write_text(snapshot.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return out_path


def load_graph(repository_root: Path) -> GraphSnapshot | None:
    path = repository_root / ".ananke" / "graph" / "metadata.json"
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    return GraphSnapshot.model_validate(payload)


def query_graph(repository_root: Path, needle: str) -> list[GraphNode]:
    snapshot = load_graph(repository_root)
    if snapshot is None:
        return []
    lowered = needle.lower()
    matches = [
        node
        for node in snapshot.nodes
        if lowered in node.name.lower() or lowered in node.path.lower()
    ]
    return matches


def impact_from_paths(repository_root: Path, changed_files: list[str]) -> ImpactReport:
    snapshot = load_graph(repository_root)
    if snapshot is None:
        summary = "Graph not built yet. Run graph build first."
        return ImpactReport(changed_files=changed_files, impacted_symbols=[], summary=summary)

    impacted_symbols: list[str] = []
    impacted_files: set[str] = set()
    path_set = set(changed_files)
    for edge in snapshot.edges:
        if edge.kind == "owns" and edge.source.startswith("file:"):
            file_path = edge.source.removeprefix("file:")
            if file_path in path_set:
                impacted_symbols.append(edge.target)
        elif edge.kind == "imports" and edge.source.startswith("file:"):
            src_file = edge.source.removeprefix("file:")
            if src_file in path_set:
                for node in snapshot.nodes:
                    if node.node_id == edge.target and node.path:
                        impacted_files.add(node.path)

    blast_radius = len(impacted_symbols) + len(impacted_files)
    summary = (
        f"{len(changed_files)} changed, {len(impacted_symbols)} impacted symbols, "
        f"{len(impacted_files)} impacted files"
    )
    return ImpactReport(
        changed_files=changed_files,
        impacted_symbols=sorted(impacted_symbols),
        impacted_files=sorted(impacted_files),
        blast_radius=blast_radius,
        summary=summary,
    )


def export_graph(repository_root: Path, fmt: str = "json") -> Path:
    snapshot = load_graph(repository_root)
    if snapshot is None:
        snapshot = build_graph(repository_root)
        persist_graph(repository_root, snapshot)

    graph_dir = repository_root / ".ananke" / "graph"
    graph_dir.mkdir(parents=True, exist_ok=True)

    if fmt == "markdown":
        out_path = graph_dir / "export.md"
        lines = [
            f"# Graph Export {snapshot.snapshot_id}",
            "",
            f"- Nodes: {len(snapshot.nodes)}",
            f"- Edges: {len(snapshot.edges)}",
            "",
            "## Sample Nodes",
        ]
        for node in snapshot.nodes[:20]:
            lines.append(f"- `{node.kind}` {node.path}:{node.name}")
        lines.append("")
        lines.append("## Sample Edges")
        for edge in snapshot.edges[:20]:
            lines.append(f"- `{edge.kind}` {edge.source} -> {edge.target}")
        out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return out_path

    out_path = graph_dir / "export.json"
    out_path.write_text(snapshot.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return out_path
