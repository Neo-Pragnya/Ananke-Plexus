"""Spending fewer tokens on a repository: a small map first, then the right tool for each question.

Three external tools do parts of this well, and each is optional:

- **Serena** answers symbol questions from a language server, so it reads the code as it is now. It is GPL-3.0-or-later, so
  Ananke only ever runs it as a separate process and never imports or bundles it.
- **repomix** packs chosen files, compressed, for a question that needs a lot of code at once.
- **Graphify** finds structure across a repository or several. Its graph is a snapshot that something has to refresh.

The map itself needs none of them. It's built from Ananke's own analysis and rebuilt when the sources change, so it can't
silently go stale, and it stays inside a token budget.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

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
        "Serena get_symbols_overview",
        "97% fewer tokens than reading an 8,000-token file",
    ),
    (
        "Show me one function from a big file",
        "Serena find_symbol",
        "46% fewer on a big file; more than reading a small one",
    ),
    (
        "Who calls this?",
        "grep, then read what matters",
        "10 times fewer tokens than Serena's references",
    ),
    ("What's in this small file?", "Read it", "the tool's overhead costs more than the file"),
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
)


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
    changed = _git(root, "status", "--porcelain=v1", "--untracked-files=normal")
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
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(result), encoding="utf-8")
    except OSError:
        pass  # a read-only checkout still gets its map
    return {**result, "cached": False}


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
        "mcp_server": {"command": server[0], "args": server[1:]},
        "add_to_claude_code": "claude mcp add --scope user serena -- " + " ".join(server),
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
    return {
        "path": str(target),
        "tokens_estimate": estimate_tokens(text),
        "compressed": compress,
        "include": include,
    }


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
    "estimate_tokens",
    "pack",
    "repo_map",
    "serena_setup",
    "source_state",
    "status",
]
