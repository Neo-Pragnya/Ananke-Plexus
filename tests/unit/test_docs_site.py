"""Offline documentation site: source resolution, building, and the static server."""

from __future__ import annotations

import threading
import time
import urllib.request
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ananke.plexus.cli.app import app
from ananke.plexus.docs_site import (
    DocsUnavailableError,
    build_docs,
    bundled_docs_root,
    make_server,
    resolve_source,
)

pytest.importorskip("mkdocs")


def _minimal_site(tmp_path: Path, *, with_logo: bool = True) -> Path:
    root = tmp_path / "site-src"
    (root / "docs" / "assets" / "branding").mkdir(parents=True)
    (root / "docs" / "index.md").write_text(
        "# Hello\n\nWorld.\n\n[next page](guides/workflows.md)\n"
    )
    (root / "docs" / "guides").mkdir(parents=True)
    (root / "docs" / "guides" / "workflows.md").write_text("# Workflows\n\nMore words.\n")
    if with_logo:
        (root / "docs" / "assets" / "branding" / "ananke-logo.jpg").write_bytes(b"\xff\xd8\xff")
    (root / "mkdocs.yml").write_text(
        "site_name: Test\ntheme:\n  name: material\n  logo: assets/branding/ananke-logo.jpg\n"
        "nav:\n  - Home: index.md\n  - Workflows: guides/workflows.md\n"
        if with_logo
        else "site_name: Test\ntheme:\n  name: material\n"
    )
    return root


class TestResolveSource:
    def test_bundled_root_is_found(self) -> None:
        # this repo checkout counts as a "bundled" source via the dev-checkout fallback
        root = bundled_docs_root()
        assert root is not None and (root / "mkdocs.yml").is_file()

    def test_explicit_source_used_verbatim(self, tmp_path: Path) -> None:
        src = _minimal_site(tmp_path)
        assert resolve_source(src) == src.resolve()

    def test_explicit_source_without_mkdocs_yml_rejected(self, tmp_path: Path) -> None:
        empty = tmp_path / "nothing"
        empty.mkdir()
        with pytest.raises(DocsUnavailableError):
            resolve_source(empty)

    def test_missing_bundle_and_no_dev_checkout_is_reported(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import ananke.plexus.docs_site as mod

        monkeypatch.setattr(mod, "bundled_docs_root", lambda: None)
        with pytest.raises(DocsUnavailableError, match="--source"):
            resolve_source(None)


class TestBuildDocs:
    def test_builds_a_minimal_site(self, tmp_path: Path) -> None:
        src = _minimal_site(tmp_path)
        out = build_docs(tmp_path / "out", source=src)
        assert (out / "index.html").is_file()
        assert "Hello" in (out / "index.html").read_text()

    def test_source_is_never_mutated(self, tmp_path: Path) -> None:
        src = _minimal_site(tmp_path)
        before = sorted(p.relative_to(src) for p in src.rglob("*"))
        build_docs(tmp_path / "out", source=src)
        after = sorted(p.relative_to(src) for p in src.rglob("*"))
        assert before == after  # no .cache/site/etc. leaked back into the source tree

    def test_missing_image_degrades_without_failing_the_build(self, tmp_path: Path) -> None:
        src = _minimal_site(tmp_path, with_logo=False)
        (src / "docs" / "index.md").write_text("# Hello\n\n![missing](assets/nope.png)\n")
        out = build_docs(tmp_path / "out", source=src)
        assert (out / "index.html").is_file()

    def test_real_bundled_docs_build_end_to_end(self, tmp_path: Path) -> None:
        """The real project docs, via whichever source resolve_source(None) finds (in a
        source checkout, the dev-checkout fallback)."""
        out = build_docs(tmp_path / "real-out")
        assert (out / "index.html").is_file()
        # flat .html files by default: internal links work opened directly via file://,
        # with no server needed to resolve a bare directory URL to its index.html
        assert (out / "concepts" / "registry.html").is_file()
        assert not (out / "concepts" / "registry").exists()
        assert (out / "assets" / "branding" / "ananke-logo.jpg").is_file()
        # every hero image is bundled now (pre-compressed small enough to always include)
        assert (out / "assets" / "ananke_plexus_architecture_overview.jpg").is_file()

    def test_directory_urls_opt_in(self, tmp_path: Path) -> None:
        src = _minimal_site(tmp_path)
        out = build_docs(tmp_path / "out", source=src, directory_urls=True)
        assert (out / "guides" / "workflows" / "index.html").is_file()
        assert not (out / "guides" / "workflows.html").exists()

    def test_internal_links_are_directly_openable_without_a_server(self, tmp_path: Path) -> None:
        src = _minimal_site(tmp_path)
        out = build_docs(tmp_path / "out", source=src)
        html = (out / "index.html").read_text()
        assert 'href="guides/workflows.html"' in html
        assert (out / "guides" / "workflows.html").is_file()


class TestPackagingHook:
    """hatch_build.py: what actually lands in the wheel (see docs_site.py's module docstring)."""

    def _hook(self, tmp_path: Path) -> tuple[object, dict[str, object]]:
        import sys

        pytest.importorskip("hatchling")
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
        try:
            from hatch_build import DocsSiteBuildHook
        finally:
            sys.path.remove(str(Path(__file__).resolve().parents[2]))

        root = Path(__file__).resolve().parents[2]
        hook = DocsSiteBuildHook(str(root), {}, {}, {}, str(tmp_path), "wheel")
        build_data: dict[str, object] = {}
        hook.initialize("0.0.0", build_data)
        return hook, build_data

    def test_bundles_markdown_and_images_alike(self, tmp_path: Path) -> None:
        _, build_data = self._hook(tmp_path)
        included = build_data["force_include"]
        assert isinstance(included, dict) and included

        dests = set(included.values())
        assert "ananke/plexus/_docsite/mkdocs.yml" in dests
        assert "ananke/plexus/_docsite/docs/index.md" in dests
        assert "ananke/plexus/_docsite/docs/assets/branding/ananke-logo.jpg" in dests
        assert "ananke/plexus/_docsite/docs/assets/ananke_plexus_architecture_overview.jpg" in dests
        assert not any("Ananke Plexus.md" in d or ".DS_Store" in d for d in dests)

    def test_bundled_images_are_small_enough_to_always_ship(self, tmp_path: Path) -> None:
        _, build_data = self._hook(tmp_path)
        included = build_data["force_include"]
        image_sources = [
            src for src, dst in included.items() if dst.endswith((".jpg", ".jpeg", ".png"))
        ]
        assert image_sources  # the test would be vacuous otherwise
        assert all(Path(src).stat().st_size < 600_000 for src in image_sources)
        assert sum(Path(src).stat().st_size for src in image_sources) < 4_000_000

    def test_vendored_mermaid_is_bundled(self, tmp_path: Path) -> None:
        _, build_data = self._hook(tmp_path)
        dests = set(build_data["force_include"].values())  # type: ignore[attr-defined]
        assert "ananke/plexus/_docsite/docs/assets/javascripts/mermaid.min.js" in dests


class TestMermaidRendersOffline:
    """Material lazy-loads Mermaid from https://unpkg.com on first use *unless*
    ``window.mermaid`` is already defined — so a page opened via file:// with no network (or
    the CDN merely being slow/unreachable) silently never renders any diagram. mkdocs.yml
    vendors mermaid.min.js via extra_javascript specifically to pre-empt that fetch; these
    tests prove the mechanics are in place and, where a browser is available, that a diagram
    actually renders with the real CDN host unreachable.
    """

    def test_mkdocs_yml_vendors_mermaid_before_relying_on_a_cdn(self) -> None:
        root = bundled_docs_root()
        assert root is not None
        config = (root / "mkdocs.yml").read_text()
        assert "assets/javascripts/mermaid.min.js" in config
        assert (root / "docs" / "assets" / "javascripts" / "mermaid.min.js").is_file()

    def test_vendored_file_defines_the_global_mermaid_expects(self) -> None:
        root = bundled_docs_root()
        assert root is not None
        js = (root / "docs" / "assets" / "javascripts" / "mermaid.min.js").read_text()
        # this is literally the check Material's bundle makes before deciding to fetch the
        # CDN copy (`typeof mermaid=="undefined"`) — the vendored file must satisfy it
        assert 'globalThis["mermaid"]' in js or "globalThis.mermaid" in js

    def test_diagram_renders_with_the_cdn_host_unreachable(self, tmp_path: Path) -> None:
        import shutil
        import subprocess

        chrome = (
            shutil.which("google-chrome")
            or shutil.which("chromium")
            or (
                "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
                if Path("/Applications/Google Chrome.app").exists()
                else None
            )
        )
        if not chrome:
            pytest.skip("no headless Chrome available to render against")

        out = build_docs(tmp_path / "out")
        page = out / "concepts" / "architecture.html"
        assert page.is_file()
        # the markdown pipeline must still be emitting what Material's `pre.mermaid`
        # selector looks for — if this regresses, nothing downstream has a chance
        assert '<pre class="mermaid">' in page.read_text()
        shot = tmp_path / "shot.png"
        subprocess.run(
            [
                chrome,
                "--headless=new",
                "--disable-gpu",
                "--virtual-time-budget=8000",
                "--window-size=1200,2000",
                "--host-resolver-rules=MAP unpkg.com 0.0.0.0",  # simulate no network to the CDN
                f"--screenshot={shot}",
                page.as_uri(),
            ],
            capture_output=True,
            timeout=30,
            check=True,
        )
        assert shot.is_file() and shot.stat().st_size > 10_000
        # Material renders the diagram inside a *closed* shadow root for CSS isolation, so
        # its content is invisible to DOM dumps/serialization — a screenshot (or a human
        # looking at the page) is the only way to actually observe whether it rendered.
        # We can't pixel-diff a diagram cheaply here, so this asserts the one thing a crash
        # or a silently-empty page would fail: a real, non-trivial screenshot was produced.


class TestStaticServer:
    def test_serves_built_output_read_only(self, tmp_path: Path) -> None:
        src = _minimal_site(tmp_path)
        out = build_docs(tmp_path / "out", source=src)
        server = make_server(out, "127.0.0.1", 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            for _ in range(50):
                try:
                    resp = urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1)
                    break
                except OSError:
                    time.sleep(0.05)
            else:
                pytest.fail("server never became reachable")
            assert resp.status == 200
            assert "Hello" in resp.read().decode()
            assert resp.headers.get("X-Content-Type-Options") == "nosniff"
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_picks_a_free_port_when_zero(self, tmp_path: Path) -> None:
        out = build_docs(tmp_path / "out", source=_minimal_site(tmp_path))
        a = make_server(out, "127.0.0.1", 0)
        b = make_server(out, "127.0.0.1", 0)
        try:
            assert a.server_address[1] != b.server_address[1]
        finally:
            a.server_close()
            b.server_close()


class TestDocsCli:
    def test_docs_build_command(self, tmp_path: Path) -> None:
        # a minimal fixture keeps this fast; the real docs tree is exercised once in
        # TestBuildDocs.test_real_bundled_docs_build_end_to_end
        src = _minimal_site(tmp_path)
        out = tmp_path / "site"
        res = CliRunner().invoke(app, ["docs", "build", "-o", str(out), "--source", str(src)])
        assert res.exit_code == 0, res.output
        assert (out / "index.html").is_file()

    def test_docs_build_with_explicit_source(self, tmp_path: Path) -> None:
        src = _minimal_site(tmp_path)
        out = tmp_path / "out"
        res = CliRunner().invoke(app, ["docs", "build", "-o", str(out), "--source", str(src)])
        assert res.exit_code == 0
        assert "Hello" in (out / "index.html").read_text()

    def test_docs_build_rejects_bad_source(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad"
        bad.mkdir()
        res = CliRunner().invoke(app, ["docs", "build", "--source", str(bad)])
        assert res.exit_code == 2

    def test_docs_where_reports_the_resolved_source(self) -> None:
        expected = resolve_source(None)
        res = CliRunner().invoke(app, ["docs", "where"])
        assert res.exit_code == 0
        assert str(expected) in res.output.replace("\n", "")

    def test_docs_where_with_explicit_source(self, tmp_path: Path) -> None:
        src = _minimal_site(tmp_path)
        res = CliRunner().invoke(app, ["docs", "where", "--source", str(src)])
        # rich hard-wraps long paths mid-word with no separator inserted at the break
        unwrapped = res.output.replace("\n", "")
        assert res.exit_code == 0 and str(src.resolve()) in unwrapped
