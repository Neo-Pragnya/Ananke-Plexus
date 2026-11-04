"""Routes and HTTP clients in Python, read with ``ast``: FastAPI, Flask, Django, ``requests`` and ``httpx``.

Nothing is imported or run. Names are followed through the same import resolution the code graph uses, so an id here is an id
there. Files that mention none of these frameworks aren't parsed unless an import leads to them.
"""

from __future__ import annotations

import ast
import re
import warnings
from dataclasses import dataclass, field

from ananke.plexus.graph.models import GraphSnapshot
from ananke.plexus.graph.python_imports import (
    FileImports,
    ModuleIndex,
    Symbols,
    read_imports,
)
from ananke.plexus.graph.python_imports import (
    attribute_chain as _attribute_chain,
)
from ananke.plexus.graph.service import _module_index
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
    angle_params,
    brace_params,
    expand_prefixes,
    is_catch_all,
    join_paths,
    looks_like_url,
    merged,
    only_text,
    param_name,
    regex_path,
)
from ananke.plexus.intel._common import read_text
from ananke.plexus.intel.context import RepoContext

_PARSE_FAILURES = (SyntaxError, ValueError, RecursionError, MemoryError)
_MAX_EVAL_DEPTH = 12
_MAX_EVAL_STEPS = 256
_MAX_TEXT = 2048
_MAX_DJANGO_DEPTH = 16
_MAX_UNWRAP = 4
_WORDS = (
    "fastapi", "flask", "quart", "APIRouter", "Blueprint", "urlpatterns", "requests", "httpx",
    "http.server", "aiohttp", "tornado", "starlette", "sanic", "rest_framework", "flask_restful",
    "BaseHTTPRequestHandler",
)  # fmt: skip
_CONSTRUCTORS = {
    "fastapi.FastAPI": ("fastapi", False),
    "fastapi.APIRouter": ("fastapi", True),
    "fastapi.routing.APIRouter": ("fastapi", True),
    "flask.Flask": ("flask", False),
    "flask.Blueprint": ("flask", True),
    "flask.blueprints.Blueprint": ("flask", True),
    "quart.Quart": ("flask", False),
    "quart.Blueprint": ("flask", True),
}
_ANNOTATIONS = {name.rpartition(".")[2]: kind for name, kind in _CONSTRUCTORS.items()}
_PARAM_NAMES = {"app", "application", "router", "bp", "blueprint", "api"}
_CLIENT_MODULES = {"requests": "requests", "httpx": "httpx"}
_CLIENT_CLASSES = {
    "requests.Session": "requests",
    "requests.session": "requests",
    "requests.sessions.Session": "requests",
    "httpx.Client": "httpx",
    "httpx.AsyncClient": "httpx",
}
_CLIENT_CALLS = {*HTTP_VERBS, "request", "stream"}
_UNSUPPORTED = {
    "http.server": "http.server handlers",
    "aiohttp.web": "aiohttp.web",
    "tornado": "Tornado",
    "starlette": "Starlette routes",
    "sanic": "Sanic",
    "flask_restful": "Flask-RESTful resources",
    "rest_framework.routers": "Django REST framework routers",
}
_DJANGO_FUNCS = {
    "django.urls.path": "path",
    "django.urls.re_path": "re",
    "django.conf.urls.url": "re",
    "django.urls.include": "include",
    "django.conf.urls.include": "include",
}
_METHOD_WORD = re.compile(r"['\"]([A-Za-z]{3,7})['\"]")
_MAX_CHAIN = 16
MAX_PARSED_BYTES = 64_000_000


def attribute_chain(node: ast.expr) -> list[str]:
    """A dotted name short enough to be real. Following a very long one would cost its length squared."""
    chain = _attribute_chain(node)
    return chain if len(chain) <= _MAX_CHAIN else []


@dataclass
class _Scope:
    symbol: str
    parent: _Scope | None = None
    cls: str | None = None
    top_cls: bool = False
    in_function: bool = False
    params: dict[str, ast.expr | None] = field(default_factory=dict)
    values: dict[str, ast.expr] = field(default_factory=dict)
    loops: dict[str, list[ast.expr]] = field(default_factory=dict)


@dataclass
class _File:
    path: str
    tree: ast.Module
    imports: FileImports
    external: dict[str, str] = field(
        default_factory=dict
    )  # local name → dotted name outside the repo
    values: dict[str, ast.expr] = field(default_factory=dict)  # module-level assignments
    attrs: dict[str, ast.expr] = field(default_factory=dict)  # self.x = ..., anywhere in the file
    classes: dict[str, ast.ClassDef] = field(default_factory=dict)
    class_values: dict[str, dict[str, ast.expr]] = field(
        default_factory=dict
    )  # class → attribute → value
    any_class_value: dict[str, ast.expr] = field(default_factory=dict)
    returned: dict[str, str] = field(default_factory=dict)  # function → the name it returns
    clients: dict[str, tuple[str, ast.expr | None]] = field(default_factory=dict)


class _LazyImports(dict[str, FileImports]):
    """Imports read only when a name leads to the file."""

    def __init__(self, reader: PythonReader) -> None:
        super().__init__()
        self.reader = reader

    def get(self, key: str, default: FileImports | None = None) -> FileImports | None:  # type: ignore[override]
        file = self.reader.file(key)
        return file.imports if file is not None else default


class PythonReader:
    def __init__(self, ctx: RepoContext, paths: list[str], graph: GraphSnapshot) -> None:
        self.ctx = ctx
        self.paths = paths
        self.path_set = set(paths)
        self.node_ids = {n.node_id for n in graph.nodes}
        self.nodes = {n.node_id: n for n in graph.nodes}
        known = sorted(n.path for n in graph.nodes if n.kind == "file" and n.path.endswith(".py"))
        self.index: ModuleIndex = _module_index(ctx.root, [ctx.root / p for p in known])
        self.known = set(known)
        defs: dict[str, dict[str, str]] = {}
        methods: dict[str, dict[str, dict[str, str]]] = {}
        for node in graph.nodes:
            if node.kind in {"function", "class"}:
                defs.setdefault(node.path, {})[node.name] = node.node_id
            elif node.kind == "method":
                cls, _, name = node.name.partition(".")
                methods.setdefault(node.path, {}).setdefault(cls, {})[name] = node.node_id
        self.methods = methods
        self.symbols = Symbols(self.index, _LazyImports(self), defs, methods)
        self.files: dict[str, _File | None] = {}
        self.servers: dict[tuple[str, str], Server] = {}
        self.aliases: dict[tuple[str, str], ast.expr] = {}
        self.mounts: list[Mount] = []
        self.pending: list[tuple[tuple[str, str], RawRoute]] = []
        self.routes: list[RawRoute] = []
        self.raw_clients: list[RawClient] = []
        self.limits: set[str] = set()
        self.unparsed: list[str] = []
        self.unsupported: set[str] = set()
        self.nonliteral_methods = 0
        self.computed_paths = 0
        self.unfollowed_mounts: list[str] = []
        self.steps = 0
        self.django: dict[str, list[tuple[str, str, ast.expr | str | None, int, str]]] = {}
        self.django_regex_skipped = 0
        self.django_unfollowed = 0
        self.seen_decorated = 0

    # ------------------------------------------------------------ files

    def file(self, rel: str) -> _File | None:
        if rel in self.files:
            return self.files[rel]
        self.files[rel] = None  # a cycle of imports sees nothing rather than recursing
        text = read_text(self.ctx, rel) if rel in self.known or rel in self.path_set else None
        if text is None:
            return None
        try:
            with warnings.catch_warnings():  # an odd escape in someone's string is not news
                warnings.simplefilter("ignore")
                tree = ast.parse(text)
            imports = read_imports(self.index, rel, tree)
        except _PARSE_FAILURES:
            if rel in self.path_set:
                self.unparsed.append(rel)
            return None
        file = _File(rel, tree, imports)
        self.files[rel] = file
        self._read_file_level(file)
        return file

    def _read_file_level(self, file: _File) -> None:
        for item in ast.walk(file.tree):
            if isinstance(item, ast.Import):
                for alias in item.names:
                    file.external[alias.asname or alias.name.split(".")[0]] = (
                        alias.name if alias.asname else alias.name.split(".")[0]
                    )
            elif isinstance(item, ast.ImportFrom) and not item.level and item.module:
                for alias in item.names:
                    file.external[alias.asname or alias.name] = f"{item.module}.{alias.name}"
            elif isinstance(item, ast.ClassDef) and item.name not in file.classes:
                file.classes[item.name] = item
                values = _class_values(item)
                file.class_values[item.name] = values
                for name, value in values.items():
                    file.any_class_value.setdefault(name, value)
        for dotted in file.external.values():
            for name, what in _UNSUPPORTED.items():
                if dotted == name or dotted.startswith(f"{name}."):
                    self.unsupported.add(what)
        for item in file.tree.body:
            self._module_statement(file, item)
        for item in ast.walk(file.tree):
            if isinstance(item, (ast.Assign, ast.AnnAssign)):
                targets = item.targets if isinstance(item, ast.Assign) else [item.target]
                for target in targets:
                    self._assignment(file, target, item.value)
            elif isinstance(item, (ast.With, ast.AsyncWith)):
                for use in item.items:
                    if use.optional_vars is not None:
                        self._assignment(file, use.optional_vars, use.context_expr)
            elif isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self._parameters(file, item)

    def _module_statement(self, file: _File, item: ast.stmt) -> None:
        if isinstance(item, (ast.Assign, ast.AnnAssign)) and item.value is not None:
            targets = item.targets if isinstance(item, ast.Assign) else [item.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    file.values.setdefault(target.id, item.value)
        elif isinstance(item, (ast.If, ast.Try)):
            for inner in item.body:
                self._module_statement(file, inner)
        elif isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for child in ast.iter_child_nodes(item):
                if isinstance(child, ast.Return) and isinstance(child.value, ast.Name):
                    file.returned[item.name] = child.value.id

    def _callee(self, file: _File, func: ast.expr) -> str:
        """A call's target as a dotted name outside the repository, when it is one."""
        chain = attribute_chain(func)
        if not chain or chain[0] not in file.external:
            return ""
        return ".".join([file.external[chain[0]], *chain[1:]])

    def _assignment(self, file: _File, target: ast.expr, value: ast.expr | None) -> None:
        chain = attribute_chain(target)
        if not chain or value is None:
            return
        if chain[0] == "self" and len(chain) == 2:
            name = f"self.{chain[1]}"
            file.attrs.setdefault(chain[1], value)
        elif len(chain) == 1:
            name = chain[0]
        else:
            return
        key = (file.path, name)
        if isinstance(value, ast.Call):
            dotted = self._callee(file, value.func)
            made = _CONSTRUCTORS.get(dotted)
            if made is not None:
                self._add_server(file, key, made, value)
                return
            client = _CLIENT_CLASSES.get(dotted)
            if client is not None:
                base = _keyword(value, "base_url")
                file.clients.setdefault(name, (client, base))
                return
            if attribute_chain(value.func):
                self.aliases.setdefault(key, value)
        elif attribute_chain(value):
            self.aliases.setdefault(key, value)

    def _add_server(
        self, file: _File, key: tuple[str, str], made: tuple[str, bool], call: ast.Call
    ) -> None:
        framework, is_router = made
        word = "prefix" if framework == "fastapi" else "url_prefix"
        node = _keyword(call, word)
        prefix, unknown = self._prefix(file, node, None)
        self.servers.setdefault(
            key, Server(key, framework, is_router, prefix or "", call.lineno, unknown)
        )

    def _parameters(self, file: _File, fn: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        for arg in [*fn.args.posonlyargs, *fn.args.args, *fn.args.kwonlyargs]:
            key = (file.path, arg.arg)
            if key in self.servers:
                continue
            made = None
            if arg.annotation is not None:
                chain = attribute_chain(arg.annotation)
                made = _ANNOTATIONS.get(chain[-1]) if chain else None
            elif arg.arg in _PARAM_NAMES:
                heads = {d.split(".")[0] for d in file.external.values()}
                if "fastapi" in heads:
                    made = ("fastapi", arg.arg in {"router"})
                elif "flask" in heads:
                    made = ("flask", arg.arg in {"bp", "blueprint"})
            if made is not None:
                self.servers[key] = Server(key, made[0], made[1], "", fn.lineno)

    # ------------------------------------------------------------ names

    def _lookup(
        self, rel: str, chain: list[str], seen: set[tuple[str, str]]
    ) -> tuple[str, str] | None:
        """Which server or client a dotted name means: ``(file, name)``, following imports and simple assignments."""
        if not chain:
            return None
        key = (rel, ".".join(chain[:2])) if chain[0] == "self" else (rel, chain[0])
        if chain[0] == "self":
            return key if len(chain) == 2 and key not in seen else None
        file = self.file(rel)
        if file is None or len(seen) > 64 or key in seen:
            return None
        seen.add(key)
        rest = chain[1:]
        if not rest and (key in self.servers or chain[0] in file.clients):
            return key
        alias = self.aliases.get(key)
        if alias is not None and not rest:
            return self._lookup_expr(rel, alias, seen)
        found = self._through_imports(file, chain, seen)
        return found

    def _through_imports(
        self, file: _File, chain: list[str], seen: set[tuple[str, str]]
    ) -> tuple[str, str] | None:
        imports = file.imports
        for end in range(len(chain) - 1, 0, -1):
            module = imports.dotted.get(".".join(chain[:end]))
            if module:
                return self._lookup(module, chain[end:], seen)
        bound = imports.top_level.get(chain[0]) or imports.nested.get(chain[0])
        if bound is None:
            for star in imports.stars:
                found = self._lookup(star, chain, seen)
                if found is not None:
                    return found
            return None
        target, member = bound
        if member is None:
            return self._lookup(target, chain[1:], seen) if len(chain) > 1 else None
        found = self._lookup(target, [member, *chain[1:]], seen)
        if found is None:
            submodule = self.index.submodule(target, member)
            if submodule and len(chain) > 1:
                return self._lookup(submodule, chain[1:], seen)
        return found

    def _lookup_expr(
        self, rel: str, expr: ast.expr, seen: set[tuple[str, str]]
    ) -> tuple[str, str] | None:
        if isinstance(expr, ast.Call):  # app = create_app()
            chain = attribute_chain(expr.func)
            target = self.symbols.resolve(chain, rel, None) if chain else None
            if target and target.startswith("function:"):
                _, path, name = target.split(":", 2)
                file = self.file(path)
                returned = file.returned.get(name) if file else None
                if returned:
                    return self._lookup(path, [returned], seen)
            return None
        return self._lookup(rel, attribute_chain(expr), seen)

    def _server_of(self, rel: str, expr: ast.expr, scope: _Scope | None = None) -> Server | None:
        found = self._lookup_expr(rel, expr, set())
        if found is None and isinstance(expr, ast.Name) and scope is not None:
            param = self.servers.get((rel, expr.id))
            return param
        return self.servers.get(found) if found else None

    # ------------------------------------------------------------ values

    def _prefix(
        self, file: _File, node: ast.expr | None, scope: _Scope | None
    ) -> tuple[str | None, bool]:
        """A prefix's text, and whether part of it is unknown. What's unknown is left out of the path, and said."""
        if node is None:
            return None, False
        parts = self.evaluate(file, node, scope)
        if parts is None:
            return "", True
        unknown = any(isinstance(p, Param) for p in parts)
        return "".join(p for p in merged(parts) if isinstance(p, str)), unknown

    def evaluate(self, file: _File, node: ast.expr, scope: _Scope | None) -> Parts | None:
        self.steps = 0
        parts = self._value(file, node, scope, 0)
        if parts is None:
            return None
        joined = merged(parts)
        if sum(len(p) for p in joined if isinstance(p, str)) > _MAX_TEXT:
            return None
        return joined

    def _value(self, file: _File, node: ast.expr, scope: _Scope | None, depth: int) -> Parts | None:
        self.steps += 1
        if depth > _MAX_EVAL_DEPTH or self.steps > _MAX_EVAL_STEPS:
            return None
        if isinstance(node, ast.Constant):
            return [node.value] if isinstance(node.value, str) else None
        if isinstance(node, ast.JoinedStr):
            out: Parts = []
            for piece in node.values:
                if isinstance(piece, ast.Constant) and isinstance(piece.value, str):
                    out.append(piece.value)
                elif isinstance(piece, ast.FormattedValue):
                    inner = self._value(file, piece.value, scope, depth + 1)
                    out += inner if inner is not None else [Param(_name_of(piece.value))]
            return out
        if isinstance(node, ast.BinOp):
            if isinstance(node.op, ast.Add):
                left = self._value(file, node.left, scope, depth + 1)
                right = self._value(file, node.right, scope, depth + 1)
                left = left if left is not None else [Param(_name_of(node.left))]
                right = right if right is not None else [Param(_name_of(node.right))]
                return left + right
            if isinstance(node.op, ast.Mod) and isinstance(node.left, ast.Constant):
                text = node.left.value
                if isinstance(text, str) and text.count("%s") <= 8:
                    chunks = text.split("%s")
                    filled: Parts = [chunks[0]]
                    for chunk in chunks[1:]:
                        filled += [Param("param"), chunk]
                    return filled
            return None
        if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or):
            last = self._value(file, node.values[-1], scope, depth + 1)
            first = self._value(file, node.values[0], scope, depth + 1)
            if first is not None and not any(isinstance(p, Param) for p in first):
                return first
            return last if looks_like_url(last) else [Param(_name_of(node.values[0]))]
        if isinstance(node, ast.Call):
            return self._call_value(file, node, scope, depth)
        chain = attribute_chain(node)
        if chain:
            return self._named(file, chain, scope, depth)
        return None

    def _call_value(
        self, file: _File, node: ast.Call, scope: _Scope | None, depth: int
    ) -> Parts | None:
        dotted = self._callee(file, node.func)
        if dotted in {"os.getenv", "os.environ.get"} and node.args:
            name = node.args[0].value if isinstance(node.args[0], ast.Constant) else "env"
            default = node.args[1] if len(node.args) > 1 else _keyword(node, "default")
            if default is not None:
                found = self._value(file, default, scope, depth + 1)
                if found is not None:
                    return found
            return [Param(str(name))]
        if isinstance(node.func, ast.Name) and node.func.id == "str" and node.args:
            return self._value(file, node.args[0], scope, depth + 1)
        if dotted in {"urllib.parse.urljoin", "urllib.parse.quote"} or (
            isinstance(node.func, ast.Name) and node.func.id in {"urljoin", "quote"}
        ):
            pieces = [self._value(file, a, scope, depth + 1) for a in node.args[:2]]
            if node.func and _tail(node.func) == "quote":
                return [Param(_name_of(node.args[0]))] if node.args else None
            if len(pieces) == 2 and pieces[0] is not None and pieces[1] is not None:
                base, tail = merged(pieces[0]), merged(pieces[1])
                if base and tail and isinstance(tail[0], str) and tail[0].startswith("/"):
                    return [*base, *tail]
            return None
        if isinstance(node.func, ast.Attribute) and node.func.attr in {"rstrip", "strip", "lstrip"}:
            return self._value(file, node.func.value, scope, depth + 1)
        return None

    def _named(
        self, file: _File, chain: list[str], scope: _Scope | None, depth: int
    ) -> Parts | None:
        head = chain[0]
        if head in {"self", "cls"} and len(chain) == 2:
            value = file.attrs.get(chain[1])
            value = value if value is not None else file.any_class_value.get(chain[1])
            if value is not None:
                found = self._value(file, value, None, depth + 1)
                if merged(found or []):  # an empty attribute is one that's filled in later
                    return found
            return [Param(chain[1])]
        current = scope
        if len(chain) == 1:
            while current is not None:
                if head in current.values:
                    return self._value(file, current.values[head], current, depth + 1)
                if head in current.params:
                    default = current.params[head]
                    if default is None:
                        return [Param(head)]
                    return self._value(file, default, current.parent, depth + 1)
                current = current.parent
        found = self._module_value(file, chain, depth, set())
        return found if found is not None else [Param(chain[-1])]

    def _module_value(
        self, file: _File, chain: list[str], depth: int, seen: set[tuple[str, str]]
    ) -> Parts | None:
        key = (file.path, ".".join(chain))
        if key in seen or len(seen) > 32:
            return None
        seen.add(key)
        head = chain[0]
        if len(chain) == 1 and head in file.values:
            return self._value(file, file.values[head], None, depth + 1)
        if len(chain) == 2:
            value = file.class_values.get(head, {}).get(chain[1])
            if value is None and head in file.values:
                made = file.values[head]  # settings = Settings()
                if isinstance(made, ast.Call) and isinstance(made.func, ast.Name):
                    if made.func.id in file.classes:
                        value = file.class_values[made.func.id].get(chain[1])
                    else:
                        return self._module_value(file, [made.func.id, chain[1]], depth, seen)
            if value is not None:
                return self._value(file, value, None, depth + 1)
        imports = file.imports
        for end in range(len(chain) - 1, 0, -1):
            module = imports.dotted.get(".".join(chain[:end]))
            other = self.file(module) if module else None
            if other is not None:
                return self._module_value(other, chain[end:], depth, seen)
        bound = imports.top_level.get(head) or imports.nested.get(head)
        if bound is None:
            return None
        target, member = bound
        names = [member, *chain[1:]] if member is not None else chain[1:]
        other = self.file(target)
        if other is None or not names:
            return None
        found = self._module_value(other, names, depth, seen)
        if found is None and member is not None and len(chain) > 1:
            submodule = self.index.submodule(target, member)
            other = self.file(submodule) if submodule else None
            if other is not None:
                return self._module_value(other, chain[1:], depth, seen)
        return found

    # ------------------------------------------------------------ walking a file

    def read(self) -> None:
        try:
            self._read_all()
        except RecursionError:  # past every guard below: what's been read still stands
            self.limits.add("a Python file nested too deeply stopped this read early")

    def _read_all(self) -> None:
        relevant = []
        budget = MAX_PARSED_BYTES
        for path in self.paths:
            text = read_text(self.ctx, path)
            if text is None or not any(word in text for word in _WORDS):
                continue
            budget -= len(text)
            if budget < 0:
                self.limits.add(
                    f"only the first {MAX_PARSED_BYTES // 1_000_000} MB of Python that mentions a web framework was read"
                )
                break
            relevant.append(path)
        loaded = [f for f in (self.file(p) for p in relevant) if f is not None]
        for file in loaded:
            try:
                self._walk(file)
                if "django" in {d.split(".")[0] for d in file.external.values()}:
                    self._read_urlconf(file)
            except (AttributeError, IndexError, KeyError, TypeError, ValueError) as exc:
                self.limits.add(f"{file.path} couldn't be read: {type(exc).__name__}")
        self._resolve_routes()
        self._resolve_django()

    def _walk(self, file: _File) -> None:
        rel = file.path
        stack: list[tuple[ast.AST, _Scope]] = [(file.tree, _Scope(f"file:{rel}"))]
        self._fill_scope(file.tree, stack[0][1])
        while stack:
            node, scope = stack.pop()
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    inner = self._function_scope(rel, child, scope)
                    self._decorators(file, child, scope, inner)
                    stack.append((child, inner))
                elif isinstance(child, ast.ClassDef):
                    stack.append(
                        (
                            child,
                            _Scope(
                                scope.symbol,
                                scope,
                                child.name,
                                top_cls=scope.parent is None,
                                in_function=scope.in_function,
                            ),
                        )
                    )
                else:
                    if isinstance(child, ast.Call):
                        self._call(file, child, scope)
                    stack.append((child, scope))

    def _function_scope(
        self, rel: str, fn: ast.FunctionDef | ast.AsyncFunctionDef, scope: _Scope
    ) -> _Scope:
        symbol = scope.symbol
        if not scope.in_function:
            if scope.cls is None and scope.parent is None:
                candidate = f"function:{rel}:{fn.name}"
            elif scope.cls is not None and scope.top_cls:
                candidate = f"method:{rel}:{scope.cls}.{fn.name}"
            else:
                candidate = ""
            if candidate in self.node_ids:
                symbol = candidate
        inner = _Scope(symbol, scope, scope.cls, in_function=True)
        arguments = [*fn.args.posonlyargs, *fn.args.args]
        defaults: list[ast.expr | None] = [None] * (len(arguments) - len(fn.args.defaults))
        defaults += list(fn.args.defaults)
        for arg, default in zip(arguments, defaults, strict=False):
            inner.params[arg.arg] = default
        for arg, default in zip(fn.args.kwonlyargs, fn.args.kw_defaults, strict=False):
            inner.params[arg.arg] = default
        self._fill_scope(fn, inner)
        return inner

    def _fill_scope(self, owner: ast.AST, scope: _Scope) -> None:
        """Assignments and loops directly in this function, not in those nested inside it, so each is read once."""
        stack = list(ast.iter_child_nodes(owner))
        while stack:
            item = stack.pop()
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                continue
            if isinstance(item, ast.Assign) and scope.parent is not None:
                for target in item.targets:
                    if isinstance(target, ast.Name):
                        scope.values.setdefault(target.id, item.value)
            elif (
                isinstance(item, ast.For)
                and isinstance(item.target, ast.Name)
                and isinstance(item.iter, (ast.List, ast.Tuple))
            ):
                scope.loops[item.target.id] = list(item.iter.elts)
            stack.extend(ast.iter_child_nodes(item))

    def _decorators(
        self,
        file: _File,
        fn: ast.FunctionDef | ast.AsyncFunctionDef,
        outer: _Scope,
        inner: _Scope,
    ) -> None:
        handler, name, lines = self._handler(file.path, fn, outer, inner)
        for decorator in fn.decorator_list:
            if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute):
                continue
            attr = decorator.func.attr
            if attr not in {*HTTP_VERBS, "route", "api_route", "websocket"}:
                continue
            server = self._server_of(file.path, decorator.func.value, outer)
            if server is None:
                continue
            path = decorator.args[0] if decorator.args else _keyword(decorator, "path")
            path = path if path is not None else _keyword(decorator, "rule")
            self._add_route(
                file,
                server,
                attr,
                path,
                _keyword(decorator, "methods"),
                decorator.lineno,
                (handler, name, lines),
                outer,
            )

    def _handler(
        self,
        rel: str,
        fn: ast.FunctionDef | ast.AsyncFunctionDef,
        outer: _Scope,
        inner: _Scope,
    ) -> tuple[str, str | None, tuple[int, int] | None]:
        if not outer.in_function and inner.symbol != outer.symbol:
            return inner.symbol, None, None
        end = fn.end_lineno or fn.lineno
        # the graph holds the symbol around it, so this handler's calls are the ones in its lines
        return inner.symbol, fn.name, (fn.lineno, end)

    def _add_route(
        self,
        file: _File,
        server: Server,
        attr: str,
        path_node: ast.expr | None,
        methods_node: ast.expr | None,
        line: int,
        handler: tuple[str | None, str | None, tuple[int, int] | None],
        scope: _Scope | None,
    ) -> None:
        if path_node is None:
            self.computed_paths += 1
            return
        text = only_text(self.evaluate(file, path_node, scope))
        if text is None:
            self.computed_paths += 1
            return
        methods = self._methods(attr, methods_node, file, scope)
        for method in methods:
            route = RawRoute(
                method,
                _normal(text),
                server.framework,
                file.path,
                line,
                handler[0],
                handler[1],
                handler[2],
                is_catch_all(text),
            )
            self.pending.append((server.key, route))

    def _methods(
        self, attr: str, node: ast.expr | None, file: _File, scope: _Scope | None
    ) -> list[str]:
        if attr == "websocket":
            return ["WS"]
        if attr not in {"route", "api_route", "add_url_rule", "add_api_route"}:
            return [attr.upper()]
        if node is None:
            return ["GET"]
        target = node
        if isinstance(node, ast.Name):
            value = file.values.get(node.id)
            target = value if value is not None else node
        if isinstance(target, (ast.List, ast.Tuple, ast.Set)):
            found = [
                e.value.upper()
                for e in target.elts
                if isinstance(e, ast.Constant) and isinstance(e.value, str)
            ]
            if found and len(found) == len(target.elts):
                return sorted(set(found))
        self.nonliteral_methods += 1
        return ["GET"]

    # ------------------------------------------------------------ calls

    def _call(self, file: _File, call: ast.Call, scope: _Scope) -> None:
        func = call.func
        attr = func.attr if isinstance(func, ast.Attribute) else ""
        if attr in {"include_router", "register_blueprint", "mount"} and isinstance(
            func, ast.Attribute
        ):
            self._mount(file, call, func, attr, scope)
        elif attr in {"add_api_route", "add_url_rule"} and isinstance(func, ast.Attribute):
            self._registered(file, call, func, attr, scope)
        elif attr in _CLIENT_CALLS and isinstance(func, ast.Attribute):
            self._client(file, call, func, attr, scope)

    def _mount(
        self, file: _File, call: ast.Call, func: ast.Attribute, attr: str, scope: _Scope
    ) -> None:
        parent = self._server_of(file.path, func.value, scope)
        if parent is None:
            return
        where = f"{file.path}:{call.lineno}"
        if attr == "mount":
            if len(call.args) < 2:
                return
            prefix_node: ast.expr | None = call.args[0]
            child_nodes = [call.args[1]]
        else:
            if not call.args:
                return
            prefix_node = _keyword(
                call, "prefix" if parent.framework == "fastapi" else "url_prefix"
            )
            child_nodes = [call.args[0]]
        if isinstance(child_nodes[0], ast.Name) and child_nodes[0].id in scope.loops:
            child_nodes = scope.loops[child_nodes[0].id]
        prefix, unknown = self._prefix(file, prefix_node, scope)
        for child_node in child_nodes:
            child = self._server_of(file.path, child_node, scope)
            if child is None:
                if attr != "mount":
                    self.unfollowed_mounts.append(where)
                continue
            self.mounts.append(
                Mount(
                    parent.key,
                    child.key,
                    prefix,
                    call.lineno,
                    replaces=parent.framework == "flask",
                    prefix_unknown=unknown,
                )
            )

    def _registered(
        self, file: _File, call: ast.Call, func: ast.Attribute, attr: str, scope: _Scope
    ) -> None:
        server = self._server_of(file.path, func.value, scope)
        if server is None or not call.args:
            return
        if attr == "add_api_route":
            view = call.args[1] if len(call.args) > 1 else _keyword(call, "endpoint")
        else:
            view = call.args[2] if len(call.args) > 2 else _keyword(call, "view_func")
        handlers = self._view(file.path, view, scope) if view is not None else [(None, None)]
        methods_node = _keyword(call, "methods")
        for handler, verb in handlers:
            self._add_route(
                file,
                server,
                verb.lower() if verb else attr,
                call.args[0],
                None if verb else methods_node,
                call.lineno,
                (handler, _name_of(view) if handler is None and view is not None else None, None),
                scope,
            )

    def _view(
        self, rel: str, view: ast.expr, scope: _Scope | None
    ) -> list[tuple[str | None, str | None]]:
        """The symbol a view names and, for a class-based one, one row per method it defines."""
        for _ in range(_MAX_UNWRAP):
            if not isinstance(view, ast.Call):
                break
            chain = attribute_chain(view.func)
            if chain and chain[-1] == "as_view":
                target = self.symbols.resolve(chain[:-1], rel, None)
                return self._class_view(target)
            if not view.args:
                return [(None, None)]
            view = view.args[0]  # login_required(view)
        chain = attribute_chain(view)
        target = self.symbols.resolve(chain, rel, None) if chain else None
        return [(target, None)] if target else [(None, None)]

    def _class_view(self, target: str | None) -> list[tuple[str | None, str | None]]:
        if not target or not target.startswith("class:"):
            return [(None, None)]
        _, path, name = target.split(":", 2)
        own = self.methods.get(path, {}).get(name, {})
        verbs = [(own[v], v.upper()) for v in HTTP_VERBS if v in own]
        return list(verbs) or [(target, None)]

    def _client(
        self, file: _File, call: ast.Call, func: ast.Attribute, attr: str, scope: _Scope
    ) -> None:
        if len(self.raw_clients) >= MAX_CLIENTS:
            return
        chain = attribute_chain(func.value)
        if not chain:
            return
        framework: str | None = None
        base: Parts | None = None
        dotted = ".".join([file.external.get(chain[0], ""), *chain[1:]])
        if dotted in _CLIENT_MODULES:
            framework = _CLIENT_MODULES[dotted]
        else:
            found = self._lookup(file.path, chain, set())
            owner = self.file(found[0]) if found else None
            name = found[1] if found else ""
            if owner is not None and name in owner.clients:
                framework, base_node = owner.clients[name]
                base = self.evaluate(owner, base_node, None) if base_node is not None else None
        if framework is None:
            return
        method_node: ast.expr | None = None
        if attr == "request" or attr == "stream":
            method_node = call.args[0] if call.args else _keyword(call, "method")
            url = call.args[1] if len(call.args) > 1 else _keyword(call, "url")
            method = "UNKNOWN"
            if isinstance(method_node, ast.Constant) and isinstance(method_node.value, str):
                method = method_node.value.upper()
        else:
            url = call.args[0] if call.args else _keyword(call, "url")
            method = attr.upper()
        parts = self.evaluate(file, url, scope) if url is not None else None
        self.raw_clients.append(
            RawClient(file.path, call.lineno, method, framework, scope.symbol, parts, base)
        )

    # ------------------------------------------------------------ joining up

    def _resolve_routes(self) -> None:
        prefixes, notes = expand_prefixes(self.servers, self.mounts)
        self.limits.update(notes)
        unmounted = 0
        unknown = 0
        for key, route in self.pending:
            for prefix in prefixes.get(key, ()):
                if len(self.routes) >= MAX_ROUTES:
                    break
                full = join_paths(prefix.text, route.path)
                extra: dict[str, object] = {}
                if not prefix.mounted:
                    unmounted += 1
                    extra["unmounted"] = True
                if prefix.unknown:
                    unknown += 1
                    extra["prefix_unknown"] = True
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
        if unmounted:
            self.limits.add(
                f"{unmounted} Python routes sit on a router nothing visibly includes, so their paths have only that router's prefix"
            )
        if unknown:
            self.limits.add(
                f"{unknown} Python routes have a prefix known only at run time, so their paths leave it out"
            )
        if self.nonliteral_methods:
            self.limits.add(
                f"{self.nonliteral_methods} routes take their methods from a computed value, so they're listed as GET"
            )
        if self.computed_paths:
            self.limits.add(
                f"{self.computed_paths} Python routes have a path computed at run time, so they aren't listed"
            )
        if self.unfollowed_mounts:
            shown = ", ".join(sorted(self.unfollowed_mounts)[:5])
            self.limits.add(
                f"{len(self.unfollowed_mounts)} routers are included from a value that couldn't be followed: {shown}"
            )
        for what in sorted(self.unsupported):
            self.limits.add(f"{what} aren't read as routes, and this repository uses them")
        if self.unparsed:
            self.limits.add(
                f"{len(self.unparsed)} Python files that mention a web framework don't parse: "
                + ", ".join(sorted(self.unparsed)[:5])
            )

    # ------------------------------------------------------------ Django

    def _django_kind(self, file: _File, func: ast.expr) -> str:
        return _DJANGO_FUNCS.get(self._callee(file, func), "")

    def _read_urlconf(self, file: _File) -> None:
        entries: list[tuple[str, str, ast.expr | str | None, int, str]] = []
        for item in file.tree.body:
            value: ast.expr | None = None
            if (
                isinstance(item, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "urlpatterns" for t in item.targets)
            ) or (
                isinstance(item, ast.AugAssign)
                and isinstance(item.target, ast.Name)
                and item.target.id == "urlpatterns"
            ):
                value = item.value
            if value is not None:
                self._patterns(file, value, entries, 0)
        if entries or any(
            isinstance(i, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "urlpatterns" for t in i.targets)
            for i in file.tree.body
        ):
            self.django[file.path] = entries

    def _patterns(
        self,
        file: _File,
        value: ast.expr,
        out: list[tuple[str, str, ast.expr | str | None, int, str]],
        depth: int,
    ) -> None:
        if depth > _MAX_DJANGO_DEPTH:
            return
        if isinstance(value, ast.BinOp) and isinstance(value.op, ast.Add):
            self._patterns(file, value.left, out, depth + 1)
            self._patterns(file, value.right, out, depth + 1)
            return
        if isinstance(value, (ast.List, ast.Tuple)):
            for item in value.elts:
                if isinstance(item, ast.Call):
                    self._pattern(file, item, out, depth)
            return
        if isinstance(value, ast.Call) and self._django_kind(file, value.func):
            self._pattern(file, value, out, depth)

    def _pattern(
        self,
        file: _File,
        call: ast.Call,
        out: list[tuple[str, str, ast.expr | str | None, int, str]],
        depth: int,
    ) -> None:
        kind = self._django_kind(file, call.func)
        if kind not in {"path", "re"} or len(call.args) < 2:
            return
        text = only_text(self.evaluate(file, call.args[0], None))
        if text is None:
            self.computed_paths += 1
            return
        if kind == "re":
            route = regex_path(text)
            if route is None:
                self.django_regex_skipped += 1
                return
        else:
            route = angle_params(text)
        view = call.args[1]
        if isinstance(view, ast.Call) and self._django_kind(file, view.func) == "include":
            target = self._include_target(file, view, out, route, depth)
            if target is not None:
                out.append(("include", route, target, call.lineno, file.path))
            return
        out.append(("view", route, view, call.lineno, file.path))

    def _include_target(
        self,
        file: _File,
        call: ast.Call,
        out: list[tuple[str, str, ast.expr | str | None, int, str]],
        route: str,
        depth: int,
    ) -> str | None:
        if not call.args:
            return None
        node = call.args[0]
        if isinstance(node, ast.Tuple) and node.elts:
            node = node.elts[0]
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            resolved = self.index.resolve_absolute(file.path, node.value)
            if resolved.file:
                return resolved.file
            self.django_unfollowed += 1
            return None
        if isinstance(node, (ast.List, ast.BinOp)):
            nested: list[tuple[str, str, ast.expr | str | None, int, str]] = []
            self._patterns(file, node, nested, depth + 1)
            for kind, inner, target, inner_line, path in nested:
                out.append((kind, join_paths(route, inner).lstrip("/"), target, inner_line, path))
            return None
        chain = attribute_chain(node)
        if chain:
            current = self.symbols.local(file.path, chain[0])
            for attr in chain[1:]:
                if current is None or not current.startswith("file:"):
                    break
                current = self.symbols.member(current.removeprefix("file:"), attr)
            if current is not None and current.startswith("file:"):
                return current.removeprefix("file:")
        self.django_unfollowed += 1
        return None

    def _django_methods(self, handler: str | None) -> list[str]:
        node = self.nodes.get(handler or "")
        decorators = node.metadata.get("decorators") if node is not None else None
        if not isinstance(decorators, list):
            return ["ANY"]
        for raw in decorators:
            text = str(raw)[:256]
            if text.startswith(("require_http_methods", "api_view")) or text.endswith(
                (".require_http_methods", ".api_view")
            ):
                found = [m.upper() for m in _METHOD_WORD.findall(text)]
                return sorted(set(found)) or ["ANY"]
            if text.rpartition(".")[2] in {"require_GET", "require_safe"}:
                return ["GET"]
            if text.rpartition(".")[2] == "require_POST":
                return ["POST"]
        return ["ANY"]

    def _resolve_django(self) -> None:
        included = {
            str(target)
            for entries in self.django.values()
            for kind, _, target, _, _ in entries
            if kind == "include"
        }
        roots = sorted(path for path in self.django if path not in included)
        orphans = sorted(set(self.django) - set(roots))
        seen: set[str] = set()
        steps = 0
        for start in [*roots, *orphans]:
            stack: list[tuple[str, str, frozenset[str], int]] = [("", start, frozenset({start}), 0)]
            while stack and len(self.routes) < MAX_ROUTES and steps < MAX_ROUTES * 4:
                steps += 1
                prefix, path, trail, depth = stack.pop()
                if path in seen and not prefix:
                    continue
                seen.add(path)
                for kind, route, target, line, source in self.django.get(path, ()):
                    full = join_paths(prefix, route) if prefix or route else "/"
                    if kind == "include":
                        if str(target) not in trail and depth < _MAX_DJANGO_DEPTH:
                            stack.append((full, str(target), trail | {str(target)}, depth + 1))
                        continue
                    self._django_route(full, target, source, line)
        if self.django_regex_skipped:
            self.limits.add(
                f"{self.django_regex_skipped} Django re_path patterns say more than literals and named groups, so they aren't listed"
            )
        if self.django_unfollowed:
            self.limits.add(
                f"{self.django_unfollowed} Django include() calls couldn't be followed, such as a REST framework router's urls"
            )

    def _django_route(self, path: str, view: object, source: str, line: int) -> None:
        if not isinstance(view, ast.expr):
            return
        handlers = self._view(source, view, None)
        written = _name_of(view)
        for handler, verb in handlers:
            methods = [verb] if verb else self._django_methods(handler)
            for method in methods:
                self.routes.append(
                    RawRoute(
                        method,
                        path,
                        "django",
                        source,
                        line,
                        handler,
                        None if handler else written,
                    )
                )


def _normal(path: str) -> str:
    return brace_params(angle_params(path))


def _keyword(call: ast.Call, name: str) -> ast.expr | None:
    return next((k.value for k in call.keywords if k.arg == name), None)


def _tail(node: ast.expr) -> str:
    chain = attribute_chain(node)
    return chain[-1] if chain else ""


def _name_of(node: ast.expr) -> str:
    chain = attribute_chain(node)
    if chain:
        return chain[-1]
    if isinstance(node, ast.Call):
        return param_name(_tail(node.func)) if not node.args else _name_of(node.args[0])
    if isinstance(node, ast.Subscript):
        key = node.slice
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            return param_name(key.value)
        return _name_of(key) if isinstance(key, (ast.Name, ast.Attribute)) else _name_of(node.value)
    return "param"


def _class_values(cls: ast.ClassDef) -> dict[str, ast.expr]:
    found: dict[str, ast.expr] = {}
    for item in cls.body:
        if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
            if item.value is not None:
                found.setdefault(item.target.id, _field_default(item.value))
        elif isinstance(item, ast.Assign):
            for target in item.targets:
                if isinstance(target, ast.Name):
                    found.setdefault(target.id, _field_default(item.value))
    return found


def _field_default(value: ast.expr) -> ast.expr:
    """``Field(default="/api")`` means ``"/api"``."""
    if isinstance(value, ast.Call):
        found = _keyword(value, "default")
        if found is not None:
            return found
        if value.args and _tail(value.func) == "Field":
            return value.args[0]
    return value


__all__ = ["PythonReader"]
