"""Spending fewer tokens on a repository: a small map first, then the right tool for each question.

Three external tools do parts of this well, and each is optional:

- **Serena** answers symbol questions from a language server, so it reads the code as it is now. It is GPL-3.0-or-later, so
  Ananke only ever runs it as a separate process and never imports or bundles it.
- **repomix** packs chosen files, compressed, for a question that needs a lot of code at once.
- **Graphify** finds structure across a repository or several. Its graph is a snapshot that something has to refresh.

The map, ``outline``, ``symbol`` and ``callers`` need none of them. They come from Ananke's own graph, and each one first checks a
fingerprint of the repository, uncommitted edits included, and rebuilds if it changed. So there's no watcher to keep running, and
no answer from before your last edit.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from ananke.plexus.graph.models import GraphNode, GraphSnapshot
from ananke.plexus.graph.service import build_graph

SERENA_SPEC = "serena-agent==2.1.0"
REPOMIX_SPEC = "repomix==0.5.0"
DEFAULT_BUDGET = 1500
MAX_BUDGET = 20_000
PACK_TIMEOUT = 600
# Generated and vendored text dwarfs source in a pack, and in Graphify's communities.
NOISE = [
    "**/*.min.js",
    "**/*.lock",
    "**/package-lock.json",
    "**/*.ipynb",
    "**/dist/**",
    "**/node_modules/**",
]
SOURCE_SUFFIXES = (".py", ".ts", ".tsx", ".js", ".jsx", ".java", ".go", ".rs", ".sql")
# What each measured best at, on real repositories; see docs/guides/saving-tokens.md.
ROUTES = (
    ("Where do I start in this repository?", "ananke context map", "under 2,000 tokens"),
    (
        "What's in this big file?",
        "ananke context outline FILE",
        "about 290 tokens, against 8,300 to read it",
    ),
    (
        "Show me one function",
        "ananke context symbol NAME",
        "about 1,600 tokens from a big file, against 8,300 to read it",
    ),
    (
        "Who calls this?",
        "ananke context callers NAME",
        "about 130 tokens, and it flags text matches it couldn't resolve",
    ),
    (
        "What's in this small file?",
        "Read it",
        "under about 1,000 tokens, reading costs about the same",
    ),
    (
        "I need many files at once",
        "ananke context pack --include ...",
        "68% fewer on source; 5% if docs are left in",
    ),
    (
        "How does the architecture hang together?",
        "Graphify report, after `graphify update`",
        "5,000 to 21,000 tokens",
    ),
    (
        "How do these repositories connect?",
        "graphify merge-graphs",
        "no other tool here spans repositories",
    ),
    (
        "I need exact references, another language, or a rename",
        "Serena, if connected",
        "a language server is more exact, and covers more languages",
    ),
)
BLOCK_START = "<!-- ananke-context:start -->"
BLOCK_END = "<!-- ananke-context:end -->"
PROJECT_BLOCK = f"""{BLOCK_START}
## Reading this repository

Save tokens: use these before reading whole files. They always reflect the current code, including uncommitted edits.

- `ananke context outline FILE`: what's in a file
- `ananke context symbol NAME`: one function or class, with its source
- `ananke context callers NAME`: what calls it
- `ananke context map`: the whole repository, small. Start here if you haven't seen it

Read a file directly when it's small, or when you need most of it.
{BLOCK_END}
"""
# Where each assistant reads project instructions, and what its file needs at the top.
CLIENTS = {
    "claude-code": ("CLAUDE.md", ""),
    "codex": ("AGENTS.md", ""),
    "copilot": (".github/copilot-instructions.md", ""),
    "kiro": (".kiro/steering/ananke-context.md", "---\ninclusion: always\n---\n\n"),
    "cursor": (
        ".cursor/rules/ananke-context.mdc",
        "---\ndescription: Reading this repository\nalwaysApply: true\n---\n\n",
    ),
    "amazon-q": (".amazonq/rules/ananke-context.md", ""),
}
SESSION_HOOK = 'ananke context map --project "$CLAUDE_PROJECT_DIR" 2>/dev/null || true'
SYMBOL_KINDS = ("function", "method", "class")
MAX_BODY_LINES = 400
MAX_BODIES = 3
MAX_MENTIONS = 12
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*")


class ContextError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def estimate_tokens(text: str) -> int:
    """About right for English and code, with no dependency. Use it for budgets, not for billing."""
    return (len(text) + 3) // 4


def _git(root: Path, *args: str) -> str | None:
    try:
        done = subprocess.run(  # noqa: S603 - fixed argv
            ["git", "-C", str(root), *args],  # noqa: S607 - git from PATH, like the rest of the harness
            capture_output=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout.decode("utf-8", "replace") if done.returncode == 0 else None


def source_state(root: Path) -> str:
    """Changes whenever tracked or new files change: the commit, plus what's different from it."""
    head = _git(root, "rev-parse", "HEAD")
    changed = _git(root, "status", "--porcelain=v1", "--untracked-files=all")
    if head is None or changed is None:
        stamps = sorted(
            f"{p.relative_to(root)}:{p.stat().st_mtime_ns}"
            for p in root.rglob("*")
            if p.is_file() and p.suffix in SOURCE_SUFFIXES and ".ananke" not in p.parts
        )
        material = "\n".join(stamps)
    else:
        # the same dirty file list can hide new edits, so dirty files count by content
        dirty = []
        for line in changed.splitlines():
            path = line[3:].strip().strip('"')
            target = root / path
            if path.startswith(".ananke/") or not target.is_file():
                continue
            info = target.stat()
            if info.st_size > 1_000_000:  # a big data file is only worth a stat on every question
                dirty.append(f"{path}:{info.st_size}:{info.st_mtime_ns}")
            else:
                dirty.append(f"{path}:{hashlib.sha256(target.read_bytes()).hexdigest()}")
        material = head + "\n".join(sorted(dirty))
    return hashlib.sha256(material.encode()).hexdigest()[:16]


def _cap(rows: list[str], heading: str, budget_left: int) -> list[str]:
    """As many rows as fit, and a count of the rest, so a cut map says it was cut."""
    out = [heading]
    used = estimate_tokens(heading)
    for i, row in enumerate(rows):
        cost = estimate_tokens(row) + 1
        if used + cost > budget_left:
            out.append(f"- ... and {len(rows) - i} more")
            break
        out.append(row)
        used += cost
    return out


def _render_map(root: Path, report: dict[str, Any], budget: int, state: str) -> str:
    s = report["sections"]
    overview = s["overview"]
    lines = [f"# {root.name}: repository map", ""]
    languages = ", ".join(
        f"{k} {v}"
        for k, v in sorted(overview.get("languages", {}).items(), key=lambda x: -x[1])[:5]
    )
    lines.append(f"{overview.get('files', 0)} files, {overview.get('loc', 0)} lines. {languages}.")
    if overview.get("source_roots"):
        lines.append("Source roots: " + ", ".join(overview["source_roots"][:6]) + ".")
    # each part gets a share, so one large section can't push the others out
    share = max(120, (budget - estimate_tokens("\n".join(lines)) - 60) // 5)
    parts: list[tuple[str, list[str]]] = []
    parts.append(
        (
            "## Components",
            [
                f"- {c['name']} ({c['kind']}, {c.get('language') or 'mixed'}): {c['path']}, {c.get('files', 0)} files"
                for c in overview.get("components", [])
            ],
        )
    )
    api = s["api"]
    parts.append(
        (
            "## Routes",
            [
                f"- {r['method']} {r['path']} -> {str(r.get('handler', '')).split(':', 1)[-1]}"
                for r in api.get("routes", [])
            ],
        )
    )
    data = s["data"]
    parts.append(
        (
            "## Tables",
            [
                f"- {t['name']} ({len(t.get('columns', []))} columns): {t.get('evidence', '')}"
                for t in data.get("tables", [])
            ],
        )
    )
    quality = s["quality"]
    parts.append(
        (
            "## Largest files",
            [
                f"- {f['path']}: {f.get('loc', 0)} lines, {f.get('functions', 0)} functions"
                for f in sorted(quality.get("files", []), key=lambda f: -f.get("loc", 0))
            ],
        )
    )
    parts.append(
        (
            "## Most complex",
            [
                f"- {str(h['symbol']).split(':', 1)[-1]} (complexity {h['complexity']}): {h['path']}:{h['line']}"
                for h in quality.get("hotspots", [])
            ],
        )
    )
    for heading, rows in parts:
        if rows:
            lines += ["", *_cap(rows, heading, share)]
    lines += [
        "",
        f"Map of source state {state}. It rebuilds when the sources change: `ananke context map`.",
        "Next: Serena for symbols in big files, grep for callers, `ananke context pack --include` for many files.",
    ]
    return "\n".join(lines) + "\n"


def _prepare_cache(folder: Path) -> None:
    """A folder that keeps itself out of Git, so nobody's .gitignore has to change."""
    folder.mkdir(parents=True, exist_ok=True)
    marker = folder / ".gitignore"
    if not marker.exists():
        marker.write_text("*\n", encoding="utf-8")


def repo_map(root: Path, *, budget: int = DEFAULT_BUDGET, refresh: bool = False) -> dict[str, Any]:
    """The smallest useful picture of a repository, rebuilt only when its sources change."""
    if not 200 <= budget <= MAX_BUDGET:
        raise ContextError("INVALID_REQUEST", f"budget must be 200 to {MAX_BUDGET}, not {budget}")
    root = root.resolve()
    state = source_state(root)
    cache = root / ".ananke" / "context" / f"map-{budget}.json"
    if not refresh and cache.is_file():
        try:
            saved = json.loads(cache.read_text(encoding="utf-8"))
            if saved.get("source_state") == state:
                return {**saved, "cached": True}
        except ValueError:
            pass
    from ananke.plexus.intel import analyze

    text = _render_map(root, analyze(root), budget, state)
    result = {
        "source_state": state,
        "budget": budget,
        "tokens_estimate": estimate_tokens(text),
        "map": text,
    }
    try:
        _prepare_cache(cache.parent)
        cache.write_text(json.dumps(result), encoding="utf-8")
    except OSError:
        pass  # a read-only checkout still gets its map
    return {**result, "cached": False}


def fresh_graph(root: Path) -> GraphSnapshot:
    """The code graph for the repository as it is now, rebuilt only if its fingerprint changed."""
    root = root.resolve()
    state = source_state(root)
    cache = root / ".ananke" / "context" / "graph.json"
    if cache.is_file():
        try:
            saved = json.loads(cache.read_text(encoding="utf-8"))
            if saved.get("source_state") == state:
                return GraphSnapshot.model_validate(saved["graph"])
        except (ValueError, KeyError):
            pass  # a damaged cache is only a slower answer
    graph = build_graph(root)
    try:
        _prepare_cache(cache.parent)
        cache.write_text(
            json.dumps({"source_state": state, "graph": graph.model_dump(mode="json")}),
            encoding="utf-8",
        )
    except OSError:
        pass  # a read-only checkout still gets its answer
    return graph


def _inside(root: Path, path: str) -> str:
    """A repository-relative path, refusing anything outside the repository."""
    resolved = os.path.realpath(os.path.join(root, path))
    base = os.path.realpath(root)
    if not resolved.startswith(base + os.sep):
        raise ContextError("PATH_ESCAPE", f"{path} is outside the repository")
    return os.path.relpath(resolved, base).replace(os.sep, "/")


def _where(node: GraphNode) -> str:
    line, cell = node.metadata.get("line"), node.metadata.get("cell")
    return f"{node.path}#cell={cell}:{line}" if cell else f"{node.path}:{line}"


def _order(node: GraphNode) -> tuple[int, int]:
    return (_int(node.metadata.get("cell")), _int(node.metadata.get("line")))


def _int(value: object) -> int:
    return value if isinstance(value, int) else 0


def outline(root: Path, path: str, *, signatures: bool = False) -> dict[str, Any]:
    """What's in a file, for a fraction of the cost of reading it."""
    root = root.resolve()
    rel = _inside(root, path)
    target = root / rel
    if not target.is_file():
        raise ContextError("NOT_FOUND", f"{path} isn't a file in this repository")
    nodes = [n for n in fresh_graph(root).nodes if n.path == rel and n.kind in SYMBOL_KINDS]
    cost = estimate_tokens(target.read_text(encoding="utf-8", errors="replace"))
    if not nodes:
        raise ContextError(
            "NO_SYMBOLS",
            f"{path} has no symbols this can read, so read it directly (about {cost} tokens)",
        )
    rows = []
    for n in sorted(nodes, key=_order):
        label = (
            n.metadata.get("signature") if signatures and n.metadata.get("signature") else n.name
        )
        span = f"{n.metadata.get('line')}-{n.metadata.get('end_line')}"
        rows.append(f"{span} {n.kind} {label}")
    text = f"{rel} (about {cost} tokens to read)\n" + "\n".join(rows) + "\n"
    return {"path": rel, "read_tokens": cost, "symbols": len(nodes), "text": text}


def _matches(graph: GraphSnapshot, name: str, path: str | None) -> list[GraphNode]:
    if _IDENTIFIER.fullmatch(name) is None:
        raise ContextError(
            "INVALID_REQUEST", f"{name!r} isn't a symbol name: try add or Cart.total"
        )
    found = [
        n
        for n in graph.nodes
        if n.kind in SYMBOL_KINDS
        and (n.name == name or n.name.endswith(f".{name}"))
        and (path is None or n.path == path)
    ]
    return sorted(found, key=lambda n: (n.path, _order(n)))


def symbol(root: Path, name: str, *, path: str | None = None) -> dict[str, Any]:
    """One function, method or class with its source. Many matches list their locations instead, to choose from."""
    root = root.resolve()
    rel = _inside(root, path) if path else None
    found = _matches(fresh_graph(root), name, rel)
    if not found:
        raise ContextError("NOT_FOUND", f"no symbol named {name}" + (f" in {rel}" if rel else ""))
    parts = []
    if len(found) > MAX_BODIES:
        rows = "\n".join(f"{_where(n)} {n.kind} {n.name}" for n in found[:50])
        more = f"\n... and {len(found) - 50} more" if len(found) > 50 else ""
        text = f"{len(found)} symbols match {name}. Narrow it with --path, or Class.method:\n{rows}{more}\n"
        return {"name": name, "matches": len(found), "text": text}
    for n in found:
        start, end = _int(n.metadata.get("line")), _int(n.metadata.get("end_line"))
        heading = f"{_where(n)}-{end} {n.kind} {n.name}"
        if n.metadata.get("cell"):  # a notebook's lines are per cell, and the file is JSON
            parts.append(f"{heading}\n(in a notebook: open that cell)")
            continue
        lines = (root / n.path).read_text(encoding="utf-8", errors="replace").splitlines()
        body = lines[start - 1 : min(end, start - 1 + MAX_BODY_LINES)]
        cut = (
            f"\n... cut at {MAX_BODY_LINES} lines of {end - start + 1}"
            if end - start + 1 > MAX_BODY_LINES
            else ""
        )
        parts.append(f"{heading}\n" + "\n".join(body) + cut)
    return {"name": name, "matches": len(found), "text": "\n\n".join(parts) + "\n"}


def callers(root: Path, name: str, *, path: str | None = None) -> dict[str, Any]:
    """What calls or inherits from a symbol, plus text matches the graph couldn't resolve, so a dynamic use isn't missed."""
    root = root.resolve()
    rel = _inside(root, path) if path else None
    graph = fresh_graph(root)
    found = _matches(graph, name, rel)
    if not found:
        raise ContextError("NOT_FOUND", f"no symbol named {name}" + (f" in {rel}" if rel else ""))
    ids = {n.node_id for n in found}
    edges = sorted(
        (e for e in graph.edges if e.target in ids and e.kind in ("calls", "inherits")),
        key=lambda e: (e.source_location, e.source),
    )
    resolved = [f"{e.source_location} {e.source.split(':', 2)[-1]} ({e.kind})" for e in edges]
    # the graph keeps the first call from each caller, and can't see dynamic use: text finds the rest
    short = name.rsplit(".", 1)[-1]
    seen = {e.source_location for e in edges} | {_where(n) for n in found}
    listed = _git(root, "grep", "-n", "-w", "-I", "--untracked", "--", short) or ""
    mentions = []
    for line in listed.splitlines():
        file, _, rest = line.partition(":")
        number = rest.split(":", 1)[0]
        if f"{file}:{number}" not in seen:
            mentions.append(f"{file}:{number}")
    rows = [f"{len(edges)} resolved, from the code graph:", *resolved]
    if mentions:
        rows += [
            f"{len(mentions)} other mentions of {short}, not resolved (text only):",
            *mentions[:MAX_MENTIONS],
        ]
        if len(mentions) > MAX_MENTIONS:
            rows.append(f"... and {len(mentions) - MAX_MENTIONS} more: git grep -n -w {short}")
    return {
        "name": name,
        "resolved": len(edges),
        "other_mentions": len(mentions),
        "text": "\n".join(rows) + "\n",
    }


def _hook_entries(settings: dict[str, Any]) -> list[Any]:
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return []
    entries = hooks.get("SessionStart")
    return entries if isinstance(entries, list) else []


def _load_settings(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        # never overwrite a file we can't read: it may hold someone's settings
        raise ContextError(
            "SETTINGS_UNREADABLE", f"{path} isn't valid JSON, so it was left alone: {exc}"
        ) from exc
    if not isinstance(loaded, dict):
        raise ContextError(
            "SETTINGS_UNREADABLE", f"{path} isn't a JSON object, so it was left alone"
        )
    return loaded


def _detect_clients(root: Path) -> list[str]:
    """Clients this project shows signs of. With none, Claude Code, plus AGENTS.md, which many others read."""
    found = [
        name
        for name, marker in (
            ("claude-code", ".claude"),
            ("claude-code", "CLAUDE.md"),
            ("codex", "AGENTS.md"),
            ("codex", ".codex"),
            ("copilot", ".github/copilot-instructions.md"),
            ("kiro", ".kiro"),
            ("cursor", ".cursor"),
            ("amazon-q", ".amazonq"),
        )
        if (root / marker).exists()
    ]
    return sorted(set(found)) or ["claude-code", "codex"]


def _choose_clients(root: Path, clients: list[str] | None) -> list[str]:
    if not clients:
        return _detect_clients(root)
    if "all" in clients:
        return sorted(CLIENTS)
    unknown = sorted(set(clients) - set(CLIENTS))
    if unknown:
        raise ContextError(
            "INVALID_REQUEST",
            f"unknown client {', '.join(unknown)}: use {', '.join(sorted(CLIENTS))} or all",
        )
    return sorted(set(clients))


def _is_our_hook(entry: object) -> bool:
    inner = entry.get("hooks", []) if isinstance(entry, dict) else []
    return any(isinstance(h, dict) and h.get("command") == SESSION_HOOK for h in inner)


def enable(
    root: Path, *, clients: list[str] | None = None, session_map: bool = True, dry_run: bool = False
) -> dict[str, Any]:
    """Make a project use the graph by default: short instructions where each assistant looks for them.

    Claude Code also gets the map at the start of each session, through its hook. The others are told to ask for it.
    """
    root = root.resolve()
    chosen = _choose_clients(root, clients)
    changes = []
    for name in chosen:
        relative, header = CLIENTS[name]
        target = root / relative
        current = _read(target)
        if BLOCK_START in current:
            continue
        changes.append(f"{relative}: add a short block on reading this repository ({name})")
        if dry_run:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if current.strip():
            target.write_text(current.rstrip() + "\n\n" + PROJECT_BLOCK, encoding="utf-8")
        else:
            target.write_text(header + PROJECT_BLOCK, encoding="utf-8")
    if session_map and "claude-code" in chosen:
        settings_path = root / ".claude" / "settings.json"
        settings = _load_settings(settings_path)
        if not any(_is_our_hook(entry) for entry in _hook_entries(settings)):
            changes.append(
                ".claude/settings.json: load the map at the start of each session (claude-code)"
            )
            if not dry_run:
                settings.setdefault("hooks", {}).setdefault("SessionStart", []).append(
                    {"hooks": [{"type": "command", "command": SESSION_HOOK}]}
                )
                settings_path.parent.mkdir(parents=True, exist_ok=True)
                settings_path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    return {"clients": chosen, "changes": changes, "dry_run": dry_run, "enabled": True}


def disable(root: Path) -> dict[str, Any]:
    """Remove exactly what ``enable`` added, for every client, and leave everything else."""
    root = root.resolve()
    changes = []
    for relative, header in {v[0]: v[1] for v in CLIENTS.values()}.items():
        target = root / relative
        current = _read(target)
        start, end = current.find(BLOCK_START), current.find(BLOCK_END)
        if start == -1 or end == -1:
            continue
        kept = (
            current[:start].rstrip() + "\n\n" + current[end + len(BLOCK_END) :].lstrip()
        ).strip()
        if kept and kept != header.strip():
            target.write_text(kept + "\n", encoding="utf-8")
        else:
            target.unlink()
        changes.append(f"{relative}: removed the block")
    settings_path = root / ".claude" / "settings.json"
    settings = _load_settings(settings_path)
    entries = _hook_entries(settings)
    kept_entries = []
    for entry in entries:
        if not _is_our_hook(entry):
            kept_entries.append(entry)
            continue
        left = [
            h
            for h in entry["hooks"]
            if not (isinstance(h, dict) and h.get("command") == SESSION_HOOK)
        ]
        if left:
            kept_entries.append({**entry, "hooks": left})
    if json.dumps(kept_entries) != json.dumps(entries):
        hooks = settings["hooks"]
        if kept_entries:
            hooks["SessionStart"] = kept_entries
        else:
            del hooks["SessionStart"]
            if not hooks:
                del settings["hooks"]
        if settings:
            settings_path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
        else:
            settings_path.unlink()
        changes.append(".claude/settings.json: removed the hook")
    return {"changes": changes, "enabled": False}


def _tool(command: str) -> str | None:
    return shutil.which(command)


def _graphify_state(root: Path) -> dict[str, Any]:
    graph = root / "graphify-out" / "graph.json"
    report: dict[str, Any] = {"installed": bool(_tool("graphify")), "has_graph": graph.is_file()}
    if graph.is_file():
        built = graph.stat().st_mtime
        newest = 0.0
        tracked = _git(root, "ls-files", "-z")
        for name in (tracked or "").split("\0"):
            target = root / name
            if name and name.endswith(SOURCE_SUFFIXES) and target.is_file():
                newest = max(newest, target.stat().st_mtime)
        report["fresh"] = newest <= built
    return report


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def status(root: Path) -> dict[str, Any]:
    """Which tools are ready, what each is for, and how to get the missing ones. Runs none of them."""
    root = root.resolve()
    uvx = bool(_tool("uvx"))
    serena_project = (root / ".serena" / "project.yml").is_file()
    return {
        "tools": {
            "map": {
                "ready": True,
                "for": "a small starting picture that can't go stale",
                "licence": "built in",
            },
            "graph": {
                "ready": True,
                "for": "outline, symbol and callers; current with every edit",
                "licence": "built in",
                "enabled_for": sorted(
                    n for n, (f, _) in CLIENTS.items() if BLOCK_START in _read(root / f)
                ),
            },
            "serena": {
                "ready": uvx,
                "for": "symbols in big files, from the code as it is now",
                "licence": "GPL-3.0-or-later; run as a separate process, never bundled",
                "project_set_up": serena_project,
                "setup": serena_setup(root),
            },
            "repomix": {
                "ready": bool(_tool("repomix")) or uvx,
                "for": "many files at once, compressed",
                "licence": "MIT",
                "setup": "pip install ananke-plexus[context]",
            },
            "graphify": {
                **_graphify_state(root),
                "ready": bool(_tool("graphify")),
                "for": "structure across a repository or several; a snapshot, refreshed by `graphify update`",
                "licence": "Apache-2.0",
                "setup": "pip install graphifyy && graphify update .",
            },
        },
        "routes": [{"question": q, "use": u, "why": w} for q, u, w in ROUTES],
    }


def serena_setup(root: Path) -> dict[str, Any]:
    """What connects Serena to Claude Code, for a person to run. Ananke doesn't change a client's settings."""
    server = [
        "uvx", "--python", "3.12", "--from", SERENA_SPEC, "serena", "start-mcp-server", "--context", "claude-code",
        "--project-from-cwd", "--enable-web-dashboard", "false", "--open-web-dashboard", "false",
    ]  # fmt: skip
    return {
        "mcp_server": {
            "command": server[0],
            "args": server[1:],
            "env": {"SERENA_USAGE_REPORTING": "false"},
        },
        "add_to_claude_code": "claude mcp add --scope user --env SERENA_USAGE_REPORTING=false serena -- "
        + " ".join(server),
        # without a project file, language servers stay empty and lookups quietly find nothing
        "each_repository": f"uvx --python 3.12 --from {SERENA_SPEC} serena project create {root} --language python",
    }


def pack(
    root: Path,
    *,
    include: list[str],
    compress: bool = True,
    output: Path | None = None,
) -> dict[str, Any]:
    """Pack chosen files with repomix. ``include`` is required: unscoped, docs drown the source and it saves almost nothing."""
    include = [pattern for pattern in include if pattern.strip()]
    if not include:
        raise ContextError(
            "INVALID_REQUEST", "say which files to pack, such as --include 'src/**/*.py'"
        )
    root = root.resolve()
    command = [_tool("repomix")] if _tool("repomix") else None
    if command is None and _tool("uvx"):
        command = ["uvx", "-q", "--python", "3.12", REPOMIX_SPEC]
    if command is None:
        raise ContextError(
            "TOOL_MISSING", "repomix isn't available: pip install ananke-plexus[context]"
        )
    target = (output or root / ".ananke" / "context" / "pack.xml").resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    argv = [
        *(c for c in command if c),
        str(root),
        "--output", str(target),
        "--include", ",".join(include),
        "--ignore", ",".join(NOISE),
        "--remove-empty-lines",
        *(["--compress"] if compress else []),
    ]  # fmt: skip
    try:
        done = subprocess.run(  # noqa: S603 - argv list, no shell
            argv,
            capture_output=True,
            timeout=PACK_TIMEOUT,
            check=False,
            cwd=root,
            env=os.environ.copy(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ContextError("TOOL_FAILED", f"repomix didn't finish: {exc}") from exc
    if done.returncode != 0 or not target.is_file():
        tail = done.stderr.decode("utf-8", "replace").strip().splitlines()[-1:] or ["no output"]
        raise ContextError("TOOL_FAILED", f"repomix failed: {tail[0][:300]}")
    text = target.read_text(encoding="utf-8", errors="replace")
    # its secret check can drop a whole file on a false positive, and still exit 0
    log = done.stdout.decode("utf-8", "replace") + done.stderr.decode("utf-8", "replace")
    dropped = re.search(r"Detected (\d+) suspicious files?", log)
    excluded = int(dropped.group(1)) if dropped else 0
    result: dict[str, Any] = {
        "path": str(target),
        "tokens_estimate": estimate_tokens(text),
        "compressed": compress,
        "include": include,
        "excluded": excluded,
        "complete": excluded == 0,
    }
    if excluded:
        result["warning"] = (
            f"repomix left out {excluded} file(s) as possible secrets, which can be false positives. "
            "This pack is incomplete: read them directly, after checking them."
        )
    return result


def benchmark(root: Path) -> dict[str, Any]:
    """What a first look costs here, each way. Estimates, so the comparison is what matters."""
    root = root.resolve()
    tracked = _git(root, "ls-files", "-z")
    names = (
        tracked.split("\0")
        if tracked
        else [str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()]
    )
    total = 0
    biggest: tuple[int, str] = (0, "")
    for name in names:
        target = root / name
        if (
            name.endswith(SOURCE_SUFFIXES)
            and target.is_file()
            and target.stat().st_size < 2_000_000
        ):
            cost = estimate_tokens(target.read_text(encoding="utf-8", errors="replace"))
            total += cost
            biggest = max(biggest, (cost, name))
    small = repo_map(root)
    return {
        "all_source": total,
        "map": small["tokens_estimate"],
        "map_saving": round(1 - small["tokens_estimate"] / total, 3) if total else 0.0,
        "biggest_file": {"path": biggest[1], "tokens": biggest[0]},
    }


__all__ = [
    "DEFAULT_BUDGET",
    "ROUTES",
    "ContextError",
    "benchmark",
    "callers",
    "disable",
    "enable",
    "estimate_tokens",
    "fresh_graph",
    "outline",
    "pack",
    "repo_map",
    "serena_setup",
    "source_state",
    "status",
    "symbol",
]
