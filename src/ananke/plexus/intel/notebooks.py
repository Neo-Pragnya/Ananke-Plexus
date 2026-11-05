"""What each notebook does, in its own words and from its code, and how notebooks feed each other.

Everything comes from the notebook's text and the graph: headings and the paragraphs under them, what it imports,
the files it writes, the data it reads and writes. No cell is run, and nothing is generated or guessed.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from ananke.plexus.graph.notebook_files import cell_text, read_cells
from ananke.plexus.intel.context import RepoContext

MAX_OUTLINE = 150
MAX_SUMMARY = 420
_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$", re.M)
_NUMBER = re.compile(r"(\d+)")
_MARKUP = re.compile(r"[`*_]|!?\[([^\]]*)\]\([^)]*\)|<[^>]+>")


def natural(path: str) -> list[Any]:
    """Week-2 before Week-10."""
    return [int(part) if part.isdigit() else part.lower() for part in _NUMBER.split(path)]


def _plain(text: str) -> str:
    return " ".join(_MARKUP.sub(lambda m: m.group(1) or "", text).split())


def _first_paragraph(text: str) -> str:
    for block in re.split(r"\n\s*\n", text):
        block = block.strip()
        if not block or block.startswith(("#", "|", "```", "<", "---", "![")):
            continue
        plain = _plain(block)
        return plain if len(plain) <= MAX_SUMMARY else plain[:MAX_SUMMARY].rsplit(" ", 1)[0] + "..."
    return ""


def _outline(cells: list[dict[str, object]]) -> list[dict[str, Any]]:
    outline: list[dict[str, Any]] = []
    for number, cell in enumerate(cells, 1):
        if cell.get("cell_type") != "markdown":
            continue
        text = cell_text(cell)
        matches = list(_HEADING.finditer(text))
        for i, match in enumerate(matches):
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            outline.append(
                {
                    "level": len(match.group(1)),
                    "title": _plain(match.group(2)),
                    "cell": number,
                    "summary": _first_paragraph(text[match.end() : end]),
                }
            )
    return outline


def _language(path: str) -> str:
    return {
        ".py": "python", ".js": "javascript", ".ts": "typescript", ".sql": "sql", ".yaml": "yaml", ".yml": "yaml",
        ".json": "json", ".md": "markdown", ".txt": "text", ".toml": "toml", ".sh": "shell",
    }.get(Path(path).suffix.lower(), "other")  # fmt: skip


def analyze(ctx: RepoContext) -> dict[str, Any]:
    paths = sorted(ctx.with_suffix(".ipynb"), key=natural)
    if not paths:
        return {
            "status": "none",
            "limits": [],
            "notebooks": [],
            "links": [],
            "groups": [],
            "written_total": 0,
        }
    node_path = {n.node_id: n.path for n in ctx.graph.nodes}
    symbols: dict[str, list[str]] = {}
    for node in ctx.graph.nodes:
        if node.kind in {"function", "class"}:
            symbols.setdefault(node.path, []).append(node.name)
    externals: dict[str, set[str]] = {}
    local: dict[str, set[str]] = {}
    reads: dict[str, set[str]] = {}
    writes: dict[str, set[str]] = {}
    for edge in ctx.graph.edges:
        source = node_path.get(edge.source)
        if not source:
            continue
        if edge.kind == "imports":
            target = edge.resolved_target or ""
            if target.startswith("file:"):
                local.setdefault(source, set()).add(target.removeprefix("file:"))
            elif edge.target.startswith("module:"):
                externals.setdefault(source, set()).add(
                    edge.target.removeprefix("module:").split(".")[0]
                )
        elif edge.kind in {"reads", "writes"} and edge.target.startswith("data:"):
            (reads if edge.kind == "reads" else writes).setdefault(source, set()).add(
                edge.target.removeprefix("data:")
            )
    written_by: dict[str, str] = {}
    entries: list[dict[str, Any]] = []
    seen_content: dict[str, str] = {}
    for path in paths:
        cells = read_cells(ctx.root / path)
        # an executed copy or a delivery folder repeats a notebook: outputs aren't compared, only what it says
        content = hashlib.sha256(
            "\0".join(f"{c.get('cell_type')}:{cell_text(c)}" for c in cells).encode(
                "utf-8", "replace"
            )
        ).hexdigest()
        same_as = seen_content.setdefault(content, path) if cells else path
        outline = _outline(cells)
        title = next(
            (o["title"] for o in outline if o["level"] == 1),
            outline[0]["title"] if outline else Path(path).stem,
        )
        purpose = next((o["summary"] for o in outline if o["summary"]), "")
        written = []
        for item in ctx.written.by_notebook.get(path, []):
            written_by.setdefault(item.path, path)
            written.append(
                {
                    "path": item.path,
                    "cell": item.cell,
                    "language": _language(item.path),
                    "lines": item.text.count("\n"),
                    "symbols": sorted(symbols.get(item.path, []))[:40],
                    "on_disk": item.path in ctx.written.on_disk,
                }
            )
        entries.append(
            {
                "path": path,
                "same_as": None if same_as == path else same_as,
                "folder": path.rsplit("/", 1)[0] if "/" in path else ".",
                "title": title,
                "purpose": purpose,
                "cells": {
                    "code": sum(c.get("cell_type") == "code" for c in cells),
                    "markdown": sum(c.get("cell_type") == "markdown" for c in cells),
                },
                "outline": outline[:MAX_OUTLINE],
                "outline_total": len(outline),
                "defines": sorted(symbols.get(path, []))[:60],
                "imports": sorted(externals.get(path, set())),
                "uses": sorted(local.get(path, set())),
                "written": written,
                "reads": sorted(reads.get(path, set())),
                "writes": sorted(writes.get(path, set())),
            }
        )
    links: list[dict[str, str]] = []
    made_data: dict[str, str] = {}
    for entry in entries:
        if entry["same_as"]:
            continue
        for data in entry["writes"]:
            made_data.setdefault(data, entry["path"])
    for entry in entries:
        if entry["same_as"]:
            continue
        for used in entry["uses"]:
            author = written_by.get(used)
            if author and author != entry["path"]:
                links.append(
                    {
                        "from": author,
                        "to": entry["path"],
                        "via": used,
                        "kind": "imports what it writes",
                    }
                )
        for data in entry["reads"]:
            author = made_data.get(data)
            if author and author != entry["path"]:
                links.append(
                    {
                        "from": author,
                        "to": entry["path"],
                        "via": data,
                        "kind": "reads what it writes",
                    }
                )
    groups: dict[str, list[str]] = {}
    for entry in entries:
        groups.setdefault(entry["folder"], []).append(entry["path"])
    limits = list(ctx.written.limits)
    unread = sum(1 for e in entries if not e["cells"]["code"] and not e["cells"]["markdown"])
    if unread:
        limits.append(f"{unread} notebooks couldn't be read")
    limits.append(
        "what's here comes from each notebook's headings and code: no cell was run, and outputs aren't read"
    )
    return {
        "status": "found",
        "limits": limits,
        "notebooks": entries,
        "links": sorted(links, key=lambda k: (natural(k["from"]), natural(k["to"]), k["via"])),
        "groups": [
            {"folder": k, "notebooks": v}
            for k, v in sorted(groups.items(), key=lambda kv: natural(kv[0]))
        ],
        "written_total": sum(len(e["written"]) for e in entries),
    }
