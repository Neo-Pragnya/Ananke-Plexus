"""Symbol-level editing for Python (FUT-03) and type-aware references (FUT-02), without Serena.

* ``replace``: swap a function's, method's or class's whole definition for new source, re-indented to fit;
* ``insert``: add new source before or after a symbol, at its indentation;
* ``rename``: change a name at its definition and at every reference found;
* ``references``: where a symbol is used, resolved by type with Jedi (MIT, the optional ``typed`` extra), or from the
  code graph's call edges without it.

Every edit is a **dry run** that returns a unified diff until ``write`` is set. Before writing, each changed file must
still parse, the new definition must define the name it replaces, and a file that changed since it was read is
refused. A rename changes only real name tokens (never text in strings or comments); with Jedi each reference is
resolved by type, and without it references come from the graph and are labelled inferred.
"""

from __future__ import annotations

import ast
import difflib
import hashlib
import io
import keyword
import textwrap
import tokenize
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class EditError(ValueError):
    """An edit that can't be made safely; the message says why and what to do."""


@dataclass
class Located:
    path: str
    name: str
    start: int  # first line, decorators included (1-based)
    end: int  # last line (1-based, inclusive)
    def_line: int
    indent: str


def _defs(tree: ast.Module) -> list[tuple[str, ast.AST]]:
    """Every function, method and class with its dotted name (``Cart.total``)."""
    out: list[tuple[str, ast.AST]] = []

    def walk(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = f"{prefix}{child.name}"
                out.append((name, child))
                walk(child, f"{name}.")

    walk(tree, "")
    return out


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise EditError(f"{path} can't be read: {exc}") from exc


def _python_files(root: Path) -> list[Path]:
    from ananke.plexus.graph.service import _iter_python_files

    return _iter_python_files(root)


def locate(root: Path, name: str, path: str | None = None) -> Located:
    """The one definition ``name`` (``total``, ``Cart.total``) means; several are an error that lists them."""
    root = root.resolve()
    files = [root / path] if path else _python_files(root)
    hits: list[Located] = []
    for f in files:
        if not f.is_file():
            raise EditError(f"{path} doesn't exist")
        text = _read(f)
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        lines = text.splitlines()
        for dotted, node in _defs(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            if dotted == name or (dotted.rsplit(".", 1)[-1] == name and "." not in name):
                start = min([node.lineno, *(d.lineno for d in node.decorator_list)])
                line = lines[node.lineno - 1]
                hits.append(
                    Located(
                        f.relative_to(root).as_posix(), dotted, start, node.end_lineno or node.lineno,
                        node.lineno, line[: len(line) - len(line.lstrip())],
                    )
                )  # fmt: skip
    if not hits:
        raise EditError(
            f"no function, method or class named {name!r}; check `ananke context outline FILE`"
        )
    if len(hits) > 1:
        where = ", ".join(f"{h.path}:{h.def_line} ({h.name})" for h in hits[:8])
        raise EditError(
            f"{name!r} names {len(hits)} definitions ({where}); add --path or use Class.method"
        )
    return hits[0]


def _diff(path: str, before: str, after: str) -> str:
    return "".join(
        difflib.unified_diff(
            before.splitlines(True), after.splitlines(True), f"a/{path}", f"b/{path}"
        )
    )


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _commit(root: Path, changes: dict[str, tuple[str, str]], write: bool) -> dict[str, Any]:
    """Check every changed file parses, then write them all or none."""
    for rel, (_, after) in changes.items():
        try:
            ast.parse(after)
        except SyntaxError as exc:
            raise EditError(
                f"the edit would leave {rel} unparseable at line {exc.lineno}: {exc.msg}"
            ) from exc
    diffs = {
        rel: _diff(rel, before, after)
        for rel, (before, after) in changes.items()
        if before != after
    }
    if write:
        for rel, (before, _) in changes.items():
            target = root / rel
            if _digest(_read(target)) != _digest(before):
                raise EditError(
                    f"{rel} changed since it was read; nothing was written, run the edit again"
                )
        for rel, (before, after) in changes.items():
            if before != after:
                (root / rel).write_text(after, encoding="utf-8")
    return {
        "written": write,
        "files": sorted(diffs),
        "diff": "".join(diffs[k] for k in sorted(diffs)),
    }


def _fit(source: str, indent: str) -> list[str]:
    body = textwrap.dedent(source).strip("\n")
    return [(indent + ln) if ln.strip() else "" for ln in body.splitlines()]


def replace(
    root: Path, name: str, source: str, *, path: str | None = None, write: bool = False
) -> dict[str, Any]:
    root = root.resolve()
    loc = locate(root, name, path)
    try:
        new_tree = ast.parse(textwrap.dedent(source))
    except SyntaxError as exc:
        raise EditError(f"the new source doesn't parse at line {exc.lineno}: {exc.msg}") from exc
    defined = [
        n.name
        for n in new_tree.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    ]
    short = loc.name.rsplit(".", 1)[-1]
    if defined != [short]:
        raise EditError(
            f"the new source must define exactly {short!r} (it defines {defined or 'nothing'}); use rename to change a name"
        )
    before = _read(root / loc.path)
    lines = before.splitlines()
    new = lines[: loc.start - 1] + _fit(source, loc.indent) + lines[loc.end :]
    after = "\n".join(new) + ("\n" if before.endswith("\n") else "")
    result = _commit(root, {loc.path: (before, after)}, write)
    return {
        "action": "replace",
        "symbol": loc.name,
        "path": loc.path,
        "lines": [loc.start, loc.end],
        **result,
    }


def insert(
    root: Path, source: str, *, after: str | None = None, before: str | None = None, path: str | None = None,
    write: bool = False,
) -> dict[str, Any]:  # fmt: skip
    if bool(after) == bool(before):
        raise EditError("give exactly one of --after or --before")
    root = root.resolve()
    anchor = locate(root, after or before or "", path)
    try:
        ast.parse(textwrap.dedent(source))
    except SyntaxError as exc:
        raise EditError(f"the new source doesn't parse at line {exc.lineno}: {exc.msg}") from exc
    text = _read(root / anchor.path)
    lines = text.splitlines()
    block = _fit(source, anchor.indent)
    gap = [""] * (1 if anchor.indent else 2)
    at = anchor.end if after else anchor.start - 1
    new = lines[:at] + (gap + block if after else block + gap) + lines[at:]
    result = _commit(
        root, {anchor.path: (text, "\n".join(new) + ("\n" if text.endswith("\n") else ""))}, write
    )
    return {"action": "insert", "anchor": anchor.name, "path": anchor.path, "where": "after" if after else "before",
            **result}  # fmt: skip


def _jedi() -> Any:
    try:
        import jedi  # type: ignore[import-not-found,unused-ignore]
    except ImportError:
        return None
    return jedi


def references(root: Path, name: str, *, path: str | None = None) -> dict[str, Any]:
    """Where a symbol is used: by type with Jedi when it's installed, otherwise from the code graph (inferred)."""
    root = root.resolve()
    loc = locate(root, name, path)
    jedi = _jedi()
    rows: list[dict[str, Any]] = []
    if jedi is not None:
        text = _read(root / loc.path)
        line = text.splitlines()[loc.def_line - 1]
        short = loc.name.rsplit(".", 1)[-1]
        column = line.index(short, len(line) - len(line.lstrip()))
        project = jedi.Project(path=str(root))
        script = jedi.Script(code=text, path=str(root / loc.path), project=project)
        for ref in script.get_references(loc.def_line, column, include_builtins=False):
            if ref.module_path is None:
                continue
            try:
                rel = Path(ref.module_path).resolve().relative_to(root).as_posix()
            except ValueError:
                continue
            rows.append({"path": rel, "line": ref.line, "column": ref.column, "definition": ref.is_definition(),
                         "status": "resolved by type (jedi)"})  # fmt: skip
        method = "jedi"
    else:
        from ananke.plexus.context import fresh_graph

        graph = fresh_graph(root, save=False)
        ids = {n.node_id for n in graph.nodes}
        target = next((i for i in (f"{k}:{loc.path}:{loc.name}" for k in ("function", "method", "class")) if i in ids),
                      None)  # fmt: skip
        rows.append(
            {
                "path": loc.path,
                "line": loc.def_line,
                "column": None,
                "definition": True,
                "status": "observed",
            }
        )
        for e in graph.edges:
            if e.target == target and e.kind in {"calls", "inherits"} and e.source_location:
                where, _, ln = e.source_location.rpartition(":")
                if ln.isdigit():
                    rows.append({"path": where.split("#", 1)[0], "line": int(ln), "column": None, "definition": False,
                                 "status": f"{e.origin} from the code graph"})  # fmt: skip
        method = "graph"
    rows.sort(key=lambda r: (r["path"], r["line"], r["column"] or 0))
    return {"symbol": loc.name, "definition": f"{loc.path}:{loc.def_line}", "method": method, "references": rows,
            "limits": [] if method == "jedi" else [
                "Jedi isn't installed (pip install 'ananke-plexus[typed]'), so references come from the graph's call "
                "edges: imports and attribute uses that aren't calls aren't listed."]}  # fmt: skip


def _rename_tokens(
    text: str, old: str, new: str, lines: set[int] | None, spots: set[tuple[int, int]] | None = None
) -> str:
    """``old`` → ``new`` in NAME tokens only: at exact (line, column) ``spots`` when known, else on ``lines``.
    Strings and comments are never touched."""
    out = []
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (tokenize.TokenError, IndentationError) as exc:
        raise EditError(f"can't tokenize: {exc}") from exc
    for tok in tokens:
        here = tok.start in spots if spots is not None else (lines is None or tok.start[0] in lines)
        if tok.type == tokenize.NAME and tok.string == old and here:
            tok = tok._replace(string=new)
        out.append(tok)
    return str(tokenize.untokenize(out))


def _leftovers(root: Path, name: str, changes: dict[str, tuple[str, str]]) -> list[str]:
    """Name tokens still spelled ``name`` after the rename: uses the graph couldn't tie to the symbol."""
    left = []
    for f in _python_files(root):
        rel = f.relative_to(root).as_posix()
        text = changes[rel][1] if rel in changes else _read(f)
        try:
            tokens = tokenize.generate_tokens(io.StringIO(text).readline)
            left += [
                f"{rel}:{t.start[0]}"
                for t in tokens
                if t.type == tokenize.NAME and t.string == name
            ]
        except (tokenize.TokenError, IndentationError):
            continue
    return left


def rename(
    root: Path, old: str, new: str, *, path: str | None = None, write: bool = False
) -> dict[str, Any]:
    if not new.isidentifier() or keyword.iskeyword(new):
        raise EditError(f"{new!r} isn't a valid Python name")
    root = root.resolve()
    loc = locate(root, old, path)
    short = loc.name.rsplit(".", 1)[-1]
    refs = references(root, old, path=path)
    by_file: dict[str, set[int]] = {}
    spots: dict[str, set[tuple[int, int]]] = {}
    for r in refs["references"]:
        by_file.setdefault(r["path"], set()).add(r["line"])
        if r["column"] is not None:
            spots.setdefault(r["path"], set()).add((r["line"], r["column"]))
    if refs["method"] == "graph":  # imports aren't calls: ``from pkg.mod import old`` names it too
        for f in _python_files(root):
            rel = f.relative_to(root).as_posix()
            text = _read(f)
            try:
                tree = ast.parse(text)
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and any(a.name == short for a in node.names):
                    by_file.setdefault(rel, set()).add(node.lineno)
    changes = {}
    for rel, lines in sorted(by_file.items()):
        before = _read(root / rel)
        exact = spots.get(rel) if refs["method"] == "jedi" else None
        after = _rename_tokens(before, short, new, lines, exact)
        changes[rel] = (before, after)
    unresolved = _leftovers(root, short, changes) if refs["method"] == "graph" else []
    if unresolved and write:
        where = ", ".join(unresolved[:8])
        raise EditError(
            f"{len(unresolved)} other uses of {short!r} weren't resolved by the graph ({where}), so renaming only "
            "some would break the code; install the typed extra (pip install 'ananke-plexus[typed]') for references "
            "by type, or rename them yourself"
        )
    result = _commit(root, changes, write)
    result["unresolved"] = unresolved
    return {"action": "rename", "symbol": loc.name, "to": new, "method": refs["method"],
            "references": len(refs["references"]), **result, "limits": refs["limits"]}  # fmt: skip
