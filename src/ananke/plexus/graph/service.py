"""Graph build, query, and impact services."""

import ast
import json
from datetime import UTC, datetime
from pathlib import Path

from ananke.plexus.graph.models import GraphEdge, GraphNode, GraphSnapshot, ImpactReport

_SKIP_DIRS = {"node_modules", "site-packages", "__pycache__"}


def _iter_python_files(repository_root: Path) -> list[Path]:
    """Python sources of the project itself: not hidden directories (.git, .venv, caches), dependencies or
    virtual environments (any directory holding a ``pyvenv.cfg``)."""
    venvs = {cfg.parent for cfg in repository_root.rglob("pyvenv.cfg")}
    found = []
    for path in repository_root.rglob("*.py"):
        rel_parts = path.relative_to(repository_root).parts[:-1]
        if any(part.startswith(".") or part in _SKIP_DIRS for part in rel_parts):
            continue
        if any(parent in venvs for parent in path.parents):
            continue
        found.append(path)
    return sorted(found)


def _module_name(rel_path: str) -> str:
    """``src/shop/cart.py`` → ``src.shop.cart``; ``pkg/__init__.py`` → ``pkg``."""
    parts = rel_path.removesuffix(".py").split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _doc(node: ast.AST) -> str:
    try:
        text = ast.get_docstring(node) or ""  # type: ignore[arg-type]
    except TypeError:
        return ""
    return text.strip().splitlines()[0][:200] if text.strip() else ""


def _signature(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    try:
        args = ast.unparse(fn.args)
    except Exception:  # pragma: no cover - ast.unparse handles every parsed tree
        args = "..."
    returns = f" -> {ast.unparse(fn.returns)}" if fn.returns is not None else ""
    return (
        f"{'async ' if isinstance(fn, ast.AsyncFunctionDef) else ''}def {fn.name}({args}){returns}"
    )


def _symbol_meta(node: ast.AST) -> dict[str, object]:
    meta: dict[str, object] = {
        "line": getattr(node, "lineno", 0),
        "end_line": getattr(node, "end_lineno", getattr(node, "lineno", 0)),
    }
    doc = _doc(node)
    if doc:
        meta["doc"] = doc
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        meta["signature"] = _signature(node)
        decorators = [ast.unparse(d) for d in node.decorator_list]
        if decorators:
            meta["decorators"] = decorators
    return meta


def build_graph(repository_root: Path) -> GraphSnapshot:
    """Python files, their top-level functions and classes, methods, imports, and inferred calls and bases.

    Node ids are stable: ``file:<path>``, ``function:<path>:<name>``, ``class:<path>:<name>`` and
    ``method:<path>:<Class>.<name>``. Symbol metadata carries ``line``, ``end_line``, a one-line ``doc`` and, for
    functions, a ``signature``. ``calls`` and ``inherits`` edges are resolved through each file's own definitions
    and its ``import``/``from … import`` statements to other files in the repository; they are ``inferred``, and
    dynamic dispatch or re-exports beyond one level are not followed.
    """
    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []
    parsed: dict[str, ast.Module] = {}
    modules: dict[
        str, str
    ] = {}  # dotted module name (with and without a leading "src.") → rel path

    for path in _iter_python_files(repository_root):
        rel_path = str(path.relative_to(repository_root))
        file_id = f"file:{rel_path}"
        file_meta: dict[str, object] = {"module": _module_name(rel_path)}
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source)
        except (SyntaxError, UnicodeDecodeError):
            file_meta["parse_error"] = True
            nodes.append(
                GraphNode(
                    node_id=file_id, kind="file", name=path.name, path=rel_path, metadata=file_meta
                )
            )
            continue
        file_meta["loc"] = source.count("\n") + (0 if source.endswith("\n") or not source else 1)
        doc = _doc(tree)
        if doc:
            file_meta["doc"] = doc
        nodes.append(
            GraphNode(
                node_id=file_id, kind="file", name=path.name, path=rel_path, metadata=file_meta
            )
        )
        parsed[rel_path] = tree
        module = _module_name(rel_path)
        modules[module] = rel_path
        if module.startswith("src."):
            modules[module.removeprefix("src.")] = rel_path

    # Top-level definitions per file, for resolving calls and bases.
    defs: dict[str, dict[str, str]] = {}
    methods: dict[str, dict[str, dict[str, str]]] = {}
    for rel_path, tree in parsed.items():
        file_id = f"file:{rel_path}"
        defs[rel_path] = {}
        methods[rel_path] = {}
        for item in tree.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                fn_id = f"function:{rel_path}:{item.name}"
                nodes.append(
                    GraphNode(
                        node_id=fn_id,
                        kind="function",
                        name=item.name,
                        path=rel_path,
                        metadata=_symbol_meta(item),
                    )
                )
                edges.append(GraphEdge(source=file_id, target=fn_id, kind="owns"))
                defs[rel_path][item.name] = fn_id
            elif isinstance(item, ast.ClassDef):
                cls_id = f"class:{rel_path}:{item.name}"
                nodes.append(
                    GraphNode(
                        node_id=cls_id,
                        kind="class",
                        name=item.name,
                        path=rel_path,
                        metadata=_symbol_meta(item),
                    )
                )
                edges.append(GraphEdge(source=file_id, target=cls_id, kind="owns"))
                defs[rel_path][item.name] = cls_id
                methods[rel_path][item.name] = {}
                for member in item.body:
                    if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        m_id = f"method:{rel_path}:{item.name}.{member.name}"
                        nodes.append(
                            GraphNode(
                                node_id=m_id,
                                kind="method",
                                name=f"{item.name}.{member.name}",
                                path=rel_path,
                                metadata=_symbol_meta(member),
                            )
                        )
                        edges.append(GraphEdge(source=cls_id, target=m_id, kind="owns"))
                        methods[rel_path][item.name][member.name] = m_id

            if isinstance(item, ast.Import):
                for alias in item.names:
                    edges.append(
                        GraphEdge(source=file_id, target=f"module:{alias.name}", kind="imports")
                    )
            elif isinstance(item, ast.ImportFrom) and item.module:
                edges.append(
                    GraphEdge(source=file_id, target=f"module:{item.module}", kind="imports")
                )

    seen: set[tuple[str, str, str]] = set()
    for rel_path, tree in parsed.items():
        # Names this file can see: its own definitions and what it imports from repository files.
        names: dict[str, str] = dict(defs[rel_path])
        module_alias: dict[str, str] = {}
        for item in tree.body:
            if isinstance(item, ast.ImportFrom) and item.module and item.module in modules:
                target_file = modules[item.module]
                for alias in item.names:
                    if alias.name in defs.get(target_file, {}):
                        names[alias.asname or alias.name] = defs[target_file][alias.name]
                    sub = f"{item.module}.{alias.name}"
                    if sub in modules:
                        module_alias[alias.asname or alias.name] = modules[sub]
            elif isinstance(item, ast.Import):
                for alias in item.names:
                    if alias.name in modules:
                        module_alias[alias.asname or alias.name.split(".")[0]] = modules[alias.name]

        def resolve(
            func: ast.expr,
            cls: str | None,
            rel: str = rel_path,
            scope: dict[str, str] = names,
            aliases: dict[str, str] = module_alias,
        ) -> str | None:
            if isinstance(func, ast.Name):
                return scope.get(func.id)
            if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                owner = func.value.id
                if owner == "self" and cls is not None:
                    return methods[rel].get(cls, {}).get(func.attr)
                if owner in aliases:
                    return defs.get(aliases[owner], {}).get(func.attr)
                owner_id = scope.get(owner, "")
                if owner_id.startswith(
                    "class:"
                ):  # Cls.method(...): a classmethod, staticmethod or explicit call
                    _, cls_file, cls_name = owner_id.split(":", 2)
                    return methods.get(cls_file, {}).get(cls_name, {}).get(func.attr)
            return None

        def add(source: str, target: str, kind: str, line: int, rel: str = rel_path) -> None:
            key = (source, target, kind)
            if source != target and key not in seen:
                seen.add(key)
                edges.append(
                    GraphEdge(
                        source=source,
                        target=target,
                        kind=kind,
                        origin="inferred",
                        confidence=0.8,
                        source_location=f"{rel}:{line}",
                    )
                )

        scopes: list[tuple[str, ast.AST, str | None]] = []
        for item in tree.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                scopes.append((f"function:{rel_path}:{item.name}", item, None))
            elif isinstance(item, ast.ClassDef):
                for base in item.bases:
                    target = resolve(base, None)
                    if target and target.startswith("class:"):
                        add(f"class:{rel_path}:{item.name}", target, "inherits", item.lineno)
                for member in item.body:
                    if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        scopes.append(
                            (f"method:{rel_path}:{item.name}.{member.name}", member, item.name)
                        )
        for caller, body, cls in scopes:
            for call_node in ast.walk(body):
                if isinstance(call_node, ast.Call):
                    target = resolve(call_node.func, cls)
                    if target:
                        add(caller, target, "calls", getattr(call_node, "lineno", 0))

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
    changed_modules: set[str] = set()
    for node in snapshot.nodes:
        if node.kind == "file" and node.path in path_set:
            module = str(node.metadata.get("module") or _module_name(node.path))
            changed_modules |= {module, module.removeprefix("src.")}
    for edge in snapshot.edges:
        if edge.kind == "owns" and edge.source.startswith("file:"):
            file_path = edge.source.removeprefix("file:")
            if file_path in path_set:
                impacted_symbols.append(edge.target)
        elif edge.kind == "imports" and edge.source.startswith("file:"):
            # A file that imports a changed module is impacted (dependents, not dependencies).
            importer = edge.source.removeprefix("file:")
            if edge.target.removeprefix("module:") in changed_modules and importer not in path_set:
                impacted_files.add(importer)
        elif (
            edge.kind == "calls"
            and edge.target.split(":")[1:2]
            and edge.target.split(":")[1] in path_set
        ):
            caller_file = edge.source.split(":")[1]
            if caller_file not in path_set:
                impacted_files.add(caller_file)

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
