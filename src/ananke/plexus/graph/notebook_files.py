"""Files a notebook writes with ``%%writefile``: read as if they existed, without creating them.

Course and research repositories often keep their real code inside notebook cells, so a folder can look nearly empty
until someone runs it. Nothing here runs a cell or writes a file. A path is relative to the notebook's folder, as it
is when the cell runs. A file really on disk wins, since that's what would be imported.
"""

from __future__ import annotations

import json
import posixpath
from dataclasses import dataclass, field
from pathlib import Path

MAX_NOTEBOOK_BYTES = 50_000_000
MAX_WRITTEN_BYTES = 2_000_000
MAX_WRITTEN_FILES = 5_000
_MAGICS = ("%%writefile", "%%file")


@dataclass(frozen=True)
class WrittenFile:
    path: str
    text: str
    notebook: str
    cell: int


@dataclass
class WrittenFiles:
    files: dict[str, WrittenFile] = field(default_factory=dict)
    by_notebook: dict[str, list[WrittenFile]] = field(default_factory=dict)
    on_disk: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    limits: list[str] = field(default_factory=list)

    def text(self, path: str) -> str | None:
        written = self.files.get(path)
        return None if written is None else written.text


def read_cells(path: Path) -> list[dict[str, object]]:
    """A notebook's cells, or an empty list when it can't be read as one. Outputs are ignored."""
    try:
        if path.stat().st_size > MAX_NOTEBOOK_BYTES:
            return []
        cells = json.loads(path.read_text(encoding="utf-8")).get("cells", [])
    except (OSError, ValueError, AttributeError, RecursionError):
        return []
    return [c for c in cells if isinstance(c, dict)] if isinstance(cells, list) else []


def cell_text(cell: dict[str, object]) -> str:
    raw = cell.get("source", "")
    return "".join(str(part) for part in raw) if isinstance(raw, list) else str(raw)


def _target(first_line: str) -> tuple[str | None, bool]:
    """The path a magic line names, and whether it appends. ``None`` when it isn't one this can read."""
    words = first_line.split()
    if not words or words[0] not in _MAGICS:
        return None, False
    append = False
    name = None
    for word in words[1:]:
        if word in {"-a", "--append"}:
            append = True
        elif word in {"-f", "--force"} or word.startswith("-"):
            continue
        else:
            name = word.strip("'\"")
            break
    return name, append


def find_written(root: Path, notebooks: list[Path]) -> WrittenFiles:
    """Every file these notebooks write. Later cells replace earlier ones, as they would when run."""
    result = WrittenFiles()
    computed = outside = oversized = 0
    collected: dict[str, WrittenFile] = {}
    for notebook in notebooks:
        try:
            rel_notebook = notebook.relative_to(root).as_posix()
        except ValueError:
            continue
        folder = posixpath.dirname(rel_notebook)
        mine: dict[str, WrittenFile] = {}
        for number, cell in enumerate(read_cells(notebook), 1):
            if cell.get("cell_type") != "code":
                continue
            text = cell_text(cell)
            first, _, body = text.lstrip().partition("\n")
            name, append = _target(first)
            if not first.startswith(_MAGICS):
                continue
            if not name or any(mark in name for mark in ("{", "$", "%")):
                computed += 1
                continue
            rel = posixpath.normpath(posixpath.join(folder, name))
            if rel.startswith("../") or rel == ".." or name.startswith(("/", "~")):
                outside += 1
                continue
            if len(body) > MAX_WRITTEN_BYTES:
                oversized += 1
                continue
            earlier = mine.get(rel)
            body = (earlier.text if append and earlier else "") + body
            mine[rel] = WrittenFile(
                rel, body if body.endswith("\n") or not body else body + "\n", rel_notebook, number
            )
        if mine:
            result.by_notebook[rel_notebook] = list(mine.values())
        for rel, written in mine.items():
            other = collected.get(rel)
            if other is not None and other.text != written.text:
                result.conflicts.append(rel)
            collected.setdefault(
                rel, written
            )  # sorted order decides, so the answer doesn't change between runs
    for rel, written in sorted(collected.items()):
        if (root / rel).is_file():
            result.on_disk.append(rel)
        elif len(result.files) < MAX_WRITTEN_FILES:
            result.files[rel] = written
    if len(collected) - len(result.on_disk) > MAX_WRITTEN_FILES:
        result.limits.append(f"only the first {MAX_WRITTEN_FILES} files notebooks write were read")
    if computed:
        result.limits.append(
            f"{computed} writefile cells name a computed path, so they weren't read"
        )
    if outside:
        result.limits.append(
            f"{outside} writefile cells write outside this folder, so they weren't read"
        )
    if oversized:
        result.limits.append(f"{oversized} writefile cells were too large to read")
    if result.conflicts:
        count = len(set(result.conflicts))
        result.limits.append(
            f"{count} files are written differently by different notebooks: the first by path is shown"
        )
    return result


__all__ = [
    "MAX_WRITTEN_BYTES",
    "WrittenFile",
    "WrittenFiles",
    "cell_text",
    "find_written",
    "read_cells",
]
