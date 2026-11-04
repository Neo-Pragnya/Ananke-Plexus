"""What the route and client readers share: normalising paths, resolving prefixes, and who owns a line.

Regular expressions here match one bracketed name at a time, and everything else walks a string once, so a hostile path can't
make them backtrack.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field

HTTP_VERBS = ("get", "post", "put", "patch", "delete", "head", "options")
MAX_PATH = 2048
MAX_SEGMENTS = 32
MAX_ROUTES = 5000
MAX_CLIENTS = 5000
MAX_PREFIXES = (
    32  # paths one router can be reached by: stops a router mounted twice at each of many levels
)
MAX_MOUNT_DEPTH = 32
_ANGLE = re.compile(r"<([^<>]*)>")
_BRACE = re.compile(r"\{([^{}]*)\}")
_QUERY_NAMES = {"query", "qs", "params", "querystring", "search", "searchparams", "queryparams"}
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "[::1]"}  # noqa: S104 - a name to recognise, nothing binds to it


@dataclass(frozen=True)
class Param:
    """A value only known at run time."""

    name: str


Parts = list[str | Param]


def param_name(raw: str) -> str:
    """The last identifier of an expression, or ``param``."""
    name = ""
    for ch in reversed(raw[-128:]):
        if ch.isalnum() or ch == "_":
            name = ch + name
        elif name:
            break
    return name if name and not name[0].isdigit() else "param"


def join_parts(parts: Iterable[str | Param]) -> str:
    return "".join(p if isinstance(p, str) else "{" + p.name + "}" for p in parts)


def merged(parts: Iterable[str | Param]) -> Parts:
    """Neighbouring text joined up, once, so long chains stay linear."""
    out: Parts = []
    buffer: list[str] = []
    for part in parts:
        if isinstance(part, str):
            buffer.append(part)
            continue
        if buffer:
            out.append("".join(buffer))
            buffer = []
        out.append(part)
    if buffer:
        out.append("".join(buffer))
    return [p for p in out if p != ""]


def only_text(parts: Parts | None) -> str | None:
    """The string these parts make, if they're all known."""
    if parts is None:
        return None
    joined = merged(parts)
    if not joined:
        return ""
    return joined[0] if len(joined) == 1 and isinstance(joined[0], str) else None


def looks_like_url(parts: Parts | None) -> bool:
    """Whether a fallback like ``x || "/api"`` is a URL. ``id || "new"`` says nothing about the path."""
    joined = merged(parts or [])
    if not joined or not isinstance(joined[0], str):
        return False
    return joined[0].startswith(("/", "http://", "https://"))


def join_paths(*pieces: str) -> str:
    """Pieces of a route's path as one, always with a leading slash and no doubled ones. A trailing slash stays."""
    kept = [p for p in pieces if p]
    text = "/".join(kept)
    segments = [s for s in text.split("/") if s]
    tail = "/" if kept and kept[-1].endswith("/") and segments else ""
    return "/" + "/".join(segments) + tail


def brace_params(path: str) -> str:
    """FastAPI and OpenAPI: ``{id:path}`` becomes ``{id}``."""
    return _BRACE.sub(lambda m: "{" + m[1].partition(":")[0] + "}", path)


def angle_params(path: str) -> str:
    """Flask and Django: ``<int:id>`` becomes ``{id}``."""
    return _ANGLE.sub(lambda m: "{" + m[1].rpartition(":")[2] + "}", path)


def colon_params(path: str) -> str:
    """Express and NestJS: ``:id`` and ``:id?`` become ``{id}``, and ``*`` becomes ``{*}``."""
    out = []
    for segment in path.split("/"):
        if segment.startswith(":"):
            name = segment[1:].partition("(")[0].rstrip("?+*")
            out.append("{" + name + "}" if name else segment)
        elif segment in {"*", "(.*)", "*splat"}:
            out.append("{*}")
        else:
            out.append(segment)
    return "/".join(out)


def is_catch_all(raw: str) -> bool:
    """A route written to take any number of segments, as a single-page app's fallback is."""
    if "(.*)" in raw or ":path}" in raw or "<path:" in raw:
        return True
    return raw.rstrip("/").endswith("*")


def regex_path(pattern: str) -> str | None:
    """A Django ``re_path`` pattern as a path, or ``None`` when it says more than literals and named groups can."""
    text = pattern[1:] if pattern.startswith("^") else pattern
    text = text.removesuffix("$")
    text = text.removesuffix("/?")
    out: list[str] = []
    i = 0
    size = len(text)
    while i < size:
        ch = text[i]
        if ch == "(" and text.startswith("(?P<", i):
            close = text.find(">", i)
            if close < 0:
                return None
            name = text[i + 4 : close]
            depth = 1
            j = close + 1
            while j < size and depth:
                if text[j] == "\\":
                    j += 1
                elif text[j] == "(":
                    depth += 1
                elif text[j] == ")":
                    depth -= 1
                j += 1
            if depth:
                return None
            out.append("{" + name + "}")
            i = j
        elif ch == "\\" and i + 1 < size and not text[i + 1].isalnum():
            out.append(text[i + 1])
            i += 2
        elif ch in "()[]{}*+?|^$\\.":
            return None
        else:
            out.append(ch)
            i += 1
    return "".join(out)


@dataclass(frozen=True)
class Target:
    """Where a client's request goes."""

    path: str
    host: str | None = None  # another host's name, when it isn't local
    base_unknown: bool = False  # what comes before the path is only known at run time


def _clean_segments(text: str) -> list[str] | None:
    segments: list[str] = []
    for segment in text.split("/"):
        if segment in {"", "."}:
            continue
        if segment == "..":
            if segments:
                segments.pop()
            continue
        segments.append(segment)
        if len(segments) > MAX_SEGMENTS:
            return None
    if segments and segments[-1].endswith("}"):
        last = segments[-1]
        opener = last.rfind("{")
        if opener > 0 and last[opener + 1 : -1].lower() in _QUERY_NAMES:
            segments[-1] = last[
                :opener
            ]  # ``/orders${query}``: the query string, not a part of the path
    return segments


def target_of(parts: Parts | None) -> Target | None:
    """Where a URL goes, or ``None`` when its path is truly unknown or it isn't HTTP.

    A leading unknown (``${API}/orders``) leaves a known path behind it, marked as such. Nothing can be said about ``${url}``.
    """
    if not parts:
        return None
    pieces = merged(parts)
    if not pieces:
        return None
    base_unknown = False
    if isinstance(pieces[0], Param):
        following = pieces[1] if len(pieces) > 1 else None
        if not isinstance(following, str) or not following.startswith("/"):
            return None
        base_unknown = True
        pieces = pieces[1:]
    text = join_parts(pieces)
    if len(text) > MAX_PATH:
        return None
    host: str | None = None
    lowered = text[:12].lower()
    if lowered.startswith(("http://", "https://", "//")):
        start = text.find("//") + 2
        slash = text.find("/", start)
        cut = [p for p in (slash, text.find("?", start), text.find("#", start)) if p >= 0]
        end = min(cut) if cut else len(text)
        authority = text[start:end]
        text = text[end:]
        name = authority.rpartition("@")[2].partition(":")[0].lower()
        if "{" in authority:
            base_unknown = True
        elif name not in _LOCAL_HOSTS and "." in name:
            host = name
    else:
        scheme, colon, _ = lowered.partition(":")
        if colon and scheme.isascii() and scheme.replace("+", "").replace("-", "").isalnum():
            return None  # data:, blob:, ws:, mailto:
    for mark in ("?", "#"):
        where = text.find(mark)
        if where >= 0:
            text = text[:where]
    segments = _clean_segments(text)
    if segments is None:
        return None
    if base_unknown and not any("{" not in s for s in segments):
        return None
    tail = "/" if text.endswith("/") and segments else ""
    return Target("/" + "/".join(segments) + tail, host, base_unknown)


def shape(path: str) -> tuple[str, ...]:
    """A path's segments for matching: parameters are all alike, and a trailing slash doesn't count."""
    return tuple("{}" if "{" in s else s for s in path.split("/") if s)[: MAX_SEGMENTS + 1]


class LineOwners:
    """The innermost symbol holding each line of a file, in one sweep, so many clients in a file stay linear."""

    def __init__(self, symbols: Iterable[tuple[int, int, str]], fallback: str) -> None:
        self.fallback = fallback
        ordered = sorted(symbols, key=lambda s: (s[0], -s[1], s[2]))
        self.starts: list[int] = []
        self.owners: list[str | None] = []
        stack: list[tuple[int, str]] = []
        last = max((s[1] for s in ordered), default=0)
        at = 0
        for line in range(1, last + 1):
            while stack and stack[-1][0] < line:
                stack.pop()
            changed = False
            while at < len(ordered) and ordered[at][0] <= line:
                if ordered[at][1] >= line:
                    stack.append((ordered[at][1], ordered[at][2]))
                    changed = True
                at += 1
            owner = stack[-1][1] if stack else None
            if changed or not self.owners or self.owners[-1] != owner:
                self.starts.append(line)
                self.owners.append(owner)
        self.last = last

    def at(self, line: int) -> str:
        if line < 1 or line > self.last or not self.starts:
            return self.fallback
        low, high = 0, len(self.starts)
        while high - low > 1:
            middle = (low + high) // 2
            if self.starts[middle] <= line:
                low = middle
            else:
                high = middle
        return self.owners[low] or self.fallback


def owners_by_file(nodes: Iterable[object]) -> dict[str, list[tuple[int, int, str]]]:
    """Functions and methods with their line spans, by file."""
    found: dict[str, list[tuple[int, int, str]]] = {}
    for node in nodes:
        kind = getattr(node, "kind", "")
        if kind not in {"function", "method"}:
            continue
        meta = getattr(node, "metadata", {})
        line, end = meta.get("line"), meta.get("end_line")
        if isinstance(line, int) and isinstance(end, int):
            found.setdefault(getattr(node, "path", ""), []).append(
                (line, end, getattr(node, "node_id", ""))
            )
    return found


@dataclass
class Server:
    """An app, router or blueprint: something routes hang off."""

    key: tuple[str, str]
    framework: str
    is_router: bool
    prefix: str = ""
    line: int = 0
    prefix_unknown: bool = False


@dataclass
class Mount:
    parent: tuple[str, str]
    child: tuple[str, str]
    prefix: str | None  # ``None`` when the call gives none
    line: int
    replaces: bool = False  # Flask: a prefix given when registering replaces the blueprint's own
    prefix_unknown: bool = False


@dataclass
class Prefix:
    text: str
    unknown: bool = False
    mounted: bool = True


@dataclass
class RawRoute:
    method: str
    path: str
    framework: str
    file: str
    line: int
    handler: str | None = None
    handler_name: str | None = None
    handler_lines: tuple[int, int] | None = None
    catch_all: bool = False
    extra: dict[str, object] = field(default_factory=dict)


@dataclass
class RawClient:
    file: str
    line: int
    method: str
    framework: str
    caller: str
    parts: Parts | None  # ``None`` when nothing can be said about the URL
    base: Parts | None = None


def with_base(base: Parts | None, parts: Parts | None) -> Parts | None:
    """A client's configured base joined to a call's URL. An absolute URL ignores the base."""
    if parts is None:
        return None
    pieces = merged(parts)
    if not base or not pieces:
        return pieces
    first = pieces[0]
    if isinstance(first, str) and first[:8].lower().startswith(("http://", "https://", "//")):
        return pieces
    if isinstance(first, Param):  # the whole URL is unknown, and the base doesn't change that
        return pieces
    joined = merged(base)
    if joined and isinstance(joined[-1], str):
        joined[-1] = joined[-1].rstrip("/")
    tail = pieces[0] if isinstance(pieces[0], str) else ""
    if not tail.startswith("/"):
        joined.append("/")
    return merged([*joined, *pieces])


def expand_prefixes(
    servers: dict[tuple[str, str], Server], mounts: list[Mount]
) -> tuple[dict[tuple[str, str], list[Prefix]], list[str]]:
    """Every path prefix each server is reached by, walking down from those nothing mounts. Cycles end; fan-out is capped."""
    children: dict[tuple[str, str], list[Mount]] = {}
    mounted: set[tuple[str, str]] = set()
    for mount in sorted(mounts, key=lambda m: (m.parent, m.child, m.line)):
        if mount.child in servers:
            children.setdefault(mount.parent, []).append(mount)
            mounted.add(mount.child)
    notes: list[str] = []
    found: dict[tuple[str, str], list[Prefix]] = {}
    capped: set[tuple[str, str]] = set()
    steps = 0
    cycle = False

    def add(key: tuple[str, str], prefix: Prefix) -> bool:
        have = found.setdefault(key, [])
        if any(p.text == prefix.text for p in have):
            return False
        if len(have) >= MAX_PREFIXES:
            capped.add(key)
            return False
        have.append(prefix)
        return True

    def walk(root: tuple[str, str], is_mounted: bool) -> None:
        nonlocal steps, cycle
        server = servers[root]
        start = Prefix(
            join_paths(server.prefix) if server.prefix else "",
            server.prefix_unknown,
            is_mounted,
        )
        add(root, start)
        stack = [(root, start, 0, frozenset({root}))]
        while stack and steps < MAX_ROUTES * 4:
            key, prefix, depth, path = stack.pop()
            if depth >= MAX_MOUNT_DEPTH:
                continue
            for mount in children.get(key, ()):
                steps += 1
                if mount.child in path:
                    cycle = True
                    continue
                child = servers[mount.child]
                own = "" if mount.replaces and mount.prefix is not None else child.prefix
                text = join_paths(prefix.text, mount.prefix or "", own)
                next_prefix = Prefix(
                    "" if text == "/" else text,
                    prefix.unknown or mount.prefix_unknown or child.prefix_unknown,
                    prefix.mounted,
                )
                if add(mount.child, next_prefix):
                    stack.append((mount.child, next_prefix, depth + 1, path | {mount.child}))

    for key in sorted(k for k in servers if k not in mounted):
        walk(key, not servers[key].is_router)
    for key in sorted(servers):  # only reachable through a cycle
        if key not in found:
            walk(key, False)
    if capped:
        notes.append(
            f"{len(capped)} routers are mounted in over {MAX_PREFIXES} ways, so only the first {MAX_PREFIXES} are listed"
        )
    if cycle:
        notes.append("some routers include each other, which can't be served; the cycle was cut")
    return found, notes
