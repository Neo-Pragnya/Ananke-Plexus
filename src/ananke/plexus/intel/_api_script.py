"""Routes and HTTP clients in JavaScript and TypeScript: Express, NestJS, ``fetch``, axios and Angular's ``HttpClient``.

Nothing is run. The scanner follows ``graph.javascript``'s, so comments and strings can't create a route and every line number
holds, and it also keeps the text of template literals, which is where most URLs are. Each step moves forward and looks a bounded
distance, so the work stays proportional to a file's size.
"""

from __future__ import annotations

import bisect
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from ananke.plexus.graph.javascript import (
    _DOUBLE,
    _NUMBER,
    _PUNCT,
    _SINGLE,
    _TEMPLATE_STOP,
    _WORD,
    ModuleResolver,
    Token,
    _Linker,
    _matching,
    _Parsed,
    _regex_allowed,
    _regex_end,
    is_minified,
    parse_file,
)
from ananke.plexus.intel._api_common import (
    HTTP_VERBS,
    MAX_CLIENTS,
    MAX_ROUTES,
    Mount,
    Param,
    Parts,
    RawClient,
    RawRoute,
    Server,
    colon_params,
    expand_prefixes,
    is_catch_all,
    join_paths,
    merged,
    only_text,
)
from ananke.plexus.intel._common import read_text
from ananke.plexus.intel.context import RepoContext

_DECLARATIONS = {"const", "let", "var"}
_FIELD_MODIFIERS = {"private", "public", "protected", "readonly", "static", "override"}
_STATEMENTS = {
    "export", "import", "const", "let", "var", "function", "class", "interface", "type", "return", "if", "for",
    "while", "switch", "try", "throw",
}  # fmt: skip
_MAX_EXPRESSION = 96  # tokens one value can take: enough for a URL, and it stops a pathological one
_MAX_ARGS = 32
_MAX_LOOKAHEAD = 48
_MAX_EVAL_DEPTH = 8
_MAX_EVAL_STEPS = 256
_MAX_CHAIN = 8
_MAX_TEXT = 2048
_MAX_KEYS = 512
_INTERESTING = {
    "fetch", *HTTP_VERBS, "request", "use", "route", "all", "setGlobalPrefix",
}  # fmt: skip
_GLOBALS = {"window", "globalThis", "self", "global"}
_NEST_VERBS = {
    "Get": "GET", "Post": "POST", "Put": "PUT", "Patch": "PATCH", "Delete": "DELETE", "Head": "HEAD",
    "Options": "OPTIONS", "All": "ANY",
}  # fmt: skip
_URL_WRAPPERS = {"encodeURIComponent", "encodeURI", "String"}


def scan(text: str) -> list[Token]:
    """Tokens as ``graph.javascript.tokenize`` gives them, with templates kept.

    A template is ``template``, then ``quasi`` text and ``(``...``)`` around each substitution, then ``template_end``. A ``regex``
    token keeps its text. Quotes and regular expressions that don't close on their line are text, remembered per line, so a line
    full of them stays linear.
    """
    tokens: list[Token] = []
    size = len(text)
    i = 0
    line = 1
    if text.startswith("#!"):
        i = text.find("\n")
        i = size if i < 0 else i
    braces = 0
    resume_at: list[int] = []
    no_quote_until = {"'": -1, '"': -1}
    no_regex_until = -1
    in_template = False
    pieces: list[str] = []
    while i < size:
        if in_template:
            start = i
            pieces = []
            while True:
                stop = _TEMPLATE_STOP.search(text, i)
                if stop is None:
                    pieces.append(text[i:])
                    i = size
                    break
                pieces.append(text[i : stop.start()])
                i = stop.end()
                if stop.group() == "\\":
                    pieces.append(text[i : i + 1])
                    i += 1
                    continue
                break
            line_at = line
            line += text.count("\n", start, i)
            tokens.append(Token("quasi", "".join(pieces), line_at))
            in_template = False
            if stop is None:
                tokens.append(Token("template_end", "", line))
                break
            if stop.group() == "${":
                resume_at.append(braces)
                tokens.append(Token("punct", "(", line))
            else:
                tokens.append(Token("template_end", "", line))
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
                    tokens.append(Token("regex", text[i : min(end, i + 256)], line))
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
            tokens.append(Token("template", "", line))
            i += 1
            in_template = True
            continue
        if ch == "}" and resume_at and braces == resume_at[-1]:
            resume_at.pop()
            tokens.append(Token("punct", ")", line))
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
        matched = _PUNCT.match(text, i)
        end = matched.end() if matched else i + 1
        if ch == "{":
            braces += 1
        elif ch == "}":
            braces -= 1
        tokens.append(Token("punct", text[i:end], line))
        i = end
    return tokens


@dataclass
class _Instance:
    framework: str  # axios or angular
    base: tuple[int, int] | None = None
    script: _Script | None = None


@dataclass
class _Script:
    rel: str
    tokens: list[Token]
    match: list[int]
    depth: list[int]
    parsed: _Parsed
    decls: dict[str, list[tuple[int, int, int]]] = field(default_factory=dict)  # name → (at, start, end)
    fields: dict[str, tuple[int, int]] = field(default_factory=dict)
    instances: dict[str, _Instance] = field(default_factory=dict)
    servers: set[str] = field(default_factory=set)
    http_fields: set[str] = field(default_factory=set)
    size: int = 0

    def __post_init__(self) -> None:
        self.size = len(self.tokens)

    def text(self, i: int) -> str:
        return self.tokens[i].text if 0 <= i < self.size else ""

    def kind(self, i: int) -> str:
        return self.tokens[i].kind if 0 <= i < self.size else ""

    def punct(self, i: int, value: str) -> bool:
        return self.kind(i) == "punct" and self.tokens[i].text == value

    def word(self, i: int, value: str | None = None) -> bool:
        return self.kind(i) == "word" and (value is None or self.tokens[i].text == value)

    def closing(self, opener: int) -> int:
        close = self.match[opener] if 0 <= opener < self.size else -1
        return self.size if close < 0 else close

    def bound_to(self, name: str) -> tuple[str, str | None] | None:
        return self.parsed.bindings.get(name)

    def spec_of(self, name: str) -> str:
        bound = self.parsed.bindings.get(name)
        return bound[0] if bound else ""

    def expression_end(self, start: int) -> int:
        i = start
        taken = 0
        while i < self.size and taken < _MAX_EXPRESSION:
            taken += 1
            token = self.tokens[i]
            if token.kind == "punct":
                if token.text in {"(", "[", "{"}:
                    i = self.closing(i) + 1
                    continue
                if token.text in {";", ",", ")", "]", "}"}:
                    return i
            elif (
                token.kind == "word"
                and token.text in _STATEMENTS
                and i > start
                and token.line != self.tokens[i - 1].line
            ):
                return i
            i += 1
        return min(i, self.size)

    def arguments(self, opener: int) -> list[tuple[int, int]]:
        """The token ranges of a call's arguments, up to a limit."""
        close = self.closing(opener)
        out: list[tuple[int, int]] = []
        start = i = opener + 1
        taken = 0
        while i < close and len(out) < _MAX_ARGS and taken < _MAX_EXPRESSION * 8:
            taken += 1
            if self.kind(i) == "punct":
                if self.tokens[i].text in {"(", "[", "{"}:
                    i = self.closing(i) + 1
                    continue
                if self.tokens[i].text == ",":
                    out.append((start, i))
                    start = i + 1
            i += 1
        if start < close and len(out) < _MAX_ARGS:
            out.append((start, min(i, close)))
        return out

    def key_value(self, opener: int, wanted: str) -> tuple[int, int] | None:
        """The value of a key at the top of an object literal."""
        close = self.closing(opener)
        i = opener + 1
        taken = 0
        while i < close and taken < _MAX_KEYS:
            taken += 1
            if self.kind(i) == "punct" and self.tokens[i].text in {"(", "[", "{"}:
                i = self.closing(i) + 1
                continue
            if (
                self.kind(i) in {"word", "string"}
                and self.text(i) == wanted
                and self.punct(i + 1, ":")
            ):
                return i + 2, self.expression_end(i + 2)
            i += 1
        return None


class ScriptReader:
    def __init__(self, ctx: RepoContext, paths: Iterable[str], known_files: Iterable[str]) -> None:
        self.ctx = ctx
        self.paths = list(paths)
        self.scripts: dict[str, _Script] = {}
        self.resolver = ModuleResolver(ctx.root, set(known_files))
        self.limits: set[str] = set()
        self.routes: list[RawRoute] = []
        self.raw_clients: list[RawClient] = []
        self.owner_of: dict[str, object] = {}
        self.servers: dict[tuple[str, str], Server] = {}
        self.mounts: list[Mount] = []
        self.pending: list[tuple[tuple[str, str], RawRoute]] = []
        self.nest: list[RawRoute] = []
        self.nest_prefixes: list[tuple[str, str]] = []
        self.steps = 0
        self.computed_routes = 0
        self.unparsed: list[str] = []
        self.linker: _Linker | None = None
        self.uses_express = False

    # ------------------------------------------------------------ loading

    def load(self) -> None:
        for rel in self.paths:
            text = read_text(self.ctx, rel)
            if text is None or is_minified(Path(rel).name, text) or rel.endswith(".d.ts"):
                continue
            try:
                tokens = scan(text)
                match, depth = _matching(tokens)
                script = _Script(rel, tokens, match, depth, parse_file(rel, text))
            except Exception:  # whatever a file holds, the rest of the repository still gets read
                self.unparsed.append(rel)
                continue
            self.scripts[rel] = script
            if any(spec == "express" for spec, _ in script.parsed.bindings.values()):
                self.uses_express = True
        self.linker = _Linker({rel: s.parsed for rel, s in self.scripts.items()}, self.resolver)
        for script in self.scripts.values():
            self._collect(script)

    def _collect(self, script: _Script) -> None:
        """A first walk: what names hold, before any call is read."""
        for i, token in enumerate(script.tokens):
            if token.kind != "word":
                continue
            word = token.text
            if word in _DECLARATIONS and script.word(i + 1):
                self._declaration(script, i)
            elif word == "this" and script.punct(i + 1, ".") and script.word(i + 2):
                if script.punct(i + 3, "="):
                    self._field(script, script.text(i + 2), i + 4)
            elif word == "HttpClient" and script.punct(i - 1, ":") and script.word(i - 2):
                if script.spec_of("HttpClient") == "@angular/common/http":
                    script.http_fields.add(script.text(i - 2))
            elif word == "inject" and script.punct(i + 1, "(") and script.word(i + 2, "HttpClient"):
                if script.punct(i - 1, "=") and script.word(i - 2):
                    script.http_fields.add(script.text(i - 2))
            elif word == "module" and script.punct(i + 1, ".") and script.word(i + 2, "exports"):
                if script.punct(i + 3, "="):
                    self._made(script, "default", i + 4)
            elif word == "export" and script.word(i + 1, "default"):
                self._made(script, "default", i + 2)
            elif script.punct(i + 1, "=") or (
                script.text(i - 1) in _FIELD_MODIFIERS and script.punct(i + 1, ":")
            ):
                self._maybe_field(script, i)

    def _maybe_field(self, script: _Script, i: int) -> None:
        before = script.text(i - 1)
        if not (before in _FIELD_MODIFIERS or before in {";", "{", "}"}):
            return
        j = i + 1
        if script.punct(j, ":"):
            limit = min(script.size, j + 16)
            while j < limit and not script.punct(j, "="):
                if script.punct(j, ";") or script.punct(j, ")") or script.punct(j, ","):
                    return
                j += 1
        if script.punct(j, "=") and not script.punct(j + 1, "="):
            self._field(script, script.text(i), j + 1)

    def _field(self, script: _Script, name: str, start: int) -> None:
        script.fields.setdefault(name, (start, script.expression_end(start)))
        self._made(script, f"this.{name}", start)

    def _declaration(self, script: _Script, i: int) -> None:
        name = script.text(i + 1)
        j = i + 2
        if script.punct(j, ":"):
            limit = min(script.size, j + 16)
            while j < limit and not script.punct(j, "="):
                if script.punct(j, ";"):
                    return
                j += 1
        if not script.punct(j, "=") or script.punct(j + 1, "="):
            return
        start = j + 1
        end = script.expression_end(start)
        script.decls.setdefault(name, []).append((i, start, end))
        self._made(script, name, start)

    def _made(self, script: _Script, name: str, start: int) -> None:
        """If a value builds an axios instance or an Express app, remember which name holds it."""
        head = script.text(start)
        if script.kind(start) != "word":
            return
        if script.punct(start + 1, ".") and script.word(start + 2, "create"):
            if script.spec_of(head) == "axios" and script.punct(start + 3, "("):
                base = None
                if script.punct(start + 4, "{"):
                    base = script.key_value(start + 4, "baseURL")
                script.instances[name] = _Instance("axios", base, script)
            return
        spec = script.spec_of(head)
        bound = script.bound_to(head)
        if spec != "express" or bound is None:
            return
        if script.punct(start + 1, "(") and bound[1] in {None, "default"}:
            self._add_server(script, name, False, script.tokens[start].line)
        elif script.punct(start + 1, "(") and bound[1] == "Router":
            self._add_server(script, name, True, script.tokens[start].line)
        elif (
            script.punct(start + 1, ".")
            and script.word(start + 2, "Router")
            and script.punct(start + 3, "(")
        ):
            self._add_server(script, name, True, script.tokens[start].line)

    def _add_server(self, script: _Script, name: str, is_router: bool, line: int) -> None:
        script.servers.add(name)
        key = (script.rel, name)
        self.servers.setdefault(key, Server(key, "express", is_router, "", line))

    # ------------------------------------------------------------ names across files

    def _follow(self, script: _Script, name: str, seen: set[tuple[str, str]]) -> tuple[_Script, str] | None:
        """Which file and local name an imported name means. A name of ``""`` is the module itself."""
        key = (script.rel, name)
        if key in seen or len(seen) > 64:
            return None
        seen.add(key)
        if name in script.instances or name in script.servers or name in script.decls:
            return script, name
        bound = script.parsed.bindings.get(name)
        if bound is None:
            return None
        target = self._script_of(script.rel, bound[0])
        if target is None:
            return None
        if bound[1] is None:
            return target, ""
        return self._member(target, bound[1], seen)

    def _script_of(self, importer: str, spec: str) -> _Script | None:
        file = self.resolver.resolve(importer, spec)
        return self.scripts.get(file) if file else None

    def _member(self, script: _Script, name: str, seen: set[tuple[str, str]]) -> tuple[_Script, str] | None:
        key = (script.rel, f"member:{name}")
        if key in seen or len(seen) > 64:
            return None
        seen.add(key)
        if name == "default" and (name in script.instances or name in script.servers):
            return script, name
        parsed = script.parsed
        alias = parsed.exports.get(name)
        if alias and alias != name:
            found = self._follow(script, alias, seen)
            if found is not None:
                return found
        again = parsed.reexports.get(name)
        if again:
            target = self._script_of(script.rel, again[0])
            if target is None:
                return None
            return (target, "") if again[1] is None else self._member(target, again[1], seen)
        found = self._follow(script, alias or name, seen)
        if found is not None:
            return found
        if name != "default":
            for spec in parsed.stars:
                target = self._script_of(script.rel, spec)
                found = self._member(target, name, seen) if target is not None else None
                if found is not None:
                    return found
        return None

    def _resolve_chain(self, script: _Script, chain: list[str]) -> tuple[_Script, str] | None:
        if chain[0] == "this":
            return (script, f"this.{chain[1]}") if len(chain) == 2 else None
        found = self._follow(script, chain[0], set())
        for attr in chain[1:]:
            if found is None or found[1]:
                return None
            found = self._member(found[0], attr, set())
        if found is not None and not found[1]:  # the module itself: CommonJS's module.exports
            default = self._member(found[0], "default", set())
            return default
        return found

    # ------------------------------------------------------------ values

    def evaluate(self, script: _Script, start: int, end: int) -> Parts | None:
        self.steps = 0
        parts = self._expression(script, start, end, start, 0)
        if parts is None:
            return None
        joined = merged(parts)
        return None if sum(len(p) for p in joined if isinstance(p, str)) > _MAX_TEXT else joined

    def _expression(self, script: _Script, start: int, end: int, at: int, depth: int) -> Parts | None:
        self.steps += 1
        if depth > _MAX_EVAL_DEPTH or self.steps > _MAX_EVAL_STEPS or start >= end:
            return None
        end = min(end, start + _MAX_EXPRESSION)
        alternatives: list[tuple[int, int]] = []
        terms: list[tuple[int, int]] = []
        begin = i = start
        ternary = False
        while i < end:
            kind = script.kind(i)
            if kind == "punct":
                text = script.tokens[i].text
                if text in {"(", "[", "{"}:
                    i = script.closing(i) + 1
                    continue
                if text == "+":
                    terms.append((begin, i))
                    begin = i + 1
                elif (text == "|" and script.punct(i + 1, "|")) or (
                    text == "?" and script.punct(i + 1, "?")
                ):
                    alternatives.append((start, i))
                    start = begin = i + 2
                    i += 2
                    terms = []
                    continue
                elif text == "?":
                    ternary = True
            elif kind == "template":
                i = self._template_end(script, i, end)
                continue
            i += 1
        if ternary:
            return [Param("value")]
        if alternatives:
            for low, high in alternatives:
                found = self._expression(script, low, high, at, depth + 1)
                if found is not None and not any(isinstance(p, Param) for p in found):
                    return found
            return self._expression(script, start, end, at, depth + 1)
        terms.append((begin, end))
        out: Parts = []
        for low, high in terms:
            value = self._term(script, low, high, at, depth)
            out += value if value is not None else [Param(_last_word(script, low, high))]
        return out

    def _template_end(self, script: _Script, i: int, end: int) -> int:
        """The token after the template opening at ``i``, jumping over each substitution."""
        i += 1
        while i < end:
            kind = script.kind(i)
            if kind == "template_end":
                return i + 1
            if kind == "punct" and script.tokens[i].text == "(":
                i = script.closing(i) + 1
                continue
            i += 1
        return end

    def _term(self, script: _Script, start: int, end: int, at: int, depth: int) -> Parts | None:
        kind = script.kind(start)
        if kind == "string":
            return [script.text(start)]
        if kind == "number":
            return [script.text(start)]
        if kind == "template":
            return self._template(script, start, end, at, depth)
        if kind == "punct" and script.tokens[start].text == "(":
            close = script.closing(start)
            return self._expression(script, start + 1, close, at, depth + 1)
        if kind != "word":
            return None
        chain = [script.text(start)]
        i = start + 1
        while (
            i + 1 < end
            and (script.punct(i, ".") or script.punct(i, "?."))
            and script.word(i + 1)
            and len(chain) < _MAX_CHAIN
        ):
            chain.append(script.text(i + 1))
            i += 2
        if script.punct(i, "(") and i < end:
            if chain[-1] in _URL_WRAPPERS:
                return [Param(_last_word(script, i + 1, script.closing(i)))]
            return [Param(chain[-2] if chain[-1] == "toString" and len(chain) > 1 else chain[-1])]
        if script.punct(i, "["):
            return [Param(chain[-1])]
        return self._named(script, chain, at, depth)

    def _template(self, script: _Script, start: int, end: int, at: int, depth: int) -> Parts:
        out: Parts = []
        i = start + 1
        while i < end:
            kind = script.kind(i)
            if kind == "template_end":
                break
            if kind == "quasi":
                out.append(script.text(i))
                i += 1
            elif kind == "punct" and script.tokens[i].text == "(":
                close = script.closing(i)
                value = self._expression(script, i + 1, close, at, depth + 1)
                out += value if value is not None else [Param(_last_word(script, i + 1, close))]
                i = close + 1
            else:
                i += 1
        return out

    def _named(self, script: _Script, chain: list[str], at: int, depth: int) -> Parts | None:
        name = chain[-1]
        if chain[0] == "this":
            where = script.fields.get(chain[1]) if len(chain) == 2 else None
            if where is None:
                return [Param(name)]
            found = self._expression(script, where[0], where[1], where[0], depth + 1)
            return found if found is not None else [Param(name)]
        if chain[0] in {"process", "import"}:
            return [Param(name)]
        located = self._locate(script, chain[0], at)
        if located is None:
            return [Param(name)]
        owner, start, end = located
        for key in chain[1:]:
            if not owner.punct(start, "{"):
                return [Param(name)]
            value = owner.key_value(start, key)
            if value is None:
                return [Param(name)]
            start, end = value
        found = self._expression(owner, start, end, start, depth + 1)
        return found if found is not None else [Param(name)]

    def _locate(self, script: _Script, name: str, at: int) -> tuple[_Script, int, int] | None:
        """Where a name's value is written: the nearest declaration before use in this function, or at the top of a file."""
        rows = script.decls.get(name)
        if rows:
            places = [r[0] for r in rows]
            index = bisect.bisect_right(places, at) - 1
            top = next((r for r in rows if script.depth[r[0]] == 0), None)
            if index >= 0:
                row = rows[index]
                if script.depth[row[0]] == 0 or self._same_function(script, row[0], at):
                    return script, row[1], row[2]
            return (script, top[1], top[2]) if top else None
        found = self._follow(script, name, set())
        if found is None or found[0] is script or not found[1]:
            return None
        owner, local = found
        rows = owner.decls.get(local)
        top = next((r for r in rows or [] if owner.depth[r[0]] == 0), None)
        return (owner, top[1], top[2]) if top else None

    def _same_function(self, script: _Script, a: int, b: int) -> bool:
        """Whether no function boundary at the top of the declaration's block closes between them."""
        depth = script.depth[a]
        if depth > script.depth[b]:
            return False
        # the bracket that opens the declaration's block must still be open at the use
        i = a
        steps = 0
        while i >= 0 and steps < _MAX_LOOKAHEAD * 8:
            steps += 1
            closer = script.match[i] if script.kind(i) == "punct" else -1
            if script.kind(i) == "punct" and script.text(i) in {"{", "("} and closer > a:
                return closer >= b
            if script.kind(i) == "punct" and script.text(i) in {"}", ")", "]"} and closer >= 0:
                i = closer
            i -= 1
        return False

    # ------------------------------------------------------------ reading calls

    def read(self) -> None:
        self.load()
        for script in self.scripts.values():
            self._read_calls(script)
            self._read_nest(script)
        self._finish()

    def _call_open(self, script: _Script, i: int) -> int:
        """The ``(`` of a call of the word at ``i``, past ``?.`` and type arguments, or -1."""
        j = i + 1
        if script.punct(j, "?."):
            j += 1
        if script.punct(j, "("):
            return j
        if not script.punct(j, "<"):
            return -1
        depth = 0
        limit = min(script.size, j + _MAX_LOOKAHEAD)
        while j < limit:
            if script.kind(j) == "punct":
                text = script.tokens[j].text
                if text == "<":
                    depth += 1
                elif text == ">":
                    depth -= 1
                    if depth == 0:
                        return j + 1 if script.punct(j + 1, "(") else -1
                elif text in {"(", "[", "{"}:
                    j = script.closing(j) + 1
                    continue
                elif text in {";", ")", "}", "]"}:
                    return -1
            j += 1
        return -1

    def _chain_before(self, script: _Script, i: int) -> list[str] | None:
        """``a.b`` written before ``.name`` at ``i``. ``None`` when it hangs off a call or index."""
        chain: list[str] = []
        j = i
        while (script.punct(j - 1, ".") or script.punct(j - 1, "?.")) and len(chain) < _MAX_CHAIN:
            if not script.word(j - 2):
                return None
            chain.append(script.text(j - 2))
            j -= 2
        if script.punct(j - 1, ".") or script.punct(j - 1, "?."):
            return None
        chain.reverse()
        return chain

    def _read_calls(self, script: _Script) -> None:
        for i, token in enumerate(script.tokens):
            if token.kind != "word":
                continue
            word = token.text
            callable_instance = (
                word in script.instances or script.spec_of(word) == "axios"
            ) and not (script.punct(i - 1, ".") or script.punct(i - 1, "?."))
            if word not in _INTERESTING and not callable_instance:
                continue
            if script.word(i - 1, "function") or script.word(i - 1, "async"):
                continue
            opener = self._call_open(script, i)
            if opener < 0:
                continue
            chain = self._chain_before(script, i)
            if chain is None:
                continue
            if word == "fetch":
                self._fetch(script, i, opener, chain)
            elif callable_instance and not chain:
                self._axios_call(script, i, opener, word)
            elif word in {*HTTP_VERBS, "request", "all", "use", "route", "setGlobalPrefix"} and chain:
                self._member_call(script, i, opener, chain, word)
            if len(self.raw_clients) >= MAX_CLIENTS or len(self.pending) >= MAX_ROUTES:
                self.limits.add("this repository has more calls than can be listed; the rest were left out")
                return

    def _add_client(
        self,
        script: _Script,
        i: int,
        method: str,
        framework: str,
        url: tuple[int, int] | None,
        base: Parts | None,
    ) -> None:
        parts = self.evaluate(script, url[0], url[1]) if url is not None else None
        self.raw_clients.append(
            RawClient(script.rel, script.tokens[i].line, method, framework, "", parts, base)
        )

    def _fetch(self, script: _Script, i: int, opener: int, chain: list[str]) -> None:
        if chain and not (len(chain) == 1 and chain[0] in _GLOBALS):
            return
        if not chain and ("fetch" in script.parsed.defs or self._is_local_binding(script, "fetch")):
            return
        args = script.arguments(opener)
        if not args:
            return
        method = "GET"
        if len(args) > 1:
            method = self._method_option(script, args[1])
        self._add_client(script, i, method, "fetch", args[0], None)

    def _is_local_binding(self, script: _Script, name: str) -> bool:
        spec = script.spec_of(name)
        return bool(spec) and bool(self.resolver.resolve(script.rel, spec))

    def _method_option(self, script: _Script, option: tuple[int, int], default: str = "GET") -> str:
        start, end = option
        if not script.punct(start, "{"):
            return "UNKNOWN"
        value = script.key_value(start, "method")
        if value is not None:
            return script.text(value[0]).upper() if script.kind(value[0]) == "string" else "UNKNOWN"
        i = start + 1
        close = min(script.closing(start), end, start + _MAX_EXPRESSION)
        while i < close:  # a spread could carry the method
            if script.punct(i, "..."):
                return "UNKNOWN"
            i = script.closing(i) + 1 if script.text(i) in {"(", "[", "{"} else i + 1
        return default

    def _instance_of(self, script: _Script, chain: list[str]) -> _Instance | None:
        if len(chain) == 1 and script.spec_of(chain[0]) == "axios":
            bound = script.bound_to(chain[0])
            if bound is not None and bound[1] in {None, "default"}:
                return _Instance("axios")
        if len(chain) == 2 and chain[0] == "this" and chain[1] in script.http_fields:
            return _Instance("angular")
        if len(chain) == 1 and chain[0] in script.http_fields:
            return _Instance("angular")
        found = self._resolve_chain(script, chain)
        if found is None:
            return None
        return found[0].instances.get(found[1])

    def _base(self, instance: _Instance) -> Parts | None:
        if instance.base is None or instance.script is None:
            return None
        return self.evaluate(instance.script, instance.base[0], instance.base[1])

    def _axios_call(self, script: _Script, i: int, opener: int, word: str) -> None:
        instance = self._instance_of(script, [word])
        if instance is None:
            return
        args = script.arguments(opener)
        if not args:
            return
        self._config_call(script, i, args, instance)

    def _config_call(
        self, script: _Script, i: int, args: list[tuple[int, int]], instance: _Instance
    ) -> None:
        first = args[0]
        base = self._base(instance)
        if script.punct(first[0], "{"):  # axios({ url, method })
            url = script.key_value(first[0], "url")
            self._add_client(
                script, i, self._method_option(script, first), instance.framework, url, base
            )
            return
        method = self._method_option(script, args[1]) if len(args) > 1 else "GET"
        self._add_client(script, i, method, instance.framework, first, base)

    def _member_call(
        self, script: _Script, i: int, opener: int, chain: list[str], word: str
    ) -> None:
        instance = self._instance_of(script, chain) if word in {*HTTP_VERBS, "request"} else None
        args = script.arguments(opener)
        if instance is not None:
            if not args:
                return
            if word == "request":
                if instance.framework == "angular" and len(args) > 1:
                    verb = script.text(args[0][0]).upper() if script.kind(args[0][0]) == "string" else "UNKNOWN"
                    self._add_client(script, i, verb, "angular", args[1], None)
                else:
                    self._config_call(script, i, args, instance)
                return
            self._add_client(script, i, word.upper(), instance.framework, args[0], self._base(instance))
            return
        if word == "setGlobalPrefix":
            if args:
                text = only_text(self.evaluate(script, args[0][0], args[0][1]))
                if text:
                    self.nest_prefixes.append((script.rel, text))
            return
        if word not in {*HTTP_VERBS, "all", "use", "route"}:
            return
        server = self._server_of(script, chain, args, word)
        if server is None:
            return
        if word == "use":
            self._use(script, server, args, script.tokens[i].line)
        elif word == "route":
            self._route_chain(script, server, opener, args)
        else:
            self._express_route(script, server, word, args, script.tokens[i].line)

    def _server_of(
        self, script: _Script, chain: list[str], args: list[tuple[int, int]], word: str
    ) -> Server | None:
        found = self._resolve_chain(script, chain)
        if found is not None:
            server = self.servers.get((found[0].rel, found[1]))
            if server is not None:
                return server
        # ``module.exports = (app) => { app.get('/x', handler) }``: a parameter in a repository that uses Express
        name = chain[0]
        if (
            len(chain) == 1
            and self.uses_express
            and name in {"app", "router"}
            and name not in script.parsed.bindings
            and name not in script.decls
            and args
            and (word == "use" or len(args) >= 2)
            and (word == "use" or script.kind(args[0][0]) in {"string", "template"})
        ):
            key = (script.rel, name)
            self.servers.setdefault(key, Server(key, "express", name == "router", "", 0))
            return self.servers[key]
        return None

    def _use(
        self, script: _Script, server: Server, args: list[tuple[int, int]], line: int
    ) -> None:
        if not args:
            return
        prefix = ""
        rest = args
        if script.kind(args[0][0]) in {"string", "template"} or (
            script.word(args[0][0]) and args[0][1] - args[0][0] == 1 and self._is_text(script, args[0])
        ):
            text = only_text(self.evaluate(script, args[0][0], args[0][1]))
            if text is None:
                return
            prefix = colon_params(text)
            rest = args[1:]
        for low, high in rest:
            child = self._mount_target(script, low, high)
            if child is not None:
                self.mounts.append(Mount(server.key, child.key, prefix, line))

    def _is_text(self, script: _Script, arg: tuple[int, int]) -> bool:
        parts = self.evaluate(script, arg[0], arg[1])
        return bool(parts) and all(isinstance(p, str) for p in parts or [])

    def _mount_target(self, script: _Script, low: int, high: int) -> Server | None:
        if script.word(low, "require") and script.punct(low + 1, "(") and script.kind(low + 2) == "string":
            target = self._script_of(script.rel, script.text(low + 2))
            if target is None:
                return None
            found = self._member(target, "default", set())
            return self.servers.get((found[0].rel, found[1])) if found else None
        chain = _plain_chain(script, low, high)
        if not chain:
            return None
        found = self._resolve_chain(script, chain)
        return self.servers.get((found[0].rel, found[1])) if found else None

    def _route_chain(
        self, script: _Script, server: Server, opener: int, args: list[tuple[int, int]]
    ) -> None:
        if not args:
            return
        j = script.closing(opener) + 1
        taken = 0
        while script.punct(j, ".") and script.word(j + 1) and script.punct(j + 2, "(") and taken < 16:
            taken += 1
            verb = script.text(j + 1)
            close = script.closing(j + 2)
            if verb in {*HTTP_VERBS, "all"}:
                self._express_route(
                    script,
                    server,
                    verb,
                    [args[0], *script.arguments(j + 2)],
                    script.tokens[j + 1].line,
                )
            j = close + 1

    def _express_route(
        self,
        script: _Script,
        server: Server,
        verb: str,
        args: list[tuple[int, int]],
        line: int,
    ) -> None:
        if len(args) < 2:  # app.get('setting')
            return
        paths = self._route_paths(script, args[0])
        if paths is None:
            self.computed_routes += 1
            return
        handler, name, lines = self._handler(script, args[-1])
        for raw in paths:
            self.pending.append(
                (
                    server.key,
                    RawRoute(
                        "ANY" if verb == "all" else verb.upper(),
                        colon_params(raw),
                        "express",
                        script.rel,
                        line,
                        handler,
                        name,
                        lines,
                        is_catch_all(raw),
                    ),
                )
            )

    def _route_paths(self, script: _Script, arg: tuple[int, int]) -> list[str] | None:
        if script.punct(arg[0], "["):
            found = []
            for low, high in script.arguments(arg[0]):
                text = only_text(self.evaluate(script, low, high))
                if text is not None:
                    found.append(text)
            return found or None
        if script.kind(arg[0]) == "regex":
            return None
        text = only_text(self.evaluate(script, arg[0], arg[1]))
        return [text] if text is not None else None

    def _handler(
        self, script: _Script, arg: tuple[int, int]
    ) -> tuple[str | None, str | None, tuple[int, int] | None]:
        """The symbol a route's last argument names, unwrapping ``wrap(fn)``, or its lines if written in place."""
        low, high = arg
        for _ in range(3):
            chain = _plain_chain(script, low, high)
            if chain:
                if chain[-1] == "bind" and len(chain) > 1:
                    chain = chain[:-1]
                resolved = self._symbol(script, chain)
                return resolved, None if resolved else chain[-1], None
            # fn.bind(x) or wrap(fn): look inside
            j = low
            while j < high and (script.word(j) or script.punct(j, ".")):
                j += 1
            if not script.punct(j, "(") or j == low:
                break
            close = script.closing(j)
            inner = script.arguments(j)
            if script.text(j - 1) == "bind":
                chain = _plain_chain(script, low, j - 2)
                if chain:
                    resolved = self._symbol(script, chain)
                    return resolved, None if resolved else chain[-1], None
            if not inner or close + 1 < high and not script.punct(close + 1, "{"):
                break
            low, high = inner[0]
        start = script.tokens[low].line if low < script.size else 0
        end = script.tokens[min(high, script.size) - 1].line if high > 0 else start
        if script.kind(low) in {"word", "punct"}:
            return f"file:{script.rel}", "<inline>", (start, end)
        return None, None, None

    def _symbol(self, script: _Script, chain: list[str]) -> str | None:
        if self.linker is None:
            return None
        try:
            return self.linker.resolve(script.rel, tuple(chain), f"file:{script.rel}")
        except (KeyError, IndexError):
            return None

    # ------------------------------------------------------------ NestJS

    def _read_nest(self, script: _Script) -> None:
        if script.spec_of("Controller") != "@nestjs/common":
            return
        i = 0
        while i < script.size:
            if script.punct(i, "@") and script.word(i + 1, "Controller"):
                i = self._read_controller(script, i)
                continue
            i += 1

    def _strings_of(self, script: _Script, opener: int, key: str = "path") -> list[str]:
        """What a Nest decorator's argument names: a string, an array of them, or ``{ path }``."""
        args = script.arguments(opener)
        if not args:
            return [""]
        low, high = args[0]
        if script.punct(low, "{"):
            value = script.key_value(low, key)
            if value is None:
                return [""]
            low, high = value
        if script.punct(low, "["):
            found = [only_text(self.evaluate(script, a, b)) for a, b in script.arguments(low)]
            return [t for t in found if t is not None] or [""]
        text = only_text(self.evaluate(script, low, high))
        return [text] if text is not None else []

    def _read_controller(self, script: _Script, i: int) -> int:
        j = i + 2
        prefixes = [""]
        if script.punct(j, "("):
            prefixes = self._strings_of(script, j)
            j = script.closing(j) + 1
        limit = min(script.size, j + _MAX_LOOKAHEAD)
        while j < limit and not script.word(j, "class"):
            if script.punct(j, "(") or script.punct(j, "{"):
                j = script.closing(j)
            j += 1
        if not script.word(j, "class") or not script.word(j + 1):
            return j
        name = script.text(j + 1)
        k = j + 2
        limit = min(script.size, k + _MAX_LOOKAHEAD)
        while k < limit and not script.punct(k, "{"):
            k += 1
        if not script.punct(k, "{"):
            return k
        close = script.closing(k)
        self._read_controller_body(script, k + 1, close, name, prefixes)
        return close + 1

    def _read_controller_body(
        self, script: _Script, start: int, close: int, cls: str, prefixes: list[str]
    ) -> None:
        pending: list[tuple[str, list[str], int]] = []
        k = start
        while k < close:
            if script.punct(k, "@") and script.word(k + 1):
                decorator = script.text(k + 1)
                after = k + 2
                paths = [""]
                if script.punct(after, "("):
                    if decorator in _NEST_VERBS:
                        paths = self._strings_of(script, after)
                    after = script.closing(after) + 1
                if decorator in _NEST_VERBS:
                    pending.append((_NEST_VERBS[decorator], paths, script.tokens[k].line))
                k = after
                continue
            if script.word(k) and script.punct(k + 1, "(") and pending:
                handler = f"method:{script.rel}:{cls}.{script.text(k)}"
                for method, paths, line in pending:
                    for prefix in prefixes:
                        for path in paths:
                            self.nest.append(
                                RawRoute(
                                    method,
                                    colon_params(join_paths(prefix, path)),
                                    "nestjs",
                                    script.rel,
                                    line,
                                    handler,
                                    catch_all=is_catch_all(path),
                                )
                            )
                pending = []
            if script.kind(k) == "punct" and script.text(k) in {"(", "[", "{"}:
                k = script.closing(k) + 1
                continue
            k += 1

    # ------------------------------------------------------------ joining up

    def _finish(self) -> None:
        prefixes, notes = expand_prefixes(self.servers, self.mounts)
        self.limits.update(notes)
        unmounted = 0
        for key, route in self.pending:
            for prefix in prefixes.get(key, ()):
                if len(self.routes) >= MAX_ROUTES:
                    break
                full = join_paths(prefix.text, route.path)
                extra: dict[str, object] = {}
                if not prefix.mounted:
                    unmounted += 1
                    extra["unmounted"] = True
                self.routes.append(
                    RawRoute(
                        route.method,
                        full,
                        route.framework,
                        route.file,
                        route.line,
                        route.handler,
                        route.handler_name,
                        route.handler_lines,
                        route.catch_all and len(full.split("/")) <= 2,
                        extra,
                    )
                )
        for route in self.nest:
            if len(self.routes) >= MAX_ROUTES:
                break
            prefix_text = ""
            for main, text in self.nest_prefixes:
                if route.file.startswith(_project_of(main)):
                    prefix_text = text
                    break
            self.routes.append(
                RawRoute(
                    route.method,
                    join_paths(prefix_text, route.path),
                    route.framework,
                    route.file,
                    route.line,
                    route.handler,
                    catch_all=route.catch_all,
                )
            )
        if unmounted:
            self.limits.add(
                f"{unmounted} Express routes sit on a router nothing visibly mounts, so their paths have only their own part"
            )
        if self.computed_routes:
            self.limits.add(
                f"{self.computed_routes} Express routes have a path computed at run time or a regular expression, so they aren't listed"
            )
        if self.unparsed:
            self.limits.add(
                f"{len(self.unparsed)} scripts couldn't be read: " + ", ".join(sorted(self.unparsed)[:5])
            )


def _project_of(path: str) -> str:
    head, marker, _ = path.partition("/src/")
    if marker:
        return f"{head}/"
    return "" if path.startswith("src/") or "/" not in path else f"{path.rpartition('/')[0]}/"


def _plain_chain(script: _Script, low: int, high: int) -> list[str]:
    """``a.b.c`` when that's all these tokens are."""
    chain: list[str] = []
    i = low
    while i < high and len(chain) < _MAX_CHAIN:
        if not script.word(i):
            return []
        chain.append(script.text(i))
        i += 1
        if i == high:
            return chain
        if not (script.punct(i, ".") or script.punct(i, "?.")):
            return []
        i += 1
    return []


def _last_word(script: _Script, low: int, high: int) -> str:
    name = "param"
    for i in range(max(low, high - _MAX_EXPRESSION), min(high, script.size)):
        if script.kind(i) == "word":
            name = script.text(i)
    return name
