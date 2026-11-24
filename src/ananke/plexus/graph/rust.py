"""Rust in the graph: read as text with a small lexer, no compiler and no slow regular expressions.

Ids take the same forms as Python: ``function:<path>:<name>``, ``class:<path>:<Type>`` (structs, enums, unions,
type aliases and traits, with ``rust_kind`` in metadata) and ``method:<path>:<Type>.<name>`` for functions inside
``impl`` and ``trait`` blocks. Functions in an inline module are named ``<mod>::<name>`` (``tests::parses``) so a
test never shadows the function it tests. Symbol metadata carries ``line``, ``end_line``, ``doc`` (the first line of
its ``///`` comment), ``signature`` and, for ``#[test]`` functions, ``test: true``.

``imports`` edges come from ``use`` and ``mod name;``. ``crate::``, ``self::``, ``super::`` and paths through a crate
of the same workspace (found by its ``Cargo.toml``) resolve to the file of the deepest module that exists.
``calls`` edges are ``inferred`` and drawn only when the target is unambiguous: a function of the same file, a name
brought in by ``use``, ``Type::name`` with one such method, ``self.name`` inside an impl, or ``.name(`` when exactly
one method in the repository has that name (lower confidence). Trait dispatch and macros aren't followed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from ananke.plexus.graph.models import GraphEdge, GraphNode

RUST_SUFFIXES = (".rs",)
RUST_SKIP_DIRS = frozenset({"target", "vendor", "third_party"})
MAX_SOURCE_BYTES = 2_000_000

_TYPE_KEYWORDS = {
    "struct": "struct",
    "enum": "enum",
    "union": "union",
    "type": "type",
    "trait": "trait",
}
_NOT_CALLS = {
    "if", "while", "for", "match", "return", "loop", "fn", "Some", "Ok", "Err", "None", "as", "in", "let",
    "move", "where", "impl", "dyn", "mut", "ref", "unsafe", "async", "await", "Box", "Vec", "String",
}  # fmt: skip


@dataclass
class Tok:
    kind: str  # "id", "p" (punctuation), "lit"
    text: str
    line: int
    pos: int


@dataclass
class _Parsed:
    rel: str
    nodes: list[GraphNode] = field(default_factory=list)
    owns: list[GraphEdge] = field(default_factory=list)
    uses: list[tuple[list[str], str, int]] = field(default_factory=list)  # path, alias, line
    mods: list[tuple[str, int]] = field(default_factory=list)  # ``mod name;``
    calls: list[tuple[str, str, str, str | None, int]] = field(default_factory=list)
    # (caller id, form, name, qualifier/type, line); form: "free", "path", "self", "dot"
    impls: list[tuple[str, str, int]] = field(default_factory=list)  # (type, trait, line)
    loc: int = 0


_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_NUMBER = re.compile(r"[0-9][0-9A-Za-z_.]*")


def tokenize(text: str) -> tuple[list[Tok], dict[int, str]]:
    """Tokens without comments or literal contents, and the ``///`` doc text by the line it ends on."""
    toks: list[Tok] = []
    docs: dict[int, str] = {}
    i, n, line = 0, len(text), 1
    while i < n:
        c = text[i]
        if c == "\n":
            line += 1
            i += 1
        elif c in " \t\r":
            i += 1
        elif text.startswith("//", i):
            end = text.find("\n", i)
            end = n if end < 0 else end
            body = text[i:end]
            if body.startswith("///") and not body.startswith("////"):
                docs[line] = body[3:].strip()
            i = end
        elif text.startswith("/*", i):
            depth, i = 1, i + 2
            while i < n and depth:
                if text.startswith("/*", i):
                    depth, i = depth + 1, i + 2
                elif text.startswith("*/", i):
                    depth, i = depth - 1, i + 2
                else:
                    line += text[i] == "\n"
                    i += 1
        elif c == '"' or (c in "br" and _raw_or_byte_string(text, i)):
            start = i
            i = _skip_string(text, i)
            line += text.count("\n", start, i)
            toks.append(Tok("lit", '""', line, start))
        elif c == "'":
            # a char literal ('a', '\n', '\u{1F600}') or a lifetime ('a)
            if i + 2 < n and text[i + 1] == "\\":
                end = text.find("'", i + 2)
                i = n if end < 0 else end + 1
                toks.append(Tok("lit", "''", line, i))
            elif i + 2 < n and text[i + 2] == "'":
                toks.append(Tok("lit", "''", line, i))
                i += 3
            else:
                m = _IDENT.match(text, i + 1)
                i = m.end() if m else i + 1
                toks.append(Tok("p", "'", line, i))
        elif c.isalpha() or c == "_":
            m = _IDENT.match(text, i)
            end = m.end() if m else i + 1
            toks.append(Tok("id", text[i:end], line, i))
            i = end
        elif c.isdigit():
            m = _NUMBER.match(text, i)
            end = m.end() if m else i + 1
            toks.append(Tok("lit", "0", line, i))
            i = end
        else:
            if text.startswith("::", i):
                toks.append(Tok("p", "::", line, i))
                i += 2
            elif text.startswith("->", i):
                toks.append(Tok("p", "->", line, i))
                i += 2
            else:
                toks.append(Tok("p", c, line, i))
                i += 1
    return toks, docs


def _raw_or_byte_string(text: str, i: int) -> bool:
    m = re.compile(r'b?r#*"|b"').match(text, i)
    return m is not None and (i == 0 or not (text[i - 1].isalnum() or text[i - 1] == "_"))


def _skip_string(text: str, i: int) -> int:
    n = len(text)
    if text[i] == "b":
        i += 1
    if text[i] == "r":
        i += 1
        hashes = 0
        while i < n and text[i] == "#":
            hashes, i = hashes + 1, i + 1
        close = '"' + "#" * hashes
        end = text.find(close, i + 1)
        return n if end < 0 else end + len(close)
    i += 1  # opening quote
    while i < n:
        if text[i] == "\\":
            i += 2
        elif text[i] == '"':
            return i + 1
        else:
            i += 1
    return n


class _Parser:
    def __init__(self, rel: str, text: str) -> None:
        self.rel = rel
        self.text = text
        self.toks, self.docs = tokenize(text)
        self.lines = text.split("\n")
        self.out = _Parsed(rel=rel, loc=text.count("\n") + 1)
        self.symbols: dict[str, GraphNode] = {}

    # ------------------------------------------------------------------ helpers
    def match_close(self, i: int) -> int:
        """Index of the token closing the bracket at ``i``."""
        pairs = {"{": "}", "(": ")", "[": "]"}
        open_, close = self.toks[i].text, pairs[self.toks[i].text]
        depth = 0
        for j in range(i, len(self.toks)):
            t = self.toks[j]
            if t.kind == "p" and t.text == open_:
                depth += 1
            elif t.kind == "p" and t.text == close:
                depth -= 1
                if depth == 0:
                    return j
        return len(self.toks) - 1

    def skip_generics(self, i: int) -> int:
        """From a ``<`` to just past its matching ``>`` (``->`` is one token, so it never counts)."""
        depth = 0
        while i < len(self.toks):
            t = self.toks[i]
            if t.kind == "p" and t.text == "<":
                depth += 1
            elif t.kind == "p" and t.text == ">":
                depth -= 1
                if depth == 0:
                    return i + 1
            elif t.kind == "p" and t.text in "{;":
                return i
            i += 1
        return i

    def body_or_end(self, i: int) -> tuple[int | None, int]:
        """From an item header, the index of its ``{`` (or None) and of its last token."""
        depth = 0
        while i < len(self.toks):
            t = self.toks[i]
            if t.kind == "p":
                if t.text in "([":
                    i = self.match_close(i) + 1
                    continue
                if t.text == "<":
                    depth += 1
                elif t.text == ">":
                    depth -= 1
                elif t.text == "{":
                    return i, self.match_close(i)
                elif t.text == ";" and depth <= 0:
                    return None, i
            i += 1
        return None, len(self.toks) - 1

    def doc_before(self, line: int) -> str:
        """The first line of the ``///`` block just above ``line`` (attributes may sit between)."""
        k = line - 1
        lines = self.lines
        while k >= 1 and lines[k - 1].strip().startswith("#["):
            k -= 1
        first = None
        while k in self.docs:
            first = self.docs[k]
            k -= 1
        return first or ""

    def add(
        self, kind: str, name: str, start: int, end: int, cls: str | None, meta: dict[str, object]
    ) -> str:
        full = f"{cls}.{name}" if cls else name
        node_id = f"{kind}:{self.rel}:{full}"
        line, end_line = self.toks[start].line, self.toks[end].line
        existing = self.symbols.get(node_id)
        if existing is not None:  # cfg-gated twins: keep the first, widen its range
            previous = existing.metadata["end_line"]
            existing.metadata["end_line"] = max(
                previous if isinstance(previous, int) else 0, end_line
            )
            return node_id
        node = GraphNode(
            node_id=node_id,
            kind=kind,
            name=full,
            path=self.rel,
            metadata={"line": line, "end_line": end_line, "language": "rust", **meta},
        )
        doc = self.doc_before(line)
        if doc:
            node.metadata["doc"] = doc
        self.symbols[node_id] = node
        self.out.nodes.append(node)
        owner = (
            f"class:{self.rel}:{cls}"
            if cls and f"class:{self.rel}:{cls}" in self.symbols
            else f"file:{self.rel}"
        )
        self.out.owns.append(
            GraphEdge(
                source=owner, target=node_id, kind="owns", source_location=f"{self.rel}:{line}"
            )
        )
        return node_id

    def signature(self, start: int, stop: int) -> str:
        raw = self.text[self.toks[start].pos : self.toks[stop].pos]
        raw = re.sub(r"//[^\n]*", "", raw)
        return " ".join(raw.split()).rstrip()

    # ------------------------------------------------------------------ items
    def parse(self) -> _Parsed:
        self.items(0, len(self.toks), cls=None, prefix="")
        return self.out

    def items(self, i: int, stop: int, cls: str | None, prefix: str) -> None:
        attrs: list[str] = []
        while i < stop:
            t = self.toks[i]
            if t.kind == "p" and t.text == "#":
                j = i + 1 + (self.toks[i + 1].text == "!" if i + 1 < stop else 0)
                if j < stop and self.toks[j].text == "[":
                    close = self.match_close(j)
                    attrs.append(" ".join(x.text for x in self.toks[j + 1 : close]))
                    i = close + 1
                    continue
            if t.kind != "id":
                i += 1
                continue
            word = t.text
            if word == "fn" and i + 1 < stop and self.toks[i + 1].kind == "id":
                i = self.function(i, stop, cls, prefix, attrs)
                attrs = []
            elif (
                word in _TYPE_KEYWORDS
                and i + 1 < stop
                and self.toks[i + 1].kind == "id"
                and cls is None
            ):
                i = self.type_item(i, word, prefix)
                attrs = []
            elif word == "impl" and cls is None:
                i = self.impl_block(i, prefix)
                attrs = []
            elif word == "mod" and i + 1 < stop and self.toks[i + 1].kind == "id":
                name = self.toks[i + 1].text
                brace, end = self.body_or_end(i + 2)
                if brace is None:
                    self.out.mods.append((name, t.line))
                else:
                    self.items(brace + 1, end, cls=None, prefix=f"{prefix}{name}::")
                i = end + 1
                attrs = []
            elif word == "use":
                i = self.use_item(i)
                attrs = []
            elif word in {"const", "static"} and cls is None:
                _, end = self.body_or_end(i + 1)
                i = end + 1
                attrs = []
            elif word == "macro_rules":
                _, end = self.body_or_end(i + 1)
                i = end + 1
            else:
                i += 1

    def function(self, i: int, stop: int, cls: str | None, prefix: str, attrs: list[str]) -> int:
        name = self.toks[i + 1].text
        start = i
        # include leading qualifiers (pub, pub(crate), const, async, unsafe, extern "C") in the signature
        while start > 0 and self.toks[start - 1].text in {
            "pub",
            "const",
            "async",
            "unsafe",
            "extern",
            '""',
        }:
            start -= 1
        if start > 0 and self.toks[start - 1].text == ")":
            k = start - 1
            while k > 0 and self.toks[k].text != "(":
                k -= 1
            if k > 0 and self.toks[k - 1].text == "pub":
                start = k - 1
        brace, end = self.body_or_end(i + 2)
        meta: dict[str, object] = {
            "signature": self.signature(start, brace if brace is not None else end)
        }
        if any(a == "test" or a.startswith("test ") or a.endswith(":: test") for a in attrs):
            meta["test"] = True
        kind = "method" if cls else "function"
        node_id = self.add(kind, f"{prefix}{name}" if not cls else name, i, end, cls, meta)
        if brace is not None:
            self.body_calls(brace + 1, end, node_id, cls)
        return end + 1

    def type_item(self, i: int, word: str, prefix: str) -> int:
        name = self.toks[i + 1].text
        brace, end = self.body_or_end(i + 2)
        if word == "trait" and brace is not None:
            self.add("class", f"{prefix}{name}", i, end, None, {"rust_kind": "trait"})
            self.items(brace + 1, end, cls=f"{prefix}{name}", prefix="")
        else:
            self.add("class", f"{prefix}{name}", i, end, None, {"rust_kind": _TYPE_KEYWORDS[word]})
        return end + 1

    def impl_block(self, i: int, prefix: str) -> int:
        j = i + 1
        if j < len(self.toks) and self.toks[j].text == "<":
            j = self.skip_generics(j)
        header: list[str] = []  # last identifier of each path segment run, generics skipped
        trait = ""
        while j < len(self.toks) and self.toks[j].text not in {"{", ";", "where"}:
            t = self.toks[j]
            if t.text == "<":
                j = self.skip_generics(j)
                continue
            if t.text == "for":
                trait = header[-1] if header else ""
                header = []
            elif t.kind == "id" and t.text not in {"dyn", "mut", "unsafe"}:
                header.append(t.text)
            j += 1
        brace, end = self.body_or_end(j)
        type_name = header[-1] if header else ""
        if not type_name or brace is None:
            return end + 1
        cls = (
            f"{prefix}{type_name}"
            if f"class:{self.rel}:{prefix}{type_name}" in self.symbols
            else type_name
        )
        if trait:
            self.out.impls.append((cls, trait, self.toks[i].line))
        self.items(brace + 1, end, cls=cls, prefix="")
        return end + 1

    def use_item(self, i: int) -> int:
        _, end = self.body_or_end(i + 1)
        line = self.toks[i].line
        for path, alias in _use_paths(self.toks[i + 1 : end]):
            self.out.uses.append((path, alias, line))
        return end + 1

    def body_calls(self, i: int, stop: int, caller: str, cls: str | None) -> None:
        toks = self.toks
        while i < stop:
            t = toks[i]
            if (
                t.kind == "id" and t.text == "fn"
            ):  # a nested fn: its calls belong to the outer one too
                i += 1
                continue
            if (
                t.kind == "id"
                and t.text == "use"
                and (i == 0 or toks[i - 1].text in {";", "{", "}"})
            ):
                i = self.use_item(i)
                continue
            nxt = toks[i + 1] if i + 1 < stop else None
            if (
                t.kind == "id"
                and nxt is not None
                and nxt.text in {"(", "::"}
                and t.text not in _NOT_CALLS
            ):
                if nxt.text == "::" and i + 2 < stop and toks[i + 2].text == "<":  # turbofish
                    k = self.skip_generics(i + 2)
                    nxt = toks[k] if k < stop else None
                    if nxt is None or nxt.text != "(":
                        i += 1
                        continue
                if nxt.text == "(":
                    prev = toks[i - 1] if i > 0 else None
                    if prev is not None and prev.text == ".":
                        recv = toks[i - 2] if i > 1 else None
                        if recv is not None and recv.text == "self" and cls:
                            self.out.calls.append((caller, "self", t.text, cls, t.line))
                        else:
                            self.out.calls.append((caller, "dot", t.text, None, t.line))
                    elif prev is not None and prev.text == "::":
                        qual = toks[i - 2].text if i > 1 else ""
                        if qual == "Self" and cls:
                            qual = cls
                        self.out.calls.append((caller, "path", t.text, qual, t.line))
                    elif prev is None or prev.text != "!":
                        self.out.calls.append((caller, "free", t.text, None, t.line))
            i += 1


def _use_paths(toks: list[Tok]) -> list[tuple[list[str], str]]:
    """``use a::{b, c::d as e, f::*};`` → ``[(['a','b'],'b'), (['a','c','d'],'e'), (['a','f','*'],'*')]``."""
    out: list[tuple[list[str], str]] = []

    def walk(k: int, base: list[str]) -> int:
        path = list(base)
        while k < len(toks):
            t = toks[k]
            if t.text == "{":
                k += 1
                while k < len(toks) and toks[k].text != "}":
                    k = walk(k, path)
                    if k < len(toks) and toks[k].text == ",":
                        k += 1
                return k + 1
            if t.text in {",", "}"}:
                break
            if t.text == "as" and k + 1 < len(toks):
                out.append((path, toks[k + 1].text))
                return k + 2
            if t.kind == "id" or t.text == "*":
                path.append(t.text)
            k += 1
        if len(path) > len(base):
            out.append(
                (path, path[-1] if path[-1] != "self" else (path[-2] if len(path) > 1 else "self"))
            )
        return k

    walk(0, [])
    return out


# ---------------------------------------------------------------------- resolution
class _Crates:
    """Workspace crates by their Rust name (``eshana-core`` → ``eshana_core``) and each file's crate root."""

    def __init__(self, repository_root: Path, files: set[str]) -> None:
        self.files = files
        self.roots: dict[str, str] = {}  # crate name → root file (lib.rs or main.rs)
        self.dirs: list[tuple[str, str]] = []  # (crate dir, root file), longest first
        for rel in sorted(f for f in files if f.endswith(("src/lib.rs", "src/main.rs"))):
            crate_dir = rel.rsplit("src/", 1)[0].rstrip("/")
            manifest = (
                repository_root / crate_dir / "Cargo.toml"
                if crate_dir
                else repository_root / "Cargo.toml"
            )
            name = Path(crate_dir).name if crate_dir else repository_root.name
            try:
                m = re.search(
                    r'^\s*name\s*=\s*"([^"]+)"', manifest.read_text(encoding="utf-8"), re.M
                )
                if m:
                    name = m.group(1)
            except OSError:
                pass
            key = name.replace("-", "_")
            if key not in self.roots or rel.endswith("lib.rs"):
                self.roots[key] = rel
            self.dirs.append((crate_dir, rel))
        self.dirs.sort(key=lambda d: -len(d[0]))

    def root_of(self, rel: str) -> str | None:
        for crate_dir, root in self.dirs:
            if not crate_dir or rel.startswith(crate_dir + "/"):
                return root
        return None

    def module_dir(self, rel: str) -> str:
        """The directory that holds a file's child modules (``a/b.rs`` → ``a/b``; ``mod.rs``/``lib.rs`` → its dir)."""
        folder, _, name = rel.rpartition("/")
        if name in {"mod.rs", "lib.rs", "main.rs"}:
            return folder
        return f"{folder}/{name[:-3]}" if folder else name[:-3]

    def child(self, rel: str, name: str) -> str | None:
        base = self.module_dir(rel)
        for cand in (f"{base}/{name}.rs", f"{base}/{name}/mod.rs"):
            if cand in self.files:
                return cand
        return None

    def parent(self, rel: str) -> str | None:
        base = self.module_dir(rel)
        folder = base.rpartition("/")[0]
        for cand in (f"{folder}.rs", f"{folder}/mod.rs", f"{folder}/lib.rs", f"{folder}/main.rs"):
            if cand in self.files and cand != rel:
                return cand
        return None

    def resolve(self, rel: str, path: list[str]) -> tuple[str | None, list[str]]:
        """The deepest module file a ``use`` path reaches and the segments left over (the item names)."""
        if not path:
            return None, path
        head, rest = path[0], path[1:]
        if head == "crate":
            current = self.root_of(rel)
        elif head == "self":
            current = rel
        elif head == "super":
            current = self.parent(rel)
            while rest and rest[0] == "super" and current:
                current, rest = self.parent(current), rest[1:]
        elif head in self.roots:
            current = self.roots[head]
        else:
            child = self.child(rel, head)  # 2018 paths may name a child module directly
            if child is None:
                return None, path
            current = child
        while current and rest:
            nxt = self.child(current, rest[0])
            if nxt is None:
                break
            current, rest = nxt, rest[1:]
        return current, rest


def build_rust(repository_root: Path, paths: list[Path]) -> tuple[list[GraphNode], list[GraphEdge]]:
    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []
    parsed: dict[str, _Parsed] = {}
    for path in paths:
        rel = path.relative_to(repository_root).as_posix()
        if path.is_symlink():
            continue
        try:
            if path.stat().st_size > MAX_SOURCE_BYTES:
                continue
            text = path.read_text(encoding="utf-8")
        except (OSError, ValueError):
            nodes.append(_file_node(rel, path.name, {"language": "rust", "parse_error": True}))
            continue
        try:
            info = _Parser(rel, text).parse()
        except Exception:  # whatever a file holds, the rest of the repository still gets mapped
            nodes.append(_file_node(rel, path.name, {"language": "rust", "parse_error": True}))
            continue
        parsed[rel] = info
        nodes.append(_file_node(rel, path.name, {"language": "rust", "loc": info.loc}))
        nodes += info.nodes
        edges += info.owns

    crates = _Crates(repository_root, set(parsed))
    functions: dict[str, dict[str, str]] = {}  # file → name → id (top-level, unprefixed)
    methods: dict[str, list[str]] = {}  # "Type.name" → ids
    by_method: dict[str, list[str]] = {}  # "name" → method ids
    types: dict[str, list[str]] = {}  # type name → class ids
    for rel, info in parsed.items():
        for node in info.nodes:
            if node.kind == "function" and "::" not in node.name:
                functions.setdefault(rel, {})[node.name] = node.node_id
            elif node.kind == "method":
                methods.setdefault(node.name, []).append(node.node_id)
                by_method.setdefault(node.name.rsplit(".", 1)[-1], []).append(node.node_id)
            elif node.kind == "class":
                types.setdefault(node.name.rsplit("::", 1)[-1], []).append(node.node_id)

    for rel, info in parsed.items():
        source = f"file:{rel}"
        imported: dict[str, str] = {}  # alias → symbol id
        modules: dict[str, str] = {}  # alias → module file, for ``alias::name(`` calls
        for name, line in info.mods:
            child = crates.child(rel, name)
            edges.append(
                GraphEdge(
                    source=source,
                    target=f"module:{name}",
                    kind="imports",
                    source_location=f"{rel}:{line}",
                    resolved_target=f"file:{child}" if child else "",
                )
            )
        for use_path, alias, line in info.uses:
            file, rest = crates.resolve(rel, use_path)
            edges.append(
                GraphEdge(
                    source=source,
                    target=f"module:{'::'.join(use_path)}",
                    kind="imports",
                    source_location=f"{rel}:{line}",
                    resolved_target=f"file:{file}" if file else "",
                )
            )
            if file and (not rest or rest == ["self"]) and alias not in {"*", "self"}:
                modules[alias] = file
            elif file and len(rest) == 1 and alias != "*":
                fn = functions.get(file, {}).get(rest[0])
                if fn:
                    imported[alias] = fn
                elif f"class:{file}:{rest[0]}" in {n for n in types.get(rest[0], [])}:
                    imported[alias] = f"class:{file}:{rest[0]}"
        for type_name, trait, line in info.impls:
            here = types.get(type_name.rsplit("::", 1)[-1], [])
            there = [t for t in types.get(trait, []) if t]
            if len(here) == 1 and len(there) == 1:
                edges.append(
                    GraphEdge(
                        source=here[0],
                        target=there[0],
                        kind="implements",
                        origin="inferred",
                        confidence=0.8,
                        source_location=f"{rel}:{line}",
                    )
                )
        seen: set[tuple[str, str]] = set()
        local = functions.get(rel, {})
        for caller, form, name, qual, line in info.calls:
            target: str | None = None
            confidence = 0.8
            if form == "free":
                target = local.get(name) or imported.get(name)
            elif form == "self" and qual:
                found = [m for m in methods.get(f"{qual}.{name}", []) if m.split(":", 2)[1] == rel]
                found = found or methods.get(f"{qual}.{name}", [])
                target = found[0] if len(found) == 1 else None
            elif form == "path" and qual:
                found = methods.get(f"{qual}.{name}", [])
                if len(found) == 1:
                    target = found[0]
                elif not found:
                    module_file = modules.get(qual)
                    if module_file is None:
                        module_file, rest = crates.resolve(rel, [qual])
                        module_file = module_file if not rest else None
                    if module_file:
                        target = functions.get(module_file, {}).get(name)
            elif form == "dot":
                found = by_method.get(name, [])
                if len(found) == 1:
                    target, confidence = found[0], 0.5
            if target is None or target == caller or (caller, target) in seen:
                continue
            seen.add((caller, target))
            edges.append(
                GraphEdge(
                    source=caller,
                    target=target,
                    kind="calls",
                    origin="inferred",
                    confidence=confidence,
                    source_location=f"{rel}:{line}",
                )
            )
    return nodes, edges


def _file_node(rel: str, name: str, metadata: dict[str, object]) -> GraphNode:
    return GraphNode(node_id=f"file:{rel}", kind="file", name=name, path=rel, metadata=metadata)
