"""What the metric analyzers share: how to read an untrusted file, and how to name a file's role."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path, PurePosixPath

from ananke.plexus.intel.context import MAX_TEXT_BYTES, RepoContext

LANGUAGES = {
    ".py": "python", ".pyi": "python", ".ipynb": "python",
    ".ts": "typescript", ".tsx": "typescript", ".mts": "typescript", ".cts": "typescript",
    ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".sql": "sql", ".go": "go", ".rs": "rust", ".java": "java", ".kt": "kotlin", ".kts": "kotlin",
    ".rb": "ruby", ".php": "php", ".cs": "csharp", ".c": "c", ".h": "c", ".cc": "cpp", ".cpp": "cpp",
    ".hpp": "cpp", ".swift": "swift", ".scala": "scala", ".sh": "shell", ".bash": "shell",
    ".ps1": "powershell", ".r": "r", ".lua": "lua", ".dart": "dart", ".vue": "vue",
    ".svelte": "svelte", ".astro": "astro", ".html": "html", ".css": "css", ".scss": "css",
    ".tf": "terraform",
}  # fmt: skip
# Data for a repository's own tests, not code it runs: a fixture's manifest isn't a dependency.
FIXTURE_DIRS = {"fixtures", "__fixtures__", "testdata", "__mocks__", "__snapshots__"}
TEST_DIRS = {"tests", "test", "__tests__", "spec", "e2e", "acceptance"}
SCRIPT_SUFFIXES = (".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs")


def language_of(path: str) -> str | None:
    """The language a file's extension says, or ``None`` for data, docs and minified bundles."""
    name = path.rsplit("/", 1)[-1].lower()
    if name.endswith((".min.js", ".min.css")):
        return None
    return LANGUAGES.get(PurePosixPath(name).suffix)


def is_fixture(path: str) -> bool:
    return any(part in FIXTURE_DIRS for part in path.split("/")[:-1])


def is_test_file(path: str) -> bool:
    """A test by its name or folder. Under ``src`` only ``tests`` counts: ``src/app/spec`` can be real code."""
    parts = path.split("/")
    name = parts[-1]
    if (name.startswith("test_") and name.endswith(".py")) or name.endswith("_test.py"):
        return True
    if name == "conftest.py" or name.endswith("_test.go"):
        return True
    if name.endswith(SCRIPT_SUFFIXES) and (
        ".test." in name or ".spec." in name or name.startswith(("test-", "test_"))
    ):
        return True
    if "src" in parts[:-1]:
        return any(p in {"tests", "__tests__"} for p in parts[parts.index("src") + 1 : -1])
    return any(p in TEST_DIRS for p in parts[:-1])


def deepest_folder(path: str, folders: Iterable[str]) -> str | None:
    """Of these folders, the deepest that holds ``path``. ``.`` holds everything, and is the last resort."""
    best = None
    for folder in folders:
        if folder == ".":
            best = best or "."
        elif path.startswith(f"{folder}/") and (
            best in {None, "."} or len(folder) > len(best or "")
        ):
            best = folder
    return best


def is_inside(root: Path, path: str) -> bool:
    """Whether a repository path stays inside it. A symlink to a file elsewhere must not be read."""
    try:
        return (root / path).resolve().is_relative_to(root)
    except (OSError, RuntimeError, ValueError):
        return False


def read_bytes(ctx: RepoContext, path: str, cap: int = MAX_TEXT_BYTES) -> bytes | None:
    """A regular file inside the repository, at most ``cap`` bytes, or ``None``."""
    target = ctx.root / path
    if not is_inside(ctx.root, path):
        return None
    try:
        if not target.is_file() or target.stat().st_size > cap:
            return None
        return target.read_bytes()
    except OSError:
        return None


def read_text(ctx: RepoContext, path: str, cap: int = MAX_TEXT_BYTES) -> str | None:
    raw = read_bytes(ctx, path, cap)
    return None if raw is None else raw.decode("utf-8", "replace")


def count_loc(text: str) -> int:
    """Lines that aren't blank."""
    return sum(1 for line in text.splitlines() if line.strip())


def some(items: list[str], shown: int = 5) -> str:
    """A list short enough for a sentence."""
    head = ", ".join(items[:shown])
    return f"{head} and {len(items) - shown} more" if len(items) > shown else head
