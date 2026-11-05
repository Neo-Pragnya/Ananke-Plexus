"""JavaScript and TypeScript in the code graph, read as text. No Node, nothing is run.

A linear scanner turns a file into tokens, so comments and strings can't create edges and every line number holds.
Declarations and calls are recognised by walking the tokens rather than by matching patterns over the source, which
also keeps the work proportional to the file's size whatever it contains.
"""

import contextlib
import json
import posixpath
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import NamedTuple

from ananke.plexus.graph.models import GraphEdge, GraphNode

JS_SUFFIXES = (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs")
JS_SKIP_DIRS = frozenset(
    {"dist", "build", "out", "vendor", "vendored", "third_party", "bower_components", "coverage"}
)
MAX_SOURCE_BYTES = 2_000_000
_MAX_CONFIG_BYTES = 1_000_000
_MINIFIED_MEAN_LINE = 300
_RESOLVE_ORDER = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")
_CONFIG_NAMES = ("tsconfig.json", "jsconfig.json")
_MAX_HOPS = (
    200  # visits per lookup: enough for a large barrel file, and it stops a pathological one
)
_MAX_NAMES = 128  # names to read from one destructuring pattern
_LOOKAHEAD = 64  # tokens to look through for a type annotation or a class heading

# Each pattern is one character class, or an unrolled loop whose parts can't match the same text.
_WORD = re.compile(r"#?(?:[^\W\d]|\$)[\w$]*")
_NUMBER = re.compile(r"\d[\w.]*")
_PUNCT = re.compile(r"=>|===|==|!==|!=|<=|>=|\?\.|\.\.\.|.", re.S)
_SINGLE = re.compile(r"'[^'\\\n]*(?:\\.[^'\\\n]*)*'", re.S)
_DOUBLE = re.compile(r'"[^"\\\n]*(?:\\.[^"\\\n]*)*"', re.S)
_TEMPLATE_STOP = re.compile(r"`|\\|\$\{")
_CONFIG_STOP = re.compile(r'"|//|/\*')
_CONFIG_STRING = re.compile(r'"[^"\\\n]*(?:\\.[^"\\\n]*)*"')
_TRAILING_COMMA = re.compile(r",(\s*[}\]])")

# After these a slash starts a regular expression; after any other word it is division.
_BEFORE_REGEX = {
    "return", "typeof", "case", "in", "of", "delete", "void", "throw", "new", "else", "do", "instanceof",
    "await", "yield",
}  # fmt: skip
_NOT_CALLS = {
    "if", "for", "while", "switch", "catch", "with", "return", "typeof", "function", "super", "await", "new",
    "delete", "void", "throw", "yield", "class", "import", "export", "in", "of", "instanceof", "else", "do",
    "try", "finally", "case", "default", "constructor", "this", "require", "extends",
}  # fmt: skip
_MODIFIERS = {
    "public", "private", "protected", "static", "async", "readonly", "abstract", "override", "get", "set",
    "declare", "accessor",
}  # fmt: skip
_STATEMENTS = {"export", "import", "const", "let", "var", "function", "class", "interface", "type"}
_DECLARATIONS = {"const", "let", "var"}
_OPENERS = {"(": ")", "[": "]", "{": "}"}
_CLOSERS = {")": "(", "]": "[", "}": "{"}


class Token(NamedTuple):
    kind: str  # word, number, string, template, regex or punct
    text: str
    line: int


def _regex_end(text: str, start: int) -> int:
    """Where the regular expression literal at ``start`` ends, or -1 if none closes on its line."""
    i = start + 1
    in_class = False
    size = len(text)
    while i < size:
        ch = text[i]
        if ch == "\n":
            return -1
        if ch == "\\":
            i += 2
            continue
        if ch == "[":
            in_class = True
        elif ch == "]":
            in_class = False
        elif ch == "/" and not in_class:
            return i + 1
        i += 1
    return -1


def _regex_allowed(tokens: list[Token]) -> bool:
    if not tokens:
        return True
    last = tokens[-1]
    if last.kind == "word":
        return last.text in _BEFORE_REGEX
    if last.kind != "punct":
        return False
    return last.text not in {")", "]", "}", "<"}  # ``<`` is a closing JSX tag, ``</div>``


def tokenize(text: str) -> list[Token]:
    """Comments vanish and strings become single tokens. Every step moves forward, so this is linear.

    Valid code never leaves a quote or regular expression open at the end of a line, so one that does is text, such
    as the apostrophe in JSX's ``<p>Don't</p>``. Remembering that per line keeps a line of them linear too.
    """
    tokens: list[Token] = []
    size = len(text)
    i = 0
    line = 1
    if text.startswith("#!"):
        i = text.find("\n")
        i = size if i < 0 else i
    braces = 0
    resume_at: list[int] = []  # brace depth at each open ``${`` of a template
    no_quote_until = {"'": -1, '"': -1}
    no_regex_until = -1
    in_template = False
    while i < size:
        if in_template:
            start = i
            while True:
                stop = _TEMPLATE_STOP.search(text, i)
                if stop is None:
                    i = size
                    break
                i = stop.end()
                if stop.group() == "\\":
                    i += 1
                    continue
                break
            line += text.count("\n", start, i)
            in_template = False
            if stop is None:
                break
            if stop.group() == "${":
                resume_at.append(braces)
            else:
                tokens.append(Token("template", "", line))
            continue
        ch = text[i]
        if ch.isspace():
            if ch == "\n":
                line += 1
            i += 1
            continue
        if ch == "/" and i + 1 < size:
            following = text[i + 1]
            if following == "/":
                end = text.find("\n", i)
                i = size if end < 0 else end
                continue
            if following == "*":
                end = text.find("*/", i + 2)
                end = size if end < 0 else end + 2
                line += text.count("\n", i, end)
                i = end
                continue
            if i > no_regex_until and _regex_allowed(tokens):
                end = _regex_end(text, i)
                if end > 0:
                    tokens.append(Token("regex", "", line))
                    i = end
                    continue
                newline = text.find("\n", i)
                no_regex_until = size if newline < 0 else newline
        if ch in no_quote_until:
            if i > no_quote_until[ch]:
                found = (_SINGLE if ch == "'" else _DOUBLE).match(text, i)
                if found:
                    tokens.append(Token("string", found.group()[1:-1], line))
                    line += text.count("\n", i, found.end())
                    i = found.end()
                    continue
                newline = text.find("\n", i)
                no_quote_until[ch] = size if newline < 0 else newline
            i += 1
            continue
        if ch == "`":
            if text.find("`", i + 1) < 0:
                i += 1  # text again: nothing can close it
                continue
            stop = _TEMPLATE_STOP.search(text, i + 1)
            probe = i + 1
            while stop is not None and stop.group() == "\\":
                probe = stop.end() + 1
                stop = _TEMPLATE_STOP.search(text, probe)
            if stop is not None and stop.group() == "`":
                tokens.append(Token("string", text[i + 1 : stop.start()], line))
                line += text.count("\n", i, stop.end())
                i = stop.end()
                continue
            tokens.append(Token("template", "", line))
            i += 1
            in_template = True
            continue
        if ch == "}" and resume_at and braces == resume_at[-1]:
            resume_at.pop()
            i += 1
            in_template = True
            continue
        word = _WORD.match(text, i)
        if word:
            tokens.append(Token("word", word.group(), line))
            i = word.end()
            continue
        number = _NUMBER.match(text, i)
        if number:
            tokens.append(Token("number", number.group(), line))
            i = number.end()
            continue
        text_end = _PUNCT.match(text, i)
        end = text_end.end() if text_end else i + 1  # ``.`` with DOTALL always matches
        if ch == "{":
            braces += 1
        elif ch == "}":
            braces -= 1
        tokens.append(Token("punct", text[i:end], line))
        i = end
    return tokens


def _matching(tokens: list[Token]) -> tuple[list[int], list[int]]:
    """For each bracket the index of its partner (both ways; the end of the file if unclosed), and each token's depth.

    A stray closer is ignored and an unclosed opener gives way to its enclosing pair, so broken code stays bounded.
    """
    size = len(tokens)
    match = [-1] * size
    depth = [0] * size
    stack: list[int] = []
    open_counts = {"(": 0, "[": 0, "{": 0}
    for i, token in enumerate(tokens):
        depth[i] = len(stack)
        if token.kind != "punct":
            continue
        if token.text in _OPENERS:
            stack.append(i)
            open_counts[token.text] += 1
        elif token.text in _CLOSERS:
            opener = _CLOSERS[token.text]
            if not open_counts[opener]:
                continue
            while stack:
                top = stack.pop()
                open_counts[tokens[top].text] -= 1
                if tokens[top].text == opener:
                    match[top], match[i] = i, top
                    depth[i] = len(stack)
                    break
                match[top] = i
    for top in stack:
        match[top] = size
    return match, depth


@dataclass
class _ImportRef:
    spec: str
    line: int


@dataclass
class _Parsed:
    rel: str
    language: str
    loc: int
    refs: list[_ImportRef] = field(default_factory=list)
    defs: dict[str, str] = field(default_factory=dict)
    methods: dict[str, dict[str, str]] = field(default_factory=dict)
    fields: dict[str, dict[str, str]] = field(
        default_factory=dict
    )  # class → field → declared class
    nodes: list[GraphNode] = field(default_factory=list)
    owns: list[GraphEdge] = field(default_factory=list)
    bindings: dict[str, tuple[str, str | None]] = field(
        default_factory=dict
    )  # local → (spec, name)
    exports: dict[str, str] = field(default_factory=dict)  # exported name → local name
    reexports: dict[str, tuple[str, str | None]] = field(
        default_factory=dict
    )  # name → (spec, name)
    stars: list[str] = field(default_factory=list)
    # (caller, chain as written, line)
    calls: list[tuple[str, tuple[str, ...], int]] = field(default_factory=list)
    bases: list[tuple[str, tuple[str, ...], int]] = field(default_factory=list)


class _Parser:
    """One file's symbols, imports and calls, from its tokens."""

    def __init__(self, rel: str, language: str, loc: int, tokens: list[Token]) -> None:
        self.out = _Parsed(rel, language, loc)
        self.rel = rel
        self.tokens = tokens
        self.size = len(tokens)
        self.match, self.depth = _matching(tokens)
        self.owner: list[str | None] = [None] * self.size
        self.heads: set[int] = set()  # names being declared, which look like calls but aren't
        self.required: set[str] = set()  # local names bound by require() or import()
        self.symbols: dict[str, GraphNode] = {}
        self.language = language
        self.not_code: list[bool] = self._type_declarations()

    def _type_declarations(self) -> list[bool]:
        """Tokens inside ``interface`` and ``type`` declarations, where ``get(id): T`` is a signature, not a call."""
        marked = [False] * self.size
        if self.language != "typescript":
            return marked
        i = 0
        while i < self.size:
            token = self.tokens[i]
            i += 1
            if token.kind != "word" or token.text not in {"interface", "type"}:
                continue
            if not self.is_word(i) or self.member_access(i - 1):
                continue
            j = i + 1
            limit = min(self.size, j + _LOOKAHEAD)
            while j < limit and self.text(j) not in {"{", ";", "("}:
                j += 1
            if self.punct(j, "{"):
                end = min(self.closing(j), self.size)
                marked[j:end] = [True] * (end - j)
                i = max(i, end)  # one walk over the tokens however these nest
        return marked

    def text(self, i: int) -> str:
        return self.tokens[i].text if 0 <= i < self.size else ""

    def is_word(self, i: int) -> bool:
        return 0 <= i < self.size and self.tokens[i].kind == "word"

    def is_string(self, i: int) -> bool:
        return 0 <= i < self.size and self.tokens[i].kind == "string"

    def punct(self, i: int, value: str) -> bool:
        return (
            0 <= i < self.size and self.tokens[i].kind == "punct" and self.tokens[i].text == value
        )

    def word(self, i: int, value: str) -> bool:
        return 0 <= i < self.size and self.tokens[i].kind == "word" and self.tokens[i].text == value

    def member_access(self, i: int) -> bool:
        return self.punct(i - 1, ".") or self.punct(i - 1, "?.")

    def closing(self, opener: int) -> int:
        close = self.match[opener]
        return self.size if close < 0 else close

    # ---------------------------------------------------------------- declarations
    def add_symbol(self, kind: str, name: str, start: int, end: int, cls: str | None = None) -> str:
        """A node and its ``owns`` edge. A repeated name (an overload) keeps the first node and widens its range."""
        full = f"{cls}.{name}" if cls else name
        node_id = f"{kind}:{self.rel}:{full}"
        line = self.tokens[start].line
        end_line = self.tokens[min(end, self.size - 1)].line
        out = self.out
        existing = self.symbols.get(node_id)
        if existing is not None:
            existing.metadata["end_line"] = end_line
            return node_id
        node = GraphNode(
            node_id=node_id,
            kind=kind,
            name=full,
            path=self.rel,
            metadata={"line": line, "end_line": end_line},
        )
        self.symbols[node_id] = node
        out.nodes.append(node)
        parent = f"class:{self.rel}:{cls}" if cls else f"file:{self.rel}"
        out.owns.append(
            GraphEdge(
                source=parent, target=node_id, kind="owns", source_location=f"{self.rel}:{line}"
            )
        )
        if cls:
            out.methods.setdefault(cls, {})[name] = node_id
        else:
            out.defs.setdefault(name, node_id)
        return node_id

    def paint(self, start: int, end: int, owner: str) -> None:
        stop = min(end, self.size)
        if start < stop:
            self.owner[start:stop] = [owner] * (stop - start)

    def body_after(self, close: int) -> int:
        """The ``{`` of a body following a parameter list, past any return type; -1 for a body-less declaration."""
        i = close + 1
        if not (self.punct(i, "{") or self.punct(i, ":")):
            return -1
        limit = min(self.size, i + _LOOKAHEAD)
        while i < limit:
            if self.punct(i, "{"):
                end = self.closing(i)
                # ``foo(): { a: string } {``: that first brace was the type
                if not self.punct(end + 1, "{"):
                    return i
                i = end + 1
                continue
            if self.punct(i, ";") or self.punct(i, "}") or self.punct(i, "=>"):
                return -1
            if self.text(i) in _OPENERS and self.tokens[i].kind == "punct":
                i = self.closing(i) + 1
                continue
            i += 1
        return -1

    def expression_end(self, start: int) -> int:
        """The token after an expression that has no brackets of its own to end it."""
        i = start
        while i < self.size:
            token = self.tokens[i]
            if token.kind == "punct":
                if token.text in {";", ","} or token.text in _CLOSERS:
                    return i
                if token.text in _OPENERS:
                    i = self.closing(i) + 1
                    continue
            elif token.kind == "word" and i > start and token.text in _STATEMENTS:
                return i
            i += 1
        return self.size

    def arrow_after(self, i: int) -> int:
        """If an arrow function's parameters or head start at ``i``, the index of its ``=>``; otherwise -1."""
        if self.is_word(i) and self.punct(i + 1, "=>"):
            return i + 1
        if self.punct(i, "<"):  # const f = <T,>(x: T) => x
            limit = min(self.size, i + _LOOKAHEAD)
            while i < limit and not self.punct(i, "("):
                i += 1
        if not self.punct(i, "("):
            return -1
        i = self.closing(i) + 1
        if self.punct(i, "=>"):
            return i
        if not self.punct(i, ":"):
            return -1
        limit = min(self.size, i + _LOOKAHEAD)
        while i < limit:
            if self.punct(i, "=>"):
                return i
            if self.punct(i, ";") or self.punct(i, "="):
                return -1
            if self.tokens[i].kind == "punct" and self.tokens[i].text in _OPENERS:
                i = self.closing(i)
            i += 1
        return -1

    def function_value(self, i: int) -> tuple[int, int] | None:
        """If a function starts at ``i``: ``(body start, end)`` as token indexes, end exclusive."""
        if self.word(i, "async"):
            i += 1
        if self.word(i, "function"):
            j = i + 1
            if self.punct(j, "*"):
                j += 1
            if self.is_word(j):
                j += 1
            if self.punct(j, "<"):
                limit = min(self.size, j + _LOOKAHEAD)
                while j < limit and not self.punct(j, "("):
                    j += 1
            if not self.punct(j, "("):
                return None
            body = self.body_after(self.closing(j))
            if body < 0:
                return None
            return body, self.closing(body) + 1
        arrow = self.arrow_after(i)
        if arrow < 0:
            return None
        if self.punct(arrow + 1, "{"):
            return arrow + 1, self.closing(arrow + 1) + 1
        return arrow + 1, self.expression_end(arrow + 1)

    def wrapped_function(self, i: int) -> tuple[int, int] | None:
        """``memo(() => …)`` or ``React.forwardRef(function …)``: the wrapper's whole argument list is the body."""
        j = i
        hops = 0
        while self.is_word(j) and self.punct(j + 1, ".") and hops < 8:
            j += 2
            hops += 1
        if not (self.is_word(j) and self.punct(j + 1, "(")):
            return None
        if self.function_value(j + 2) is None:
            return None
        return j + 1, self.closing(j + 1) + 1

    def declare_function(self, name: str, head: int, span: tuple[int, int]) -> int:
        end = span[1]
        symbol = self.add_symbol("function", name, head, end - 1)
        self.heads.add(head)
        self.paint(head, end, symbol)
        return end

    def read_function(self, i: int, exported_default: bool) -> int:
        j = i + 1
        if self.punct(j, "*"):
            j += 1
        named = self.is_word(j)
        name = self.text(j) if named else "default"
        if not named and not exported_default:
            return i + 1
        if named and exported_default:
            self.out.exports["default"] = name
        k = j + 1 if named else j
        if self.punct(k, "<"):
            limit = min(self.size, k + _LOOKAHEAD)
            while k < limit and not self.punct(k, "("):
                k += 1
        if not self.punct(k, "("):
            return i + 1
        self.heads.add(j)
        close = self.closing(k)
        body = self.body_after(close)
        if body < 0:  # an overload or ``declare``: the name exists, its body is elsewhere
            self.add_symbol("function", name, j if named else i, close)
            return close + 1
        end = self.closing(body) + 1
        symbol = self.add_symbol("function", name, j if named else i, end - 1)
        self.paint(k, end, symbol)
        return end

    def read_class(self, i: int, exported_default: bool, given_name: str = "") -> int:
        j = i + 1
        named = self.is_word(j) and self.text(j) not in {"extends", "implements"}
        name = given_name or (self.text(j) if named else "default")
        if not named and not exported_default and not given_name:
            return i + 1
        if exported_default and named:
            self.out.exports["default"] = name
        k = j + 1 if named else j
        base: tuple[str, ...] = ()
        limit = min(self.size, k + _LOOKAHEAD)
        while k < limit and not self.punct(k, "{"):
            if self.word(k, "extends") and self.is_word(k + 1):
                parts = [self.text(k + 1)]
                m = k + 2
                while self.punct(m, ".") and self.is_word(m + 1) and len(parts) < 16:
                    parts.append(self.text(m + 1))
                    m += 2
                base = tuple(parts)
                k = m
                continue
            if self.tokens[k].kind == "punct" and self.tokens[k].text in {"(", "["}:
                k = self.closing(k)
            k += 1
        if not self.punct(k, "{"):
            return i + 1
        end = self.closing(k) + 1
        class_id = self.add_symbol("class", name, j if named else i, end - 1)
        self.out.methods.setdefault(name, {})
        if base:
            self.out.bases.append((class_id, base, self.tokens[i].line))
        self.paint(k, end, class_id)
        self.read_class_body(k, end - 1, name)
        return end

    def read_class_body(self, opener: int, close: int, cls: str) -> None:
        i = opener + 1
        stop = min(close, self.size)
        while i < stop:
            token = self.tokens[i]
            if token.kind == "punct":
                if token.text == "@":  # a decorator is a call, not a member
                    i += 1
                    while self.is_word(i) and self.punct(i + 1, "."):
                        i += 2
                    i += 1
                    if self.punct(i, "("):
                        i = self.closing(i) + 1
                    continue
                i = self.closing(i) + 1 if token.text in _OPENERS else i + 1
                continue
            if token.kind != "word":
                i += 1
                continue
            following = i + 1
            if token.text in _MODIFIERS and (
                self.is_word(following) or self.punct(following, "*") or self.punct(following, "[")
            ):
                i += 1
                continue
            if self.punct(following, "?") or self.punct(following, "!"):
                following += 1
            if self.punct(following, "<"):
                limit = min(stop, following + _LOOKAHEAD)
                probe = following
                while probe < limit and not self.punct(probe, "("):
                    probe += 1
                if self.punct(probe, "("):
                    following = probe
            if self.punct(following, "("):
                params = self.closing(following)
                if token.text == "constructor":
                    self.read_constructor_fields(following, params, cls)
                body = self.body_after(params)
                self.heads.add(i)
                if body < 0 or body >= stop:
                    self.add_symbol("method", token.text, i, params, cls)
                    i = params + 1
                    continue
                end = self.closing(body) + 1
                symbol = self.add_symbol("method", token.text, i, end - 1, cls)
                self.paint(following, end, symbol)
                i = end
                continue
            # a property: ``name = () => …`` is a method, anything else is data
            j = following
            if self.punct(j, ":"):
                if self.is_word(j + 1):
                    self.out.fields.setdefault(cls, {})[token.text] = self.text(j + 1)
                j = self.after_annotation(j + 1)
            if self.punct(j, "="):
                if self.word(j + 1, "new") and self.is_word(j + 2):
                    self.out.fields.setdefault(cls, {})[token.text] = self.text(j + 2)
                elif self.word(j + 1, "inject") and self.is_word(j + 3):  # inject(ApiService)
                    self.out.fields.setdefault(cls, {})[token.text] = self.text(j + 3)
                span = self.function_value(j + 1)
                if span is not None:
                    symbol = self.add_symbol("method", token.text, i, span[1] - 1, cls)
                    self.paint(j + 1, span[1], symbol)
                    i = span[1]
                    continue
                i = self.expression_end(j + 1)
                continue
            i += 1

    def read_constructor_fields(self, opener: int, close: int, cls: str) -> None:
        """``constructor(private api: ApiService)`` declares a field of that type."""
        for i in range(opener + 1, min(close, self.size) - 2):
            if (
                self.text(i) in {"private", "public", "protected", "readonly"}
                and self.is_word(i + 1)
                and self.punct(i + 2, ":")
                and self.is_word(i + 3)
            ):
                self.out.fields.setdefault(cls, {})[self.text(i + 1)] = self.text(i + 3)

    def after_annotation(self, i: int) -> int:
        """The ``=``, ``,`` or ``;`` that ends a type annotation, or where the search gave up."""
        limit = min(self.size, i + _LOOKAHEAD)
        while i < limit:
            token = self.tokens[i]
            if token.kind == "punct":
                if token.text in {"=", ",", ";"}:
                    return i
                if token.text in _OPENERS:
                    i = self.closing(i) + 1
                    continue
            i += 1
        return i

    def read_declaration(self, i: int) -> int:
        """``const a = () => …, b = function …`` at the top level."""
        j = i + 1
        while self.is_word(j):
            name_at = j
            name = self.text(j)
            j += 1
            if self.punct(j, ":"):
                j = self.after_annotation(j + 1)
            if not self.punct(j, "="):
                return max(j, i + 1)
            value = j + 1
            if self.word(value, "class"):
                j = self.read_class(value, False, name)
            else:
                span = self.function_value(value) or self.wrapped_function(value)
                if span is not None:
                    j = self.declare_function(name, name_at, span)
                else:
                    j = self.expression_end(value)
            if not self.punct(j, ","):
                return max(j, i + 1)
            j += 1
        return max(j, i + 1)

    def read_default_export(self, i: int) -> int:
        """``export default`` at ``i``: a declaration, a function value, or a name."""
        j = i + 2
        if self.word(j, "abstract"):
            j += 1
        if self.word(j, "class"):
            return self.read_class(j, True)
        if self.word(j, "async") and self.word(j + 1, "function"):
            j += 1
        if self.word(j, "function"):
            return self.read_function(j, True)
        span = self.function_value(j)
        if span is not None:
            self.out.exports["default"] = "default"
            return self.declare_function("default", j, span)
        if self.is_word(j):
            if self.punct(j + 1, "(") and self.is_word(j + 2) and self.punct(j + 3, ")"):
                self.out.exports["default"] = self.text(j + 2)  # export default memo(Page)
                return j + 4
            if not self.punct(j + 1, "(") and not self.punct(j + 1, "."):
                self.out.exports["default"] = self.text(j)
        return j

    def read_commonjs_object(self, opener: int) -> None:
        """``module.exports = { a, b: c, d() {…}, e: () => … }``"""
        i = opener + 1
        close = self.closing(opener)
        while i < close:
            if not self.is_word(i):
                i = self.closing(i) + 1 if self.text(i) in _OPENERS else i + 1
                continue
            name = self.text(i)
            if self.punct(i + 1, ",") or i + 1 == close:
                self.out.exports[name] = name
                i += 2
            elif self.punct(i + 1, ":"):
                value = i + 2
                span = self.function_value(value)
                if span is not None:
                    i = self.declare_function(name, i, span)
                elif self.is_word(value) and (self.punct(value + 1, ",") or value + 1 == close):
                    self.out.exports[name] = self.text(value)
                    i = value + 2
                else:
                    i = self.expression_end(value)
            elif self.punct(i + 1, "("):
                body = self.body_after(self.closing(i + 1))
                if body < 0:
                    i = self.closing(i + 1) + 1
                    continue
                i = self.declare_function(name, i, (body, self.closing(body) + 1))
            else:
                i += 1

    def read_commonjs(self, i: int) -> int:
        """``exports.a = …``, ``module.exports.a = …`` or ``module.exports = …``"""
        if self.word(i, "module"):
            if not (self.punct(i + 1, ".") and self.word(i + 2, "exports")):
                return i + 1
            j = i + 3
        else:
            j = i + 1
        if self.punct(j, "=") and self.word(i, "module"):
            value = j + 1
            if self.punct(value, "{"):
                self.read_commonjs_object(value)
                return self.closing(value) + 1
            span = self.function_value(value)
            if span is not None:
                self.out.exports["default"] = "default"
                return self.declare_function("default", i, span)
            if self.is_word(value):
                self.out.exports["default"] = self.text(value)
            return value
        if self.punct(j, ".") and self.is_word(j + 1) and self.punct(j + 2, "="):
            name = self.text(j + 1)
            value = j + 3
            span = self.function_value(value)
            if span is not None:
                return self.declare_function(name, j + 1, span)
            if self.is_word(value):
                self.out.exports[name] = self.text(value)
            return value
        return i + 1

    def unwrap_immediate_functions(self) -> None:
        """``(function () { … })();`` wraps a whole file in much browser code, so what's inside counts as top level."""
        i = 0
        while i < self.size:
            if self.depth[i] != 0 or self.tokens[i].kind != "punct":
                i += 1
                continue
            start = i + 1 if self.punct(i, "(") else i + 1 if self.punct(i, "!") else -1
            span = self.function_value(start) if start > 0 else None
            if span is None or not self.punct(span[1] - 1, "}"):
                i += 1
                continue
            opener = self.match[span[1] - 1]
            if opener < 0:
                i += 1
                continue
            called = (
                self.punct(span[1], "(")  # (function () { … }()) or !function () { … }()
                or (
                    self.punct(span[1], ")") and self.punct(span[1] + 1, "(")
                )  # (function () { … })()
            )
            if called:
                base = self.depth[opener] + 1
                for k in range(opener + 1, span[1] - 1):
                    self.depth[k] -= base
                i = opener + 1  # one inside another unwraps too
                continue
            i += 1

    def assigned_function(self, i: int) -> tuple[int, int] | None:
        """``views.map = () => { … }``: functions kept on an object, as a browser script without modules does."""
        if self.punct(i + 1, ".") and self.is_word(i + 2) and self.punct(i + 3, "="):
            return self.function_value(i + 4)
        return None

    def read_declarations(self) -> None:
        self.unwrap_immediate_functions()
        i = 0
        while i < self.size:
            token = self.tokens[i]
            if token.kind != "word" or self.depth[i] != 0 or self.member_access(i):
                i += 1
                continue
            word = token.text
            if word == "function":
                i = self.read_function(i, False)
            elif word == "class":
                i = self.read_class(i, False)
            elif word in _DECLARATIONS:
                i = self.read_declaration(i)
            elif word == "export" and self.word(i + 1, "default"):
                i = self.read_default_export(i)
            elif word in {"module", "exports"}:
                i = self.read_commonjs(i)
            elif (assigned := self.assigned_function(i)) is not None:
                i = self.declare_function(f"{word}.{self.text(i + 2)}", i, assigned)
            else:
                i += 1

    # ---------------------------------------------------------------- imports
    def name_pairs(self, opener: int) -> list[tuple[str, str]]:
        """``{ a, b as c, type d, default as e }`` as ``(name, alias)`` pairs."""
        pairs: list[tuple[str, str]] = []
        i = opener + 1
        close = self.closing(opener)
        while i < close:
            if (
                self.word(i, "type")
                and (self.is_word(i + 1) or self.is_string(i + 1))
                and not self.word(i + 1, "as")
            ):
                i += 1
            if not (self.is_word(i) or self.is_string(i)):
                if not self.punct(i, ","):
                    break  # not a list of names, so don't walk the rest of the file
                i += 1
                continue
            name = self.text(i)
            alias = name
            if self.word(i + 1, "as") and (self.is_word(i + 2) or self.is_string(i + 2)):
                alias = self.text(i + 2)
                i += 2
            pairs.append((name, alias))
            i += 1
        return pairs

    def read_import(self, i: int) -> None:
        if self.punct(i + 1, "("):  # import("./x")
            if self.is_string(i + 2) and (self.punct(i + 3, ")") or self.punct(i + 3, ",")):
                self.out.refs.append(_ImportRef(self.text(i + 2), self.tokens[i].line))
                self.bind_loaded(i, self.closing(i + 1), self.text(i + 2))
            return
        if self.punct(i + 1, "."):  # import.meta
            return
        if self.is_string(i + 1):
            self.out.refs.append(_ImportRef(self.text(i + 1), self.tokens[i].line))
            return
        j = i + 1
        if (
            (self.word(j, "type") or self.word(j, "typeof"))
            and not (self.word(j + 1, "from") and self.is_string(j + 2))
            and not self.punct(j + 1, ",")
        ):
            j += 1
        default = ""
        namespace = ""
        pairs: list[tuple[str, str]] = []
        if self.is_word(j) and not self.word(j, "from"):
            default = self.text(j)
            j += 1
            if self.punct(j, "=") and self.word(j + 1, "require") and self.is_string(j + 3):
                self.out.refs.append(_ImportRef(self.text(j + 3), self.tokens[i].line))
                self.out.bindings[default] = (self.text(j + 3), None)
                return
            if self.punct(j, ","):
                j += 1
        if self.punct(j, "*") and self.word(j + 1, "as") and self.is_word(j + 2):
            namespace = self.text(j + 2)
            j += 3
        elif self.punct(j, "{"):
            pairs = self.name_pairs(j)
            j = self.closing(j) + 1
        if not (self.word(j, "from") and self.is_string(j + 1)):
            return
        spec = self.text(j + 1)
        self.out.refs.append(_ImportRef(spec, self.tokens[i].line))
        if default:
            self.out.bindings[default] = (spec, "default")
        if namespace:
            self.out.bindings[namespace] = (spec, None)
        for name, alias in pairs:
            self.out.bindings[alias] = (spec, name)

    def read_export_from(self, i: int) -> None:
        j = i + 1
        if self.word(j, "type") and (self.punct(j + 1, "{") or self.punct(j + 1, "*")):
            j += 1
        if self.punct(j, "*"):
            if self.word(j + 1, "as") and self.is_word(j + 2):
                name = self.text(j + 2)
                if self.word(j + 3, "from") and self.is_string(j + 4):
                    spec = self.text(j + 4)
                    self.out.refs.append(_ImportRef(spec, self.tokens[i].line))
                    self.out.reexports[name] = (spec, None)
            elif self.word(j + 1, "from") and self.is_string(j + 2):
                spec = self.text(j + 2)
                self.out.refs.append(_ImportRef(spec, self.tokens[i].line))
                self.out.stars.append(spec)
            return
        if not self.punct(j, "{"):
            return
        pairs = self.name_pairs(j)
        after = self.closing(j) + 1
        if self.word(after, "from") and self.is_string(after + 1):
            spec = self.text(after + 1)
            self.out.refs.append(_ImportRef(spec, self.tokens[i].line))
            for name, alias in pairs:
                self.out.reexports[alias] = (spec, name)
        else:
            for name, alias in pairs:
                self.out.exports[alias] = name

    def bind_loaded(self, start: int, close: int, spec: str) -> None:
        """``const m = require('x')``, ``const { a, b: c } = await import('x')`` or ``const a = require('x').a``."""
        i = start - 1
        if self.word(i, "await"):
            i -= 1
        if not self.punct(i, "="):
            return
        picked: str | None = None
        if self.punct(close + 1, ".") and self.is_word(close + 2):
            picked = self.text(close + 2)
        before = i - 1
        if self.is_word(before) and self.text(before - 1) in _DECLARATIONS:
            local = self.text(before)
            self.out.bindings.setdefault(local, (spec, picked))
            self.required.add(local)
        elif self.punct(before, "}") and self.match[before] >= 0:
            opener = self.match[before]
            if self.text(opener - 1) not in _DECLARATIONS:
                return
            j = opener + 1
            while j < min(before, opener + _MAX_NAMES):
                if self.is_word(j):
                    name = self.text(j)
                    alias = name
                    if self.punct(j + 1, ":") and self.is_word(j + 2):
                        alias = self.text(j + 2)
                        j += 2
                    self.out.bindings.setdefault(alias, (spec, name))
                    self.required.add(alias)
                j += 1

    def read_imports(self) -> None:
        for i, token in enumerate(self.tokens):
            if token.kind != "word" or self.member_access(i):
                continue
            if token.text == "import":
                self.read_import(i)
            elif token.text == "export":
                self.read_export_from(i)
            elif (
                token.text == "require"
                and self.punct(i + 1, "(")
                and self.is_string(i + 2)
                and self.punct(i + 3, ")")
                and not self.word(i - 1, "function")
            ):
                self.out.refs.append(_ImportRef(self.text(i + 2), token.line))
                self.bind_loaded(i, i + 3, self.text(i + 2))

    # ---------------------------------------------------------------- calls
    def local_names(self, start: int, end: int) -> set[str]:
        """Names declared inside a body or as its parameters, which hide an import of the same name."""
        names: set[str] = set()
        stop = min(end, self.size)
        i = start
        while i < min(start + 8, stop) and not self.punct(i, "("):
            i += 1
        if self.punct(i, "("):  # the head: a word starting a parameter
            for k in range(i + 1, min(self.closing(i), stop)):
                if self.is_word(k) and self.text(k - 1) in {"(", ",", "...", "{", "["}:
                    names.add(self.text(k))
        i = start
        while i < stop:
            token = self.tokens[i]
            if token.kind == "word" and token.text in {*_DECLARATIONS, "function"}:
                following = i + 1
                if self.is_word(following):
                    names.add(self.text(following))
                elif self.punct(following, "{") or self.punct(following, "["):
                    pattern_end = min(self.closing(following), stop, following + _MAX_NAMES)
                    for k in range(following + 1, pattern_end):
                        if self.is_word(k) and not self.punct(k + 1, ":"):
                            names.add(self.text(k))
            i += 1
        return names - self.required

    def call_name_at(self, i: int) -> bool:
        """Whether the word at ``i`` is being called."""
        following = i + 1
        if self.punct(following, "?."):
            following += 1
        if self.punct(following, "("):
            return True
        if self.language == "typescript" and self.punct(following, "<"):  # fetch<Order>(url)
            limit = min(self.size, following + 16)
            j = following + 1
            while j < limit:
                if self.punct(j, ">"):
                    return self.punct(j + 1, "(")
                if self.text(j) in {";", "{", "}", "(", ")", "=", "=>"}:
                    return False
                j += 1
        return False

    def read_calls(self) -> None:
        file_id = f"file:{self.rel}"
        locals_by_owner: dict[str, set[str]] = {}
        ranges: dict[str, tuple[int, int]] = {}
        for i, owner in enumerate(self.owner):
            if owner is not None:
                first, _ = ranges.get(owner, (i, i))
                ranges[owner] = (first, i + 1)
        for i, token in enumerate(self.tokens):
            if token.kind != "word" or token.text in _NOT_CALLS or i in self.heads:
                continue
            if self.not_code[i]:
                continue
            if not self.call_name_at(i):
                continue
            if (
                self.punct(i + 1, "(")
                and self.punct(self.closing(i + 1) + 1, "{")
                and not self.member_access(i)
            ):
                continue  # name(args) { … } can only be a method definition
            if self.word(i - 1, "function") or (
                self.punct(i - 1, "*") and self.word(i - 2, "function")
            ):
                continue
            chain = [token.text]
            j = i
            while self.member_access(j) and self.is_word(j - 2) and len(chain) < 16:
                chain.append(self.text(j - 2))
                j -= 2
            if self.member_access(j):  # foo().bar(): what foo returns isn't known
                continue
            chain.reverse()
            owner = self.owner[i]
            if owner is not None and chain[0] != "this":
                if owner not in locals_by_owner:
                    locals_by_owner[owner] = self.local_names(*ranges[owner])
                if chain[0] in locals_by_owner[owner]:
                    continue
            self.out.calls.append((owner or file_id, tuple(chain), token.line))

    def parse(self) -> _Parsed:
        self.read_declarations()
        self.read_imports()
        self.read_calls()
        return self.out


def is_minified(name: str, text: str) -> bool:
    if ".min." in name or name.endswith("-min.js"):
        return True
    lines = text.count("\n") + 1
    return len(text) > 5000 and len(text) / lines > _MINIFIED_MEAN_LINE


def _language(rel: str) -> str:
    return "typescript" if rel.endswith((".ts", ".tsx")) else "javascript"


def parse_file(rel: str, text: str) -> _Parsed:
    """One file's symbols, imports and calls, from its text alone."""
    loc = text.count("\n") + (0 if text.endswith("\n") or not text else 1)
    return _Parser(rel, _language(rel), loc, tokenize(text)).parse()


def strip_jsonc(text: str) -> str:
    """JSON with comments and trailing commas, as ``tsconfig.json`` allows, as plain JSON."""
    kept: list[str] = []
    i = 0
    while True:
        found = _CONFIG_STOP.search(text, i)
        if found is None:
            kept.append(text[i:])
            break
        kept.append(text[i : found.start()])
        i = found.end()
        marker = found.group()
        if marker == "//":
            end = text.find("\n", i)
            i = len(text) if end < 0 else end
        elif marker == "/*":
            end = text.find("*/", i)
            i = len(text) if end < 0 else end + 2
        else:
            string = _CONFIG_STRING.match(text, found.start())
            if string is None:  # unterminated, so the line is broken: keep it and move past it
                end = text.find("\n", i)
                i = len(text) if end < 0 else end
                kept.append(text[found.start() : i])
                continue
            kept.append(string.group())
            i = string.end()
    return _TRAILING_COMMA.sub(r"\1", "".join(kept))


@dataclass(frozen=True)
class _PathRule:
    prefix: str
    suffix: str
    wildcard: bool
    targets: tuple[str, ...]  # relative to the repository root, ``*`` still in place


@dataclass(frozen=True)
class _Aliases:
    rules: tuple[_PathRule, ...] = ()
    base_url: str | None = None


class ModuleResolver:
    """Turns an import specifier into a repository file: relative paths, then ``paths`` and ``baseUrl`` aliases."""

    def __init__(self, repository_root: Path, files: Iterable[str]) -> None:
        self.root = repository_root
        self.files = set(files)
        self._aliases: dict[str, _Aliases] = {}

    def _file_for(self, path: str) -> str:
        """Exact file, then extensions, then ``/index``. ``./x.js`` in TypeScript may mean ``x.ts``."""
        path = posixpath.normpath(path)
        if path == ".." or path.startswith("../"):
            return ""
        if path in self.files:
            return path
        for suffix in _RESOLVE_ORDER:
            if f"{path}{suffix}" in self.files:
                return f"{path}{suffix}"
        stem, ext = posixpath.splitext(path)
        if ext in {".js", ".jsx", ".mjs", ".cjs"}:
            for suffix in (".ts", ".tsx"):
                if f"{stem}{suffix}" in self.files:
                    return f"{stem}{suffix}"
        for suffix in _RESOLVE_ORDER:
            candidate = posixpath.normpath(f"{path}/index{suffix}")
            if candidate in self.files:
                return candidate
        return ""

    def _read_config(self, rel: str, seen: set[str]) -> dict[str, object] | None:
        """``compilerOptions`` with ``paths`` made relative to the repository, following local ``extends``."""
        path = self.root / rel
        if rel in seen or len(seen) > 8 or rel == ".." or rel.startswith("../"):
            return None
        seen.add(rel)
        try:
            if path.is_symlink() or not path.is_file() or path.stat().st_size > _MAX_CONFIG_BYTES:
                return None
            data = json.loads(strip_jsonc(path.read_text(encoding="utf-8")))
        except (OSError, ValueError, RecursionError):
            return None
        if not isinstance(data, dict):
            return None
        here = posixpath.dirname(rel)
        merged: dict[str, object] = {}
        parents = data.get("extends")
        for parent in parents if isinstance(parents, list) else [parents]:
            if isinstance(parent, str) and parent.startswith("."):
                target = posixpath.normpath(posixpath.join(here, parent))
                target = target if target.endswith(".json") else f"{target}.json"
                merged.update(self._read_config(target, seen) or {})
        options = data.get("compilerOptions")
        if isinstance(options, dict):
            base = options.get("baseUrl")
            if isinstance(base, str):
                merged["baseUrl"] = posixpath.normpath(posixpath.join(here, base))
            paths = options.get("paths")
            if isinstance(paths, dict):
                # ``paths`` are relative to ``baseUrl`` if there is one, or else to this config
                merged["paths"] = paths
                merged["pathsBase"] = merged.get("baseUrl", here or ".")
        return merged

    def _load_aliases(self, directory: str) -> _Aliases:
        names = list(_CONFIG_NAMES)
        folder = self.root / directory if directory else self.root
        # A Vite project's tsconfig.json often just points at tsconfig.app.json, which holds the paths.
        with contextlib.suppress(OSError):
            names += sorted(p.name for p in folder.glob("tsconfig.*.json"))
        options: dict[str, object] = {}
        for name in names:
            found = self._read_config(posixpath.join(directory, name), set())
            if found and ("paths" in found or "baseUrl" in found):
                options = found
                break
        paths = options.get("paths")
        base = options.get("pathsBase")
        rules: list[_PathRule] = []
        if isinstance(paths, dict) and isinstance(base, str):
            for pattern, targets in paths.items():
                if not (isinstance(pattern, str) and isinstance(targets, list)):
                    continue
                prefix, star, suffix = pattern.partition("*")
                joined = tuple(posixpath.join(base, t) for t in targets if isinstance(t, str))
                rules.append(_PathRule(prefix, suffix, bool(star), joined))
        rules.sort(key=lambda r: (-len(r.prefix), r.prefix))
        base_url = options.get("baseUrl")
        return _Aliases(tuple(rules), base_url if isinstance(base_url, str) else None)

    def _aliases_for(self, importer: str) -> list[_Aliases]:
        """Alias settings from the importer's folder upward, nearest first."""
        found: list[_Aliases] = []
        directory = posixpath.dirname(importer)
        while True:
            if directory not in self._aliases:
                self._aliases[directory] = self._load_aliases(directory)
            aliases = self._aliases[directory]
            if aliases.rules or aliases.base_url is not None:
                found.append(aliases)
            if not directory:
                return found
            directory = posixpath.dirname(directory)

    def resolve(self, importer: str, spec: str) -> str:
        if spec.startswith("."):
            return self._file_for(posixpath.join(posixpath.dirname(importer), spec))
        if spec.startswith("/") or "://" in spec or spec.startswith("node:"):
            return ""
        for aliases in self._aliases_for(importer):
            for rule in aliases.rules:
                if rule.wildcard:
                    if not (
                        spec.startswith(rule.prefix)
                        and spec.endswith(rule.suffix)
                        and len(spec) >= len(rule.prefix) + len(rule.suffix)
                    ):
                        continue
                    middle = spec[len(rule.prefix) : len(spec) - len(rule.suffix)]
                elif spec != rule.prefix:
                    continue
                else:
                    middle = ""
                for target in rule.targets:
                    file = self._file_for(target.replace("*", middle, 1))
                    if file:
                        return file
            if aliases.base_url is not None:
                file = self._file_for(posixpath.join(aliases.base_url, spec))
                if file:
                    return file
        return ""


def external_name(spec: str) -> str:
    """The package an import names: ``lodash/debounce`` → ``lodash``, ``@a/b/c`` → ``@a/b``."""
    if spec.startswith((".", "/")) or ":" in spec:
        return spec
    parts = spec.split("/")
    return "/".join(parts[:2]) if spec.startswith("@") else parts[0]


class _Linker:
    """Follows names across files, through imports and re-exports, to the definition they mean."""

    def __init__(self, parsed: dict[str, _Parsed], resolver: ModuleResolver) -> None:
        self.parsed = parsed
        self.files: dict[tuple[str, str], str] = {}
        self.resolver = resolver

    def file_of(self, importer: str, spec: str) -> str:
        key = (importer, spec)
        if key not in self.files:
            self.files[key] = self.resolver.resolve(importer, spec)
        return self.files[key]

    def local(self, rel: str, name: str, seen: set[tuple[str, str]]) -> str | None:
        info = self.parsed.get(rel)
        if info is None:
            return None
        own = info.defs.get(name)
        if own:
            return own
        bound = info.bindings.get(name)
        if bound is None:
            return None
        spec, member = bound
        target = self.file_of(rel, spec)
        if not target:
            return None
        if member is None:
            return f"file:{target}"
        found = self.member(target, member, seen)
        if found is None and member == "default":
            return f"file:{target}"  # a CommonJS module imported as a default: its exports are its members
        return found

    def member(self, rel: str, name: str, seen: set[tuple[str, str]]) -> str | None:
        if (rel, name) in seen or len(seen) >= _MAX_HOPS:
            return None
        seen.add((rel, name))
        info = self.parsed.get(rel)
        if info is None:
            return None
        alias = info.exports.get(name)
        if alias:
            found = self.local(rel, alias, seen)
            if found:
                return found
        again = info.reexports.get(name)
        if again:
            target = self.file_of(rel, again[0])
            if target:
                return f"file:{target}" if again[1] is None else self.member(target, again[1], seen)
        found = self.local(rel, name, seen)
        if found:
            return found
        if name != "default":
            for spec in info.stars:
                target = self.file_of(rel, spec)
                found = self.member(target, name, seen) if target else None
                if found:
                    return found
        return None

    def resolve(self, rel: str, chain: tuple[str, ...], caller: str) -> str | None:
        if chain[0] == "this":
            kind, _, tail = caller.partition(":")
            if kind not in {"method", "class"} or len(chain) not in {2, 3}:
                return None
            cls = tail.partition(":")[2].partition(".")[0]
            info = self.parsed[rel]
            if len(chain) == 2:
                return info.methods.get(cls, {}).get(chain[1])
            # this.api.send(): the field's declared class says which send
            declared = info.fields.get(cls, {}).get(chain[1])
            found = self.local(rel, declared, set()) if declared else None
            if not found or not found.startswith("class:"):
                return None
            cls_file, _, cls_name = found.partition(":")[2].partition(":")
            other = self.parsed.get(cls_file)
            return other.methods.get(cls_name, {}).get(chain[2]) if other else None
        current = self.local(rel, chain[0], set())
        for attr in chain[1:]:
            if current is None:
                return None
            kind, _, tail = current.partition(":")
            if kind == "file":
                current = self.member(tail, attr, set())
            elif kind == "class":
                cls_file, _, cls_name = tail.partition(":")
                owner = self.parsed.get(cls_file)
                current = owner.methods.get(cls_name, {}).get(attr) if owner else None
            else:
                return None
        return None if current is None or current.startswith("file:") else current


def build_javascript(
    repository_root: Path, paths: list[Path]
) -> tuple[list[GraphNode], list[GraphEdge]]:
    """Nodes and edges for these files. ``.d.ts``, minified and oversized files are left out; broken ones are listed."""
    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []
    parsed: dict[str, _Parsed] = {}
    for path in paths:
        rel = str(path.relative_to(repository_root))
        name = path.name
        if name.endswith(".d.ts") or path.is_symlink():
            continue
        try:
            if path.stat().st_size > MAX_SOURCE_BYTES:
                continue
            text = path.read_text(encoding="utf-8")
        except (OSError, ValueError):
            nodes.append(_file_node(rel, name, {"language": _language(rel), "parse_error": True}))
            continue
        if is_minified(name, text):
            continue
        try:
            info = parse_file(rel, text)
        except Exception:  # whatever a file holds, the rest of the repository still gets mapped
            nodes.append(_file_node(rel, name, {"language": _language(rel), "parse_error": True}))
            continue
        parsed[rel] = info
        nodes.append(_file_node(rel, name, {"language": info.language, "loc": info.loc}))
        nodes += info.nodes
        edges += info.owns

    known = {node.path for node in nodes}
    resolver = ModuleResolver(repository_root, known)
    linker = _Linker(parsed, resolver)
    for rel, info in parsed.items():
        source = f"file:{rel}"
        written: set[tuple[str, str, int]] = set()
        for ref in info.refs:
            file = linker.file_of(rel, ref.spec)
            if file == rel:
                continue
            target = f"module:{ref.spec}" if file or ref.spec.startswith(".") else ""
            target = target or f"module:{external_name(ref.spec)}"
            if (target, file, ref.line) in written:
                continue
            written.add((target, file, ref.line))
            edges.append(
                GraphEdge(
                    source=source,
                    target=target,
                    kind="imports",
                    source_location=f"{rel}:{ref.line}",
                    resolved_target=f"file:{file}" if file else "",
                )
            )
        seen: set[tuple[str, str, str]] = set()
        links = [("calls", row) for row in info.calls] + [("inherits", row) for row in info.bases]
        for kind, (caller, chain, line) in links:
            resolved = linker.resolve(rel, chain, caller)
            if resolved is None or resolved == caller or (caller, resolved, kind) in seen:
                continue
            seen.add((caller, resolved, kind))
            edges.append(
                GraphEdge(
                    source=caller,
                    target=resolved,
                    kind=kind,
                    origin="inferred",
                    confidence=0.8,
                    source_location=f"{rel}:{line}",
                )
            )
    return nodes, edges


def _file_node(rel: str, name: str, metadata: dict[str, object]) -> GraphNode:
    return GraphNode(node_id=f"file:{rel}", kind="file", name=name, path=rel, metadata=metadata)
