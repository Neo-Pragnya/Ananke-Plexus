"""What every analyzer shares: the repository's files and its code graph, read once."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ananke.plexus.graph.models import GraphSnapshot
from ananke.plexus.graph.notebook_files import WrittenFiles, find_written

SKIP_DIRS = {
    "node_modules", "site-packages", "__pycache__", "dist", "build", "target", "vendor", "venv", "env", "htmlcov", "coverage",
}  # fmt: skip
MAX_TEXT_BYTES = 2_000_000


def _git_files(root: Path) -> list[str] | None:
    """Tracked and untracked but not ignored files, so ``.gitignore`` is respected. ``None`` outside Git."""
    try:
        done = subprocess.run(  # noqa: S603 - fixed argv
            [  # noqa: S607 - git from PATH, like the rest of the harness
                "git",
                "-C",
                str(root),
                "ls-files",
                "-z",
                "--cached",
                "--others",
                "--exclude-standard",
            ],
            capture_output=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    return [p for p in done.stdout.decode("utf-8", "replace").split("\0") if p]


def _walk_files(root: Path) -> list[str]:
    found = []
    for folder, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in SKIP_DIRS]
        found += [
            os.path.relpath(os.path.join(folder, n), root).replace(os.sep, "/") for n in names
        ]
    return found


def _keep(path: str) -> bool:
    parts = path.split("/")
    return not any(p in SKIP_DIRS or (p.startswith(".") and p != ".github") for p in parts[:-1])


@dataclass
class RepoContext:
    root: Path
    files: list[str]
    graph: GraphSnapshot
    check_vulnerabilities: bool = False
    sections: dict[str, Any] = field(default_factory=dict)
    written: WrittenFiles = field(
        default_factory=WrittenFiles
    )  # what notebooks write with %%writefile

    @classmethod
    def open(cls, root: Path, *, check_vulnerabilities: bool = False) -> RepoContext:
        root = root.resolve()
        listed = _git_files(root)
        files = sorted(p for p in (listed if listed is not None else _walk_files(root)) if _keep(p))
        files = [p for p in files if (root / p).is_file()]
        written = find_written(root, [root / p for p in files if p.endswith(".ipynb")])
        files = sorted({*files, *(p for p in written.files if _keep(p))})
        # a graph kept for these sources, rather than a second one. Analysis writes nothing, so it's only read
        from ananke.plexus.context import fresh_graph

        return cls(
            root, files, fresh_graph(root, save=False), check_vulnerabilities, written=written
        )

    def text(self, path: str) -> str | None:
        """A file's text, or ``None`` when it's too big to be source or can't be read."""
        if path in self.written.files:
            return self.written.files[path].text
        target = self.root / path
        try:
            if target.stat().st_size > MAX_TEXT_BYTES:
                return None
            return target.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None

    def with_suffix(self, *suffixes: str) -> list[str]:
        return [p for p in self.files if p.endswith(suffixes)]

    def named(self, *names: str) -> list[str]:
        return [p for p in self.files if p.rsplit("/", 1)[-1] in names]


def not_built(what: str) -> dict[str, Any]:
    """The honest answer from an analyzer that doesn't exist yet."""
    return {"status": "not_built", "limits": [f"{what} isn't analysed yet"]}
