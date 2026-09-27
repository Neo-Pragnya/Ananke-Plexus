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
    (root / "docs" / "index.md").write_text("# Hello\n\nWorld.\n")
    if with_logo:
        (root / "docs" / "assets" / "branding" / "ananke-logo.png").write_bytes(b"\x89PNG\r\n")
    (root / "mkdocs.yml").write_text(
        "site_name: Test\ntheme:\n  name: material\n  logo: assets/branding/ananke-logo.png\n"
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
        """The real project docs, via whichever source resolve_source(None) finds.

        In a source checkout that is the dev-checkout fallback (full-resolution assets,
        since a contributor previewing from source wants what will actually ship on the
        hosted site); the packaged wheel is exercised separately (see the packaging test
        below), where the build hook substitutes the small offline logo.
        """
        out = build_docs(tmp_path / "real-out")
        assert (out / "index.html").is_file()
        assert (out / "concepts" / "registry" / "index.html").is_file()
        assert (out / "assets" / "branding" / "ananke-logo.png").is_file()


class TestPackagingHook:
    """hatch_build.py: what actually lands in the wheel (see docs_site.py's module docstring)."""

    def test_bundles_markdown_but_not_hero_images(self, tmp_path: Path) -> None:
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
        included = build_data["force_include"]
        assert isinstance(included, dict) and included

        dests = set(included.values())
        assert "ananke/plexus/_docsite/mkdocs.yml" in dests
        assert "ananke/plexus/_docsite/docs/index.md" in dests
        assert "ananke/plexus/_docsite/docs/assets/branding/ananke-logo.png" in dests
        assert not any(
            d.endswith((".png", ".jpg", ".jpeg", ".gif")) and "branding" not in d for d in dests
        )
        assert not any("Ananke Plexus.md" in d or ".DS_Store" in d for d in dests)

        # the logo entry must point at the small pre-shrunk file, not the 2MB original
        logo_source = next(
            src for src, dst in included.items() if dst.endswith("assets/branding/ananke-logo.png")
        )
        assert Path(logo_source).name == "ananke-logo-offline.png"
        assert Path(logo_source).stat().st_size < 500_000


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
