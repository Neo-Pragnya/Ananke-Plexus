"""Build or serve the Ananke Plexus documentation site fully offline.

The MkDocs source (Markdown + Mermaid diagrams) ships inside the installed package at
``ananke/plexus/_docsite/`` (bundled by ``hatch_build.py``), so ``ananke docs build`` and
``ananke docs serve`` work right after ``pip install ananke-plexus[docs]`` — no PyPI page,
no GitHub Pages, no git checkout required. The large hero PNGs under ``docs/assets/`` are
not bundled (they would multiply the wheel size ~40x); pages that reference one render
with a broken-image icon unless ``--source`` points at a git checkout of the repository,
which is used byte-for-byte instead of the bundled copy.
"""

from __future__ import annotations

import contextlib
import functools
import http.server
import importlib.resources
import shutil
import socket
import tempfile
import threading
import webbrowser
from collections.abc import Iterator
from pathlib import Path

from ananke.plexus.registry.errors import RegistryError

_LOOPBACK = {"127.0.0.1", "::1", "localhost"}


class DocsUnavailableError(RegistryError):
    code = "DOCS_UNAVAILABLE"


class DocsBuildError(RegistryError):
    code = "DOCS_BUILD_FAILED"


def _dev_checkout_root() -> Path | None:
    """When running from an editable/source install, the repo's own docs/ is right there."""
    candidate = Path(__file__).resolve().parents[3] / "docs"
    return candidate.parent if (candidate / "index.md").is_file() else None


def bundled_docs_root() -> Path | None:
    """The packaged docs source (``mkdocs.yml`` + ``docs/``), or ``None`` if not bundled.

    Falls back to a source checkout so ``uv run ananke docs build`` works for contributors
    without a build step in between.
    """
    try:
        packaged = importlib.resources.files("ananke.plexus") / "_docsite"
    except ModuleNotFoundError:  # pragma: no cover - package always importable here
        packaged = None
    if packaged is not None and (packaged / "mkdocs.yml").is_file():
        with importlib.resources.as_file(packaged) as path:
            return Path(path)
    return _dev_checkout_root()


def resolve_source(source: Path | None) -> Path:
    """A directory containing ``mkdocs.yml`` — an explicit ``--source``, or the bundled copy."""
    if source is not None:
        candidate = source.expanduser().resolve()
        if not (candidate / "mkdocs.yml").is_file():
            raise DocsUnavailableError(
                f"{candidate} has no mkdocs.yml — pass the repository root (or a checkout of it)"
            )
        return candidate
    bundled = bundled_docs_root()
    if bundled is None:
        raise DocsUnavailableError(
            "no bundled documentation found in this install; pass --source PATH pointing "
            "at a checkout of https://github.com/Neo-Pragnya/Ananke-Plexus"
        )
    return bundled


def _mkdocs_available() -> bool:
    try:
        import mkdocs  # noqa: F401
    except ImportError:
        return False
    return True


@contextlib.contextmanager
def _materialized(source: Path) -> Iterator[Path]:
    """A private, writable copy of ``source`` — MkDocs writes cache files next to the config."""
    with tempfile.TemporaryDirectory(prefix="ananke-docs-src-") as tmp:
        work = Path(tmp) / "site"
        shutil.copytree(source, work, ignore=shutil.ignore_patterns(".git", "site", "__pycache__"))
        yield work


def build_docs(output: Path, *, source: Path | None = None) -> Path:
    """Render the full documentation site to ``output``. Returns ``output``."""
    if not _mkdocs_available():
        raise DocsUnavailableError(
            "mkdocs is not installed; pip install ananke-plexus[docs] (or: uv sync --extra docs)"
        )
    src = resolve_source(source)
    output = output.expanduser().resolve()
    from mkdocs.commands.build import build as mkdocs_build
    from mkdocs.config import load_config
    from mkdocs.exceptions import ConfigurationError

    with _materialized(src) as work:
        try:
            config = load_config(str(work / "mkdocs.yml"), site_dir=str(output))
            mkdocs_build(config)
        except ConfigurationError as exc:
            raise DocsBuildError(f"invalid mkdocs.yml: {exc}") from exc
    return output


def _free_port(host: str) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, 0))
        return int(s.getsockname()[1])


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        return

    def end_headers(self) -> None:
        # Read-only static docs: no reason to ever execute anything from this response.
        self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data:")
        self.send_header("X-Content-Type-Options", "nosniff")
        super().end_headers()


def make_server(
    directory: Path, host: str = "127.0.0.1", port: int = 0
) -> http.server.ThreadingHTTPServer:
    handler = functools.partial(_QuietHandler, directory=str(directory))
    return http.server.ThreadingHTTPServer((host, port or _free_port(host)), handler)


def serve_docs(
    *,
    source: Path | None = None,
    host: str = "127.0.0.1",
    port: int = 0,
    open_browser: bool = True,
) -> None:  # pragma: no cover - blocking, exercised via make_server/build_docs in tests
    """Build to a temp directory, then serve it (loopback by default) until interrupted."""
    with tempfile.TemporaryDirectory(prefix="ananke-docs-out-") as tmp:
        out = build_docs(Path(tmp) / "site", source=source)
        server = make_server(out, host, port)
        bound_host = server.server_address[0]
        url = f"http://{bound_host if isinstance(bound_host, str) else host}:{server.server_address[1]}/"
        if host not in _LOOPBACK:
            print(f"warning: serving on {host}, not just loopback — docs are public content")
        print(f"Serving offline docs at {url} (Ctrl+C to stop)")
        if open_browser:
            threading.Timer(0.3, lambda: webbrowser.open(url)).start()
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
