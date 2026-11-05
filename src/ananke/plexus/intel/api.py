"""API pathways: which routes a repository serves, which HTTP calls it makes, and which call reaches which route.

Nothing is imported or run. See docs/reference/repo-intelligence.md for the shape this answers in and for what isn't supported.
"""

from __future__ import annotations

import json
import posixpath
from dataclasses import dataclass, field
from typing import Any

from ananke.plexus.graph.javascript import JS_SKIP_DIRS, JS_SUFFIXES
from ananke.plexus.intel._api_common import (
    HTTP_VERBS,
    MAX_SEGMENTS,
    LineOwners,
    RawClient,
    RawRoute,
    Target,
    brace_params,
    join_paths,
    owners_by_file,
    shape,
    target_of,
    with_base,
)
from ananke.plexus.intel._api_python import PythonReader
from ananke.plexus.intel._api_script import ScriptReader, scan
from ananke.plexus.intel._common import is_fixture, is_test_file, read_text, some
from ananke.plexus.intel.context import RepoContext

MAX_LISTED = 20
MAX_SPECS = 20
MAX_MISSING = 50
MAX_SOURCE_FILES = 20_000
MAX_VISITS = 1024  # trie nodes one client may visit while matching
MAX_ALL_VISITS = 1_000_000
MAX_SAME_SHAPE = 64  # routes kept with one path shape, of which one client picks the nearest
_STATIC = {
    ".json", ".html", ".htm", ".csv", ".txt", ".md", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".css",
    ".js", ".map", ".woff", ".woff2", ".xml", ".yaml", ".yml", ".pdf", ".wasm",
}  # fmt: skip
_SPEC_SUFFIXES = (".json", ".yaml", ".yml")
_SPEC_NAMES = ("openapi", "swagger", "api-docs")
_PROXY_NAMES = {
    "vite.config.ts", "vite.config.js", "vite.config.mjs", "vue.config.js", "webpack.config.js",
    "webpack.dev.js", "setupProxy.js", "proxy.conf.json", "proxy.conf.js", "proxy.config.json",
    "proxy.config.js",
}  # fmt: skip


@dataclass
class _Node:
    literal: dict[str, _Node] = field(default_factory=dict)
    param: _Node | None = None
    routes: list[dict[str, Any]] = field(default_factory=list)


class _RouteIndex:
    """Routes by path shape, so literal segments can be tried before parameters."""

    def __init__(self, routes: list[dict[str, Any]]) -> None:
        self.root = _Node()
        self.visits = 0
        for route in routes:
            if route.get("catch_all") or route["method"] == "WS":
                continue
            node = self.root
            for segment in shape(route["path"]):
                if segment == "{}":
                    node.param = node.param or _Node()
                    node = node.param
                else:
                    node = node.literal.setdefault(segment, _Node())
            if len(node.routes) < MAX_SAME_SHAPE:
                node.routes.append(route)

    def find(self, method: str, path: str, client_file: str) -> tuple[dict[str, Any] | None, bool]:
        """The best route for a client, and whether others matched as well.

        The walk tries a literal before a parameter at every segment, so the first route found has the most literal path.
        """
        segments = shape(path)
        if len(segments) > MAX_SEGMENTS:
            return None, False
        visits = 0
        stack: list[tuple[_Node, int]] = [(self.root, 0)]
        while stack and visits < MAX_VISITS and self.visits < MAX_ALL_VISITS:
            visits += 1
            self.visits += 1
            node, depth = stack.pop()
            if depth == len(segments):
                chosen = _choose(node.routes, method, client_file)
                if chosen is not None:
                    return chosen
                continue
            segment = segments[depth]
            if node.param is not None:
                stack.append((node.param, depth + 1))
            if segment != "{}" and segment in node.literal:
                stack.append((node.literal[segment], depth + 1))
        return None, False


def _common_folders(a: str, b: str) -> int:
    left, right = a.split("/")[:-1], b.split("/")[:-1]
    count = 0
    for x, y in zip(left, right, strict=False):
        if x != y:
            break
        count += 1
    return count


def _choose(
    routes: list[dict[str, Any]], method: str, client_file: str
) -> tuple[dict[str, Any], bool] | None:
    if not routes:
        return None
    if method == "UNKNOWN":  # any route would do, but only one may fit
        return (routes[0], False) if len(routes) == 1 else None
    fitting = [r for r in routes if r["method"] == method] or [
        r for r in routes if r["method"] == "ANY"
    ]
    if not fitting:
        return None
    # the same path in two services: the one nearest the caller
    ranked = sorted(fitting, key=lambda r: (-_common_folders(r["_file"], client_file), r["id"]))
    best = ranked[0]
    tied = len(ranked) > 1 and _common_folders(ranked[1]["_file"], client_file) == _common_folders(
        best["_file"], client_file
    )
    return best, tied


# ---------------------------------------------------------------- proxies


@dataclass(frozen=True)
class _Proxy:
    folder: str
    prefix: str
    evidence: str


def _read_nginx(rel: str, text: str) -> list[_Proxy]:
    """``location /api/ { proxy_pass http://backend/; }``: a URI on proxy_pass replaces the location's prefix."""
    found = []
    location = ""
    line_at = 0
    for number, raw in enumerate(text.splitlines(), 1):
        words = raw.split()
        if len(words) >= 2 and words[0] == "location" and words[1].startswith("/"):
            location, line_at = words[1], number
        elif len(words) >= 2 and words[0] == "proxy_pass" and location:
            target = words[1].rstrip(";")
            _, marker, rest = target.partition("://")
            uri = rest.partition("/")[1:] if marker else ("", "")
            if uri[0] == "/" and uri[1] == "":
                found.append(
                    _Proxy(posixpath.dirname(rel), location.rstrip("/"), f"{rel}:{line_at}")
                )
            location = ""
    return found


def _read_proxy_config(rel: str, text: str) -> list[_Proxy]:
    """A dev server's proxy block whose rewrite removes its prefix: ``pathRewrite`` or Vite's ``rewrite``."""
    tokens = scan(text)
    found = []
    size = len(tokens)
    for i, token in enumerate(tokens):
        if token.kind != "string" or not token.text.startswith("/"):
            continue
        if not (i + 2 < size and tokens[i + 1].text == ":" and tokens[i + 2].text == "{"):
            continue
        prefix = token.text.rstrip("/")
        depth = 0
        strips = False
        j = i + 2
        limit = min(size, j + 128)
        while j < limit:
            now = tokens[j]
            if now.kind == "punct" and now.text == "{":
                depth += 1
            elif now.kind == "punct" and now.text == "}":
                depth -= 1
                if depth == 0:
                    break
            elif now.kind == "string" and now.text.lstrip("^").rstrip("/") == prefix:
                following = tokens[j + 2] if j + 2 < size else None
                if following is not None and tokens[j + 1].text == ":" and following.text == "":
                    strips = following.kind == "string"
            elif now.kind == "regex":
                written = now.text.strip("/").replace("\\", "").lstrip("^").rstrip("/")
                following = tokens[j + 2] if j + 2 < size else None
                if written == prefix and following is not None:
                    strips = following.kind == "string" and following.text == ""
            j += 1
        if strips:
            found.append(_Proxy(posixpath.dirname(rel), prefix, f"{rel}:{token.line}"))
    return found


def _read_proxies(ctx: RepoContext) -> list[_Proxy]:
    found: list[_Proxy] = []
    for rel in ctx.files:
        name = rel.rsplit("/", 1)[-1]
        is_nginx = name == "nginx.conf" or name.endswith(".nginx")
        if not is_nginx and name not in _PROXY_NAMES:
            continue
        text = read_text(ctx, rel)
        if text is None:
            continue
        found += _read_nginx(rel, text) if is_nginx else _read_proxy_config(rel, text)
    return found


def _proxy_for(path: str, client_file: str, proxies: list[_Proxy]) -> _Proxy | None:
    for proxy in proxies:
        inside = not proxy.folder or client_file.startswith(f"{proxy.folder}/")
        if inside and (path == proxy.prefix or path.startswith(f"{proxy.prefix}/")):
            return proxy
    return None


# ---------------------------------------------------------------- OpenAPI


def _yaml_operations(text: str) -> dict[str, Any] | None:
    """The parts of an OpenAPI YAML file this needs, line by line: no parser, no aliases, nothing to expand."""
    title = version = base = ""
    is_spec = False
    section = ""
    operations: list[tuple[str, str]] = []
    path = ""
    path_indent = -1
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        key, colon, value = stripped.partition(":")
        key = key.strip("'\"")
        value = value.strip().strip("'\"")
        if indent == 0 and colon:
            section = key
            if key in {"openapi", "swagger"}:
                is_spec = True
                version = value
            elif key == "basePath":
                base = value
            continue
        if section == "info" and key == "title" and not title:
            title = value
        elif section == "servers" and not base and stripped.startswith("- url:"):
            base = stripped.partition(":")[2].strip().strip("'\"")
        elif section == "paths":
            if key.startswith("/") and colon and (path_indent < 0 or indent <= path_indent):
                path, path_indent = key, indent
            elif path and indent > path_indent and colon and key.lower() in HTTP_VERBS:
                operations.append((key.upper(), path))
    if not is_spec:
        return None
    return {"title": title, "version": version, "base": base, "operations": operations}


def _json_operations(text: str) -> dict[str, Any] | None:
    try:
        spec = json.loads(text)
    except (ValueError, RecursionError):
        return None
    if not isinstance(spec, dict) or not ("openapi" in spec or "swagger" in spec):
        return None
    info = spec.get("info")
    paths = spec.get("paths")
    servers = spec.get("servers")
    operations = []
    if isinstance(paths, dict):
        for path, item in paths.items():
            if isinstance(item, dict):
                operations += [(m.upper(), path) for m in item if m.lower() in HTTP_VERBS]
    base = spec.get("basePath") or ""
    if not base and isinstance(servers, list) and servers and isinstance(servers[0], dict):
        base = servers[0].get("url") or ""
    return {
        "title": info.get("title") if isinstance(info, dict) else "",
        "version": str(spec.get("openapi") or spec.get("swagger") or ""),
        "base": str(base),
        "operations": operations,
    }


def _read_specs(
    ctx: RepoContext, routes: list[dict[str, Any]], limits: set[str]
) -> list[dict[str, Any]]:
    served = {(r["method"], shape(r["path"])) for r in routes}
    any_method = {shape(r["path"]) for r in routes if r["method"] == "ANY"}
    specs: list[dict[str, Any]] = []
    for rel in ctx.files:
        name = rel.rsplit("/", 1)[-1].lower()
        if not name.endswith(_SPEC_SUFFIXES) or not any(word in name for word in _SPEC_NAMES):
            continue
        if is_fixture(rel) or is_test_file(rel):
            continue
        if len(specs) >= MAX_SPECS:
            limits.add(f"only the first {MAX_SPECS} OpenAPI files were read")
            break
        text = read_text(ctx, rel)
        if text is None:
            limits.add(f"{rel} is too big or unreadable, so it wasn't read")
            continue
        read = _json_operations(text) if name.endswith(".json") else _yaml_operations(text)
        if read is None:
            continue
        target = target_of([read["base"]]) if read["base"] else None
        base = target.path if target is not None and target.path != "/" else ""
        missing = []
        for method, path in read["operations"]:
            plain = shape(brace_params(path))
            joined = shape(join_paths(base, brace_params(path)))
            if not any((method, s) in served or s in any_method for s in (plain, joined)):
                missing.append(f"{method} {path}")
        missing.sort()
        entry: dict[str, Any] = {
            "path": rel,
            "title": read["title"] or "",
            "version": read["version"] or "",
            "routes": len(read["operations"]),
            "missing_from_code": missing[:MAX_MISSING],
        }
        specs.append(entry)
        if missing:
            limits.add(
                f"{rel} describes {len(missing)} operations with no matching route in the code, such as {missing[0]}"
            )
    return specs


# ---------------------------------------------------------------- the analysis


def _static_path(path: str) -> bool:
    return posixpath.splitext(path.rsplit("/", 1)[-1])[1].lower() in _STATIC


def _skipped(rel: str) -> bool:
    return bool({*rel.split("/")[:-1]} & JS_SKIP_DIRS)


def _route_dicts(raw: list[RawRoute], limits: set[str]) -> list[dict[str, Any]]:
    """Sorted, one per method, path and handler. The same path in two places keeps both, and the later names its file."""
    seen: dict[tuple[str, str, str | None, str], RawRoute] = {}
    for route in sorted(
        raw, key=lambda r: (r.path, r.method, r.file, r.line, r.handler or "", r.handler_name or "")
    ):
        seen.setdefault((route.method, route.path, route.handler, route.handler_name or ""), route)
    out: list[dict[str, Any]] = []
    used: set[str] = set()
    repeats = 0
    for route in seen.values():
        identity = f"route:{route.method} {route.path}"
        if identity in used:
            identity = f"{identity} ({route.file}:{route.line})"
            repeats += 1
        used.add(identity)
        item: dict[str, Any] = {
            "id": identity,
            "method": route.method,
            "path": route.path,
            "handler": route.handler,
            "framework": route.framework,
            "evidence": f"{route.file}:{route.line}",
            "_file": route.file,
        }
        if route.handler_name:
            item["handler_name"] = route.handler_name
        if route.handler_lines:
            item["handler_lines"] = list(route.handler_lines)
        if route.catch_all:
            item["catch_all"] = True
        item.update(route.extra)
        out.append(item)
    if repeats:
        limits.add(
            f"{repeats} routes repeat a method and path found elsewhere, as separate services do; their ids name their file"
        )
    return out


def _client_dicts(
    raw: list[RawClient], owners: dict[str, LineOwners], limits: set[str]
) -> tuple[list[dict[str, Any]], dict[int, Target]]:
    out: list[dict[str, Any]] = []
    targets: dict[int, Target] = {}
    used: dict[str, int] = {}
    unknown: list[str] = []
    for client in sorted(raw, key=lambda c: (c.file, c.line, c.method)):
        target = target_of(with_base(client.base, client.parts))
        where = f"{client.file}:{client.line}"
        if target is None:
            unknown.append(where)
            continue
        identity = f"client:{where}"
        used[identity] = used.get(identity, 0) + 1
        if used[identity] > 1:
            identity = f"{identity}#{used[identity]}"
        caller = client.caller
        if not caller:
            found = owners.get(client.file)
            caller = found.at(client.line) if found is not None else f"file:{client.file}"
        item: dict[str, Any] = {
            "id": identity,
            "method": client.method,
            "path": target.path,
            "caller": caller,
            "framework": client.framework,
            "evidence": where,
        }
        if target.host:
            item["external"] = True
            item["host"] = target.host
        if target.base_unknown:
            item["base_unknown"] = True
        out.append(item)
        targets[len(out) - 1] = target
    if unknown:
        limits.add(
            f"{len(unknown)} HTTP calls have a URL known only at run time, such as a helper's parameter, so they aren't listed: {some(sorted(unknown))}"
        )
    return out, targets


def _match(
    clients: list[dict[str, Any]],
    routes: list[dict[str, Any]],
    proxies: list[_Proxy],
    limits: set[str],
) -> list[dict[str, Any]]:
    index = _RouteIndex(routes)
    unmatched = []
    for client in clients:
        if client.get("external"):
            continue
        path = client["path"]
        file = client["evidence"].rpartition(":")[0]
        proxy = _proxy_for(path, file, proxies)
        found: tuple[dict[str, Any] | None, bool] = (None, False)
        if proxy is not None:
            stripped = path[len(proxy.prefix) :] or "/"
            found = index.find(client["method"], stripped, file)
            if found[0] is not None:
                client["proxy"] = {"strips": proxy.prefix, "evidence": proxy.evidence}
        if found[0] is None:
            found = index.find(client["method"], path, file)
        route, ambiguous = found
        if route is not None:
            client["route"] = route["id"]
            if ambiguous:
                client["ambiguous"] = True
        elif _static_path(path):
            client["static"] = True
        else:
            unmatched.append(client)
    if index.visits >= MAX_ALL_VISITS:
        limits.add(
            "matching stopped early on a very large set of routes, so some clients may be unmatched wrongly"
        )
    return unmatched


def _note_gaps(ctx: RepoContext, skipped: int, limits: set[str], python: PythonReader) -> None:
    notebooks = [p for p in ctx.files if p.endswith(".ipynb")]
    if notebooks:
        limits.add(
            f"{len(notebooks)} notebooks' own cells weren't read for routes or calls; files they write were"
        )
    templates = [p for p in ctx.files if p.endswith((".vue", ".svelte", ".astro", ".html"))]
    if templates:
        limits.add(
            f"{len(templates)} .vue, .svelte, .astro and .html files weren't read, so calls in their scripts aren't listed"
        )
    if skipped:
        limits.add(f"{skipped} test and fixture files weren't read for routes or calls")
    limits.update(python.limits)


def analyze(ctx: RepoContext) -> dict[str, Any]:
    limits: set[str] = set()
    source = [p for p in ctx.files if p.endswith((".py", *JS_SUFFIXES))]
    kept = [p for p in source if not (is_test_file(p) or is_fixture(p))]
    skipped = len(source) - len(kept)
    if len(kept) > MAX_SOURCE_FILES:
        limits.add(f"only the first {MAX_SOURCE_FILES} source files were read")
        kept = kept[:MAX_SOURCE_FILES]
    file_nodes = {n.path for n in ctx.graph.nodes if n.kind == "file"}
    py_paths = [p for p in kept if p.endswith(".py") and p in file_nodes]
    js_paths = [p for p in kept if p.endswith(JS_SUFFIXES) and p in file_nodes and not _skipped(p)]

    python = PythonReader(ctx, py_paths, ctx.graph)
    python.read()
    scripts = ScriptReader(ctx, js_paths, file_nodes)
    scripts.read()
    limits.update(scripts.limits)
    _note_gaps(ctx, skipped, limits, python)

    owners = {
        path: LineOwners(rows, f"file:{path}")
        for path, rows in owners_by_file(ctx.graph.nodes).items()
    }
    routes = _route_dicts([*python.routes, *scripts.routes], limits)
    clients, _ = _client_dicts([*python.raw_clients, *scripts.raw_clients], owners, limits)
    unmatched = _match(clients, routes, _read_proxies(ctx), limits)
    specs = _read_specs(ctx, routes, limits)

    for route in routes:
        route.pop("_file", None)
    ordered = sorted(limits)
    if len(ordered) > MAX_LISTED:
        ordered = [*ordered[:MAX_LISTED], f"and {len(ordered) - MAX_LISTED} more limits like these"]
    found = bool(routes or clients or specs)
    if not found:
        ordered = ["no routes, HTTP calls or OpenAPI files were found", *ordered]
    return {
        "status": "found" if found else "none",
        "limits": ordered,
        "routes": routes,
        "clients": clients,
        "unmatched_clients": unmatched,
        "specs": specs,
    }
