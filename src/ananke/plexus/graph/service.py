"""Graph build, query, and impact services."""

import ast
import hashlib
import json
import posixpath
from datetime import UTC, datetime
from pathlib import Path

from ananke.plexus.graph.models import GraphEdge, GraphNode, GraphSnapshot, ImpactReport

_SKIP_DIRS = {"node_modules", "site-packages", "__pycache__"}
_MAX_NOTEBOOK_BYTES = 50_000_000  # outputs can make a notebook huge; only its source is used
_DATA_SUFFIXES = {
    ".csv", ".tsv", ".parquet", ".json", ".jsonl", ".xlsx", ".xls", ".feather", ".pkl", ".pickle",
    ".npy", ".npz", ".h5", ".hdf5", ".arrow", ".orc", ".avro",
}  # fmt: skip
_DATA_READERS = {
    "read_csv", "read_table", "read_parquet", "read_json", "read_excel", "read_feather", "read_pickle",
    "read_hdf", "read_orc", "load", "loadtxt", "genfromtxt",
}  # fmt: skip
_DATA_WRITERS = {
    "to_csv", "to_parquet", "to_json", "to_excel", "to_feather", "to_pickle", "to_hdf", "to_orc",
    "save", "savez", "savetxt",
}  # fmt: skip
_PYTHON_CELL_MAGICS = {"%%time", "%%timeit", "%%capture", "%%prun"}  # the body is ordinary Python
_PATH_READERS = {"read_text", "read_bytes"}
_PATH_WRITERS = {"write_text", "write_bytes"}
_PATH_KEYWORDS = ("path", "filepath_or_buffer", "path_or_buf", "file", "fname")


def _iter_project_files(repository_root: Path, pattern: str) -> list[Path]:
    """Sources of the project itself: not hidden directories (.git, .venv, caches), dependencies or virtual
    environments (any directory holding a ``pyvenv.cfg``)."""
    venvs = {cfg.parent for cfg in repository_root.rglob("pyvenv.cfg")}
    found = []
    for path in repository_root.rglob(pattern):
        rel_parts = path.relative_to(repository_root).parts[:-1]
        if any(part.startswith(".") or part in _SKIP_DIRS for part in rel_parts):
            continue
        if any(parent in venvs for parent in path.parents):
            continue
        found.append(path)
    return sorted(found)


def _iter_python_files(repository_root: Path) -> list[Path]:
    return _iter_project_files(repository_root, "*.py")


def _parse_notebook_source(source: str) -> ast.Module:
    """Top-level ``await`` is valid in a notebook."""
    flags = ast.PyCF_ONLY_AST | ast.PyCF_ALLOW_TOP_LEVEL_AWAIT
    tree: ast.Module = compile(source, "<notebook>", "exec", flags=flags)
    return tree


def _read_notebook(path: Path) -> tuple[str, list[tuple[int, int]], dict[str, object]]:
    """A notebook's Python as one module, without running anything.

    Returns the source, a map from each of its lines to ``(cell, line in cell)`` (both from 1) and metadata.
    Magics become ``pass`` so line numbers hold; a cell magic or a cell that doesn't parse is left out and listed.
    """
    if path.stat().st_size > _MAX_NOTEBOOK_BYTES:
        raise ValueError("notebook too large")
    notebook = json.loads(path.read_text(encoding="utf-8"))
    cells = notebook["cells"]  # nbformat 3 has worksheets instead, and is reported as a parse error
    info = notebook.get("metadata", {})
    language = info.get("kernelspec", {}).get("language") or info.get("language_info", {}).get(
        "name", "python"
    )
    meta: dict[str, object] = {"notebook": True, "language": language}
    if language != "python":
        return "", [], meta
    lines: list[str] = []
    line_map: list[tuple[int, int]] = []
    unparsed: list[int] = []
    magics: dict[str, int] = {}
    code_cells = 0
    for number, cell in enumerate(cells, 1):
        if cell.get("cell_type") != "code":
            continue
        code_cells += 1
        raw = cell.get("source", "")
        text = "".join(raw) if isinstance(raw, list) else str(raw)
        magic_name = text.lstrip().split(maxsplit=1)[0] if text.lstrip().startswith("%%") else ""
        if magic_name and magic_name not in _PYTHON_CELL_MAGICS:
            # %%writefile, %%bash and the like: the body isn't this notebook's own Python
            unparsed.append(number)
            magics[magic_name] = magics.get(magic_name, 0) + 1
            continue
        cleaned = []
        for line in text.splitlines():
            body = line.lstrip()
            magic = body.startswith(("%", "!")) or body.endswith("?")
            cleaned.append(f"{line[: len(line) - len(body)]}pass" if magic else line)
        try:
            _parse_notebook_source("\n".join(cleaned))
        except SyntaxError:
            unparsed.append(number)
            continue
        lines += cleaned
        line_map += [(number, i) for i in range(1, len(cleaned) + 1)]
    meta["code_cells"] = code_cells
    if unparsed:
        meta["unparsed_cells"] = unparsed
    if magics:
        meta["cell_magics"] = dict(sorted(magics.items()))
    return "\n".join(lines) + "\n", line_map, meta


def _literal(node: ast.expr | None) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _data_access(call: ast.Call) -> list[tuple[str, str]]:
    """``(reads|writes, path)`` for a call naming a data file with a literal path.

    Computed paths aren't followed: a graph can only state what the source states.
    """
    func = call.func
    name = (
        func.attr
        if isinstance(func, ast.Attribute)
        else func.id
        if isinstance(func, ast.Name)
        else ""
    )
    first = call.args[0] if call.args else None
    keyword = next((k.value for k in call.keywords if k.arg in _PATH_KEYWORDS), None)
    target = _literal(first) or _literal(keyword)
    if name in _PATH_READERS | _PATH_WRITERS and isinstance(func, ast.Attribute):
        owner = func.value
        target = (
            _literal(owner.args[0])
            if isinstance(owner, ast.Call)
            and owner.args
            and getattr(owner.func, "id", "") == "Path"
            else None
        )
        access = ["reads"] if name in _PATH_READERS else ["writes"]
    elif name == "open":
        mode_node = call.args[1] if len(call.args) > 1 else None
        mode = _literal(mode_node) or _literal(
            next((k.value for k in call.keywords if k.arg == "mode"), None)
        )
        mode = mode or "r"
        access = [
            *(["reads"] if "r" in mode or "+" in mode else []),
            *(["writes"] if set(mode) & set("wax+") else []),
        ]
    elif name in _DATA_READERS:
        access = ["reads"]
    elif name in _DATA_WRITERS:
        access = ["writes"]
    else:
        return []
    if not target or posixpath.splitext(target)[1].lower() not in _DATA_SUFFIXES:
        return []
    return [(kind, target) for kind in access]


def _data_path(raw: str, rel_path: str, repository_root: Path) -> str | None:
    """A data path inside the repository, relative to its root. URLs and absolute paths are left out."""
    if "://" in raw or raw.startswith(("/", "~")) or "\\" in raw:
        return None
    here = posixpath.normpath(posixpath.join(posixpath.dirname(rel_path), raw))
    from_root = posixpath.normpath(raw)
    # a notebook runs from its own folder; a module usually runs from the repository root
    order = (here, from_root) if rel_path.endswith(".ipynb") else (from_root, here)
    inside = [p for p in order if p != ".." and not p.startswith("../")]
    if not inside:
        return None
    return next((p for p in inside if (repository_root / p).is_file()), inside[0])


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
    """Python files and notebooks, their functions and classes, methods, imports, calls, bases and data files.

    Node ids are stable: ``file:<path>``, ``function:<path>:<name>``, ``class:<path>:<name>`` and
    ``method:<path>:<Class>.<name>``. Symbol metadata carries ``line``, ``end_line``, a one-line ``doc`` and, for
    functions, a ``signature``. ``calls`` and ``inherits`` edges are resolved through each file's own definitions
    and its ``import``/``from … import`` statements to other files in the repository; they are ``inferred``, and
    dynamic dispatch or re-exports beyond one level are not followed.

    A notebook is a ``file`` node with ``notebook: true``, and its symbols take the same id forms, so adding one
    never changes another file's ids. Its locations read ``<path>#cell=<n>:<line>``, its top-level calls belong to
    the file, and nothing in it is run. ``data:<path>`` nodes and inferred ``reads``/``writes`` edges come from calls
    naming a data file with a literal path; computed paths, URLs and paths outside the repository aren't included.
    """
    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []
    parsed: dict[str, ast.Module] = {}
    modules: dict[
        str, str
    ] = {}  # dotted module name (with and without a leading "src.") → rel path
    cell_maps: dict[str, list[tuple[int, int]]] = {}

    def where(rel: str, line: int) -> str:
        cells = cell_maps.get(rel)
        if cells is None:
            return f"{rel}:{line}"
        cell, in_cell = cells[line - 1] if 0 < line <= len(cells) else (0, line)
        return f"{rel}#cell={cell}:{in_cell}"

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

    for path in _iter_project_files(repository_root, "*.ipynb"):
        rel_path = str(path.relative_to(repository_root))
        file_id = f"file:{rel_path}"
        try:
            source, line_map, file_meta = _read_notebook(path)
            tree = _parse_notebook_source(source)
        except (ValueError, KeyError, TypeError, AttributeError, SyntaxError, OSError):
            nodes.append(
                GraphNode(
                    node_id=file_id,
                    kind="file",
                    name=path.name,
                    path=rel_path,
                    metadata={"notebook": True, "parse_error": True},
                )
            )
            continue
        file_meta["loc"] = len(line_map)
        nodes.append(
            GraphNode(
                node_id=file_id, kind="file", name=path.name, path=rel_path, metadata=file_meta
            )
        )
        if line_map:
            parsed[rel_path] = tree
            cell_maps[rel_path] = line_map

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
                edges.append(
                    GraphEdge(
                        source=file_id,
                        target=fn_id,
                        kind="owns",
                        source_location=where(rel_path, item.lineno),
                    )
                )
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
                edges.append(
                    GraphEdge(
                        source=file_id,
                        target=cls_id,
                        kind="owns",
                        source_location=where(rel_path, item.lineno),
                    )
                )
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
                        edges.append(
                            GraphEdge(
                                source=cls_id,
                                target=m_id,
                                kind="owns",
                                source_location=where(rel_path, member.lineno),
                            )
                        )
                        methods[rel_path][item.name][member.name] = m_id

            if isinstance(item, ast.Import):
                for alias in item.names:
                    edges.append(
                        GraphEdge(
                            source=file_id,
                            target=f"module:{alias.name}",
                            kind="imports",
                            source_location=where(rel_path, item.lineno),
                        )
                    )
            elif isinstance(item, ast.ImportFrom) and item.module:
                edges.append(
                    GraphEdge(
                        source=file_id,
                        target=f"module:{item.module}",
                        kind="imports",
                        source_location=where(rel_path, item.lineno),
                    )
                )

    seen: set[tuple[str, str, str]] = set()
    data_nodes: dict[str, GraphNode] = {}
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
                        source_location=where(rel, line),
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
        file_id = f"file:{rel_path}"
        top_level: list[tuple[str, ast.AST, str | None]] = [
            (file_id, item, None)
            for item in tree.body
            if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        ]
        # Most of a notebook is top-level code; in a module that is import-time code, left out as before.
        call_scopes = scopes + top_level if rel_path in cell_maps else scopes
        for caller, body, cls in call_scopes:
            for call_node in ast.walk(body):
                if isinstance(call_node, ast.Call):
                    target = resolve(call_node.func, cls)
                    if target:
                        add(caller, target, "calls", getattr(call_node, "lineno", 0))
        for caller, body, _ in scopes + top_level:
            for call_node in ast.walk(body):
                if not isinstance(call_node, ast.Call):
                    continue
                for access, raw in _data_access(call_node):
                    data_path = _data_path(raw, rel_path, repository_root)
                    if data_path is None:
                        continue
                    data_id = f"data:{data_path}"
                    if data_id not in data_nodes:
                        data_nodes[data_id] = GraphNode(
                            node_id=data_id,
                            kind="data",
                            name=posixpath.basename(data_path),
                            path=data_path,
                            metadata={"exists": (repository_root / data_path).is_file()},
                        )
                    add(caller, data_id, access, getattr(call_node, "lineno", 0))

    nodes += [data_nodes[k] for k in sorted(data_nodes)]
    for node in nodes:
        cells = cell_maps.get(node.path)
        line = node.metadata.get("line")
        if cells and isinstance(line, int) and 0 < line <= len(cells):
            end = node.metadata.get("end_line")
            node.metadata["cell"], node.metadata["line"] = cells[line - 1]
            if isinstance(end, int) and 0 < end <= len(cells):
                node.metadata["end_line"] = cells[end - 1][1]

    snapshot_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return GraphSnapshot(snapshot_id=snapshot_id, nodes=nodes, edges=edges)


def graph_digest(snapshot: GraphSnapshot) -> str:
    """Identifies a graph's content: the same sources give the same digest, whenever it was built."""
    nodes = sorted(
        (n.model_dump(mode="json") for n in snapshot.nodes), key=lambda n: str(n["node_id"])
    )
    edges = sorted(
        ({**e.model_dump(mode="json"), "snapshot_id": ""} for e in snapshot.edges),
        key=lambda e: (
            str(e["source"]),
            str(e["target"]),
            str(e["kind"]),
            str(e["source_location"]),
        ),
    )
    payload = json.dumps({"nodes": nodes, "edges": edges}, sort_keys=True, ensure_ascii=False)
    return f"sha256:{hashlib.sha256(payload.encode()).hexdigest()}"


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
        elif edge.kind == "reads" and edge.target.removeprefix("data:") in path_set:
            reader_file = edge.source.split(":")[1]
            if reader_file not in path_set:
                impacted_files.add(reader_file)

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
