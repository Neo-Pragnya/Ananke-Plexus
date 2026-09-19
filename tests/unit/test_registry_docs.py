"""Static HTML docs: model, safety, determinism, incremental builds, single-file dump (spec §72-§81, §109-§114, §131)."""

from __future__ import annotations

import json
import re
from pathlib import Path

from registry_support import add, open_registry

from ananke.plexus.registry.docsgen.builder import CACHE_NAME, build_dump, build_site
from ananke.plexus.registry.docsgen.markdown import render_markdown
from ananke.plexus.registry.docsgen.model import build_documentation_model
from ananke.plexus.registry.docsgen.render import inspection_report_html, pg_id
from ananke.plexus.registry.docsgen.svg import render_graph_svg, render_lineage_svg
from ananke.plexus.registry.learn import inspect_source


def populate(reg) -> None:  # type: ignore[no-untyped-def]
    add(
        reg, "graph-query", "1.0.0", approve=True, capabilities=["graph.query"], tags=None
    ) if False else None
    add(reg, "graph-query", "1.0.0", approve=True)
    add(
        reg,
        "graph-review",
        "1.0.0",
        capabilities=["graph.query", "architecture.read"],
        dependencies=[{"skill": "core/graph-query", "version": "^1"}],
        permissions={"network": True},
        runtime={"supported": ["pydantic"]},
    )
    add(
        reg,
        "graph-review",
        "2.0.0",
        capabilities=["graph.query"],
        dependencies=[{"skill": "core/graph-query", "version": "^1"}],
        runtime={"supported": ["pydantic", "microsoft"]},
        inputs={
            "json_schema": {
                "type": "object",
                "properties": {"q": {"type": "string"}},
                "required": ["q"],
            }
        },
        files={
            "README.md": b"# Graph review\n\nDoes **things**.",
            "tests/test_x.py": b"def test_x(): pass\n",
        },
    )
    add(
        reg,
        "coding-agent",
        "1.0.0",
        kind="agent",
        namespace="eng",
        skills=[{"ref": "core/graph-review", "version": "^2"}],
        runtime={"provider": "pydantic", "supported": ["pydantic"]},
        instructions={"ref": "prompts/coder.md"},
        files={"prompts/coder.md": b"You code."},
    )


def all_html(root: Path) -> dict[str, str]:
    return {str(p.relative_to(root)): p.read_text() for p in root.rglob("*.html")}


class TestModel:
    def test_model_content(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        populate(reg)
        m = build_documentation_model(reg)
        assert m.counts["skill"] == 2 and m.counts["agent"] == 1 and m.counts["versions"] == 4
        review = m.artifact("ananke://skill/core/graph-review")
        assert (
            review
            and review.default_version == "2.0.0"
            and [v.version for v in review.versions] == ["1.0.0", "2.0.0"]
        )
        v2 = review.version("2.0.0")
        assert v2.changes_from_previous and v2.changes_from_previous.suggested_bump in {
            "MAJOR",
            "MINOR",
        }
        assert v2.test_files == ["tests/test_x.py"] and v2.readme.startswith("# Graph review")
        assert v2.dependencies[0].resolved_version == "1.0.0" and v2.dependencies[0].target_uri
        query = m.artifact("ananke://skill/core/graph-query")
        assert query and query.latest_approved == "1.0.0" and query.used_by == ["core/graph-review"]
        assert "graph.query" in m.capabilities and "pydantic" in m.frameworks
        assert m.trust_breakdown["approved"] == 1 and m.license_breakdown["Apache-2.0"] == 4
        assert m.comparisons and m.search_index[0]["page"].endswith(".html")

    def test_default_version_prefers_approved_then_stable_then_latest(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        add(reg, "x", "1.0.0", approve=True)
        add(reg, "x", "2.0.0")
        m = build_documentation_model(reg)
        art = m.artifact("ananke://skill/core/x")
        assert art and art.default_version == "1.0.0" and art.latest == "2.0.0"

    def test_yanked_version_not_default(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        add(reg, "x", "1.0.0")
        add(reg, "x", "2.0.0")
        reg.yank("core/x@2.0.0")
        art = build_documentation_model(reg).artifact("ananke://skill/core/x")
        assert art and art.default_version == "1.0.0"

    def test_capability_matrix(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        populate(reg)
        m = build_documentation_model(reg)
        cols = m.capability_columns
        rows = {r.title: dict(zip(cols, r.cells, strict=True)) for r in m.capability_matrix}
        assert rows["core/graph-query"]["graph.query"] and not rows["core/graph-query"]["network"]
        assert rows["core/graph-review"]["graph.query"]
        assert "network" in cols and "filesystem.write" in cols

    def test_empty_registry(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        result = build_site(reg)
        assert result.total_pages >= 5
        assert "registry is empty" in (reg.root / "docs" / "index.html").read_text()


class TestSite:
    def test_expected_layout(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        populate(reg)
        res = build_site(reg)
        docs = reg.root / "docs"
        for rel in (
            "index.html",
            "skills/index.html",
            "agents/index.html",
            "capabilities/index.html",
            "frameworks/index.html",
            "publishers/index.html",
            "search.html",
            "search-index.json",
            "assets/style.css",
            "assets/app.js",
            "skills/core/graph-review/index.html",
            "skills/core/graph-review/1.0.0.html",
            "skills/core/graph-review/compare/1.0.0...2.0.0.html",
            "agents/eng/coding-agent/index.html",
        ):
            assert (docs / rel).is_file(), rel
        assert res.pages_written == res.total_pages and res.pages_skipped == 0
        index = json.loads((docs / "search-index.json").read_text())
        assert {e["name"] for e in index} == {
            "core/graph-query",
            "core/graph-review",
            "eng/coding-agent",
        }

    def test_artifact_page_sections(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        populate(reg)
        build_site(reg)
        html = (reg.root / "docs/skills/core/graph-review/index.html").read_text()
        for needle in (
            "Latest (2.0.0)",
            "All versions (2)",
            "Capabilities",
            "Runtime support",
            "Permissions",
            "Dependencies",
            "Dependency graph",
            "Version history",
            "README",
            "Tests",
            "Provenance",
            "Source files",
            "tests/test_x.py",
            "compare 1.0.0…2.0.0",
            "core/graph-query",
            "<svg",
        ):
            assert needle in html, needle
        assert "Used by" in (reg.root / "docs/skills/core/graph-query/index.html").read_text()

    def test_breaking_changes_flagged_in_history_and_compare(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        add(reg, "x", "1.0.0", capabilities=["graph.query", "graph.write"])
        add(reg, "x", "2.0.0", capabilities=["graph.query"], permissions={"network": True})
        build_site(reg)
        hist = (reg.root / "docs/skills/core/x/index.html").read_text()
        assert "BREAKING" in hist
        cmp_html = (reg.root / "docs/skills/core/x/compare/1.0.0...2.0.0.html").read_text()
        assert (
            "Permissions expanded" in cmp_html
            and "capability removed" in cmp_html
            and "network:*" in cmp_html
        )

    def test_deprecated_and_quarantined_banners(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        add(reg, "old", "1.0.0")
        add(reg, "bad", "1.0.0")
        reg.deprecate("core/old", "superseded", replacement="core/new")
        reg.quarantine("core/bad@1.0.0", "malicious")
        build_site(reg)
        assert "Deprecated." in (reg.root / "docs/skills/core/old/index.html").read_text()
        assert "core/new" in (reg.root / "docs/skills/core/old/index.html").read_text()
        assert "Quarantined." in (reg.root / "docs/skills/core/bad/index.html").read_text()

    def test_version_selector_links_work(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        add(reg, "x", "1.0.0", approve=True)
        add(reg, "x", "1.1.0", channel="stable")
        add(reg, "x", "2.0.0")
        build_site(reg)
        html = (reg.root / "docs/skills/core/x/index.html").read_text()
        assert (
            "Latest approved (1.0.0)" in html
            and "Latest stable (1.1.0)" in html
            and "Latest (2.0.0)" in html
        )
        assert (reg.root / "docs/skills/core/x/1.1.0.html").is_file() and (
            reg.root / "docs/skills/core/x/2.0.0.html"
        ).is_file()

    def test_all_internal_links_resolve(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        populate(reg)
        build_site(reg)
        docs = reg.root / "docs"
        for rel, html in all_html(docs).items():
            for href in re.findall(r'href="([^"#][^"]*)"', html) + re.findall(
                r'src="([^"]+)"', html
            ):
                if href.startswith(("http://", "https://", "mailto:")):
                    continue
                target = (docs / rel).parent / href
                assert target.resolve().exists(), f"{rel}: broken link {href}"

    def test_build_metadata_no_wall_clock_and_deterministic(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        populate(reg)
        a, b = tmp_path / "out-a", tmp_path / "out-b"
        build_site(reg, a)
        build_site(reg, b)
        pa, pb = all_html(a), all_html(b)
        assert pa == pb and pa  # byte-identical output for identical registry state

    def test_incremental_rebuild_only_touches_affected_pages(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        populate(reg)
        add(reg, "unrelated", "1.0.0")
        build_site(reg)
        unrelated = reg.root / "docs/skills/core/unrelated/index.html"
        before = unrelated.stat().st_mtime_ns
        again = build_site(reg)
        assert again.pages_written == 0 and again.pages_skipped == again.total_pages
        add(reg, "graph-query", "1.1.0", approve=True, files={"README.md": b"new"})
        third = build_site(reg)
        assert 0 < third.pages_written < third.total_pages
        assert unrelated.stat().st_mtime_ns == before  # untouched page not rewritten
        assert "1.1.0" in (reg.root / "docs/skills/core/graph-query/index.html").read_text()

    def test_tampered_output_is_repaired(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        populate(reg)
        build_site(reg)
        page = reg.root / "docs/index.html"
        page.write_text("tampered")
        build_site(reg)
        assert "Ananke Plexus Registry" in page.read_text()

    def test_stale_pages_removed(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        add(reg, "x", "1.0.0")
        add(reg, "y", "1.0.0")
        build_site(reg)
        assert (reg.root / "docs/skills/core/y/index.html").exists()
        reg.unregister("core/y@1.0.0", purge=True)
        res = build_site(reg)
        assert res.pages_removed >= 1 and not (reg.root / "docs/skills/core/y/index.html").exists()

    def test_cache_is_rebuildable(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        populate(reg)
        build_site(reg)
        (reg.root / "docs" / CACHE_NAME).unlink()
        res = build_site(reg)
        assert res.pages_written == res.total_pages

    def test_docs_generated_event(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        build_site(reg)
        assert "docs.generated" in [e["event_type"] for e in reg.store.list_events()]


class TestSecurity:
    HOSTILE = "<script>alert('xss')</script><img src=x onerror=alert(1)>"

    def _hostile_registry(self, tmp_path: Path):  # type: ignore[no-untyped-def]
        reg = open_registry(tmp_path)
        add(
            reg,
            "evil",
            "1.0.0",
            summary=self.HOSTILE,
            description=self.HOSTILE,
            tools=[{"name": "t", "description": self.HOSTILE}],
            metadata={"tags": ["<b>x</b>"]},
            files={
                "README.md": (
                    self.HOSTILE
                    + "\n\n[click](javascript:alert(1)) [data](data:text/html,<script>) "
                    "![img](http://tracker.example/p.png) <iframe src=//evil></iframe>"
                ).encode()
            },
        )
        return reg

    def test_untrusted_text_is_escaped_everywhere(self, tmp_path: Path) -> None:
        reg = self._hostile_registry(tmp_path)
        build_site(reg)
        for rel, html in all_html(reg.root / "docs").items():
            assert "<script>alert" not in html, rel
            assert "<img src=x" not in html, rel
            assert "<iframe" not in html, rel
            assert 'href="javascript' not in html and 'href="data:' not in html, rel

    def test_strict_csp_and_no_inline_or_external_code(self, tmp_path: Path) -> None:
        reg = self._hostile_registry(tmp_path)
        build_site(reg)
        for rel, html in all_html(reg.root / "docs").items():
            assert "default-src 'none'" in html, rel
            for tag in re.findall(r"<script\b[^>]*>", html):
                assert "src=" in tag or "application/json" in tag, f"{rel}: inline script {tag}"
            assert " style=" not in html, rel
            assert not re.search(
                r'<(?:script|link|img|iframe)\b[^>]*(?:src|href)="(?:https?:)?//', html
            ), rel

    def test_markdown_sanitizer_units(self) -> None:
        out = render_markdown(
            "<b>raw</b> **bold** `code` [ok](https://a.example/x?y=1&z=2) [bad](javascript:alert(1)) "
            "[rel](docs/a.md) [prot](//evil.example) ![i](https://t.example/p.png)"
        )
        assert (
            "&lt;b&gt;raw" in out and "<strong>bold</strong>" in out and "<code>code</code>" in out
        )
        assert (
            'href="https://a.example/x?y=1&amp;z=2"' in out
            and 'rel="noopener noreferrer nofollow"' in out
        )
        assert "javascript:" not in out and 'href="//evil' not in out and 'href="docs/a.md"' in out
        assert "<img" not in out

    def test_markdown_blocks(self) -> None:
        out = render_markdown(
            "---\ntitle: x\n---\n# Title\n\n- a\n- b\n\n1. one\n\n```py\n<b>code</b>\n```\n\n> quote\n\n---\n"
        )
        assert "<h2>Title</h2>" in out and "<ul>" in out and "<ol>" in out
        assert (
            "<pre><code>&lt;b&gt;code&lt;/b&gt;" in out and "<blockquote>" in out and "<hr>" in out
        )
        assert "title: x" not in out

    def test_markdown_size_cap(self) -> None:
        assert len(render_markdown("a" * 100_000)) < 30_000

    def test_svg_escapes_labels(self) -> None:
        svg = render_graph_svg(
            ["a"],
            {"a": ["b"]},
            {"a": "<script>x</script>", "b": "ok"},
            {"a": "skill", "b": "skill"},
        )
        assert "<script>" not in svg and "&lt;script&gt;" in svg and svg.count("<rect") == 2
        assert render_graph_svg([], {}, {}, {}) == ""
        assert "style=" not in svg

    def test_svg_cycle_safe(self) -> None:
        svg = render_graph_svg(["a"], {"a": ["b"], "b": ["a"]}, {"a": "a", "b": "b"}, {})
        assert svg.count("<rect") == 2

    def test_lineage_svg(self) -> None:
        svg = render_lineage_svg([("1.0.0", "active"), ("2.0.0", "yanked")])
        assert "1.0.0" in svg and "2.0.0" in svg and render_lineage_svg([]) == ""


class TestSingleFile:
    def test_dump_is_self_contained_and_offline(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        populate(reg)
        out = build_dump(reg, tmp_path / "registry.html")
        html = out.read_text()
        assert html.startswith("<!doctype html>") and "<style>" in html
        assert not re.search(r'<(?:script|link)[^>]+(?:src|href)="(?!#)', html)
        assert "http://" not in html.replace("http://www.w3.org/2000/svg", "")
        for name in ("core/graph-review", "core/graph-query", "eng/coding-agent"):
            assert name in html
        ids = set(re.findall(r'id="(pg-[^"]+)"', html))
        for target in re.findall(r'href="#(pg-[^"]+)"', html):
            assert target in ids, f"dangling router link {target}"
        assert pg_id("index.html") in ids

    def test_single_file_csp_hashes_match_inline_blocks(self, tmp_path: Path) -> None:
        import base64
        import hashlib

        reg = open_registry(tmp_path)
        populate(reg)
        html = build_dump(reg, tmp_path / "r.html").read_text()
        style = re.search(r"<style>(.*?)</style>", html, re.S)
        script = re.search(r"<script>(.*?)</script>", html, re.S)
        assert style and script
        for body, directive in ((style.group(1), "style-src"), (script.group(1), "script-src")):
            digest = base64.b64encode(hashlib.sha256(body.encode()).digest()).decode()
            assert f"{directive} 'sha256-{digest}'" in html

    def test_embedded_search_data_is_valid_json(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        add(reg, "x", "1.0.0", summary="</script><b>")
        html = build_dump(reg, tmp_path / "r.html").read_text()
        data = re.search(
            r'<script type="application/json" id="search-data">(.*?)</script>', html, re.S
        )
        assert data
        entries = json.loads(data.group(1))
        assert entries[0]["summary"] == "</script><b>" and entries[0]["href"].startswith("#pg-")

    def test_default_output_location_and_event(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        out = build_dump(reg)
        assert out == reg.root / "docs" / "registry.html" and out.exists()

    def test_dump_includes_comparisons_and_matrix(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        populate(reg)
        html = build_dump(reg, tmp_path / "r.html").read_text()
        assert (
            "Capability matrix" in html
            and "compare 1.0.0…2.0.0" in html
            and "Trust changes" in html
        )


class TestInspectionReport:
    def test_report_html(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        d = tmp_path / "s"
        d.mkdir()
        (d / "ananke.yaml").write_text(
            "kind: skill\nnamespace: a\nname: risky\nversion: 1.0.0\nsummary: '<b>hi</b>'\npermissions:\n  network: [x.example]\n"
        )
        html = inspection_report_html(inspect_source(reg, str(d)))
        assert "Nothing was registered" in html and "network access" in html
        bad = tmp_path / "bad"
        bad.mkdir()
        (bad / "ananke.yaml").write_text(
            "kind: skill\nnamespace: a\nname: '<b>risky</b>'\nversion: 1.0.0\n"
        )
        bad_html = inspection_report_html(inspect_source(reg, str(bad)))
        assert (
            "<b>risky" not in bad_html and "&lt;b&gt;risky" in bad_html and "incomplete" in bad_html
        )
