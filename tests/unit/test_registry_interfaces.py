"""Search, quality gate, translation, HTTP server, MCP, schemas, federation, watcher (spec §13, §70, §84, §89, §94, §98, §161)."""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from registry_support import add, open_registry

from ananke.plexus.registry.errors import PolicyViolationError, TranslationError
from ananke.plexus.registry.mcp_interface import (
    REGISTRY_TOOLS,
    call_registry_tool,
    list_registry_resources,
    read_registry_resource,
)
from ananke.plexus.registry.models import ArtifactKind, Quality, Security, TrustStatus
from ananke.plexus.registry.policy import (
    ApprovalPolicy,
    NetworkPermissionPolicy,
    PermissionPolicy,
    RegistryPolicy,
    ResolvePolicy,
    SourceConfig,
)
from ananke.plexus.registry.present import record_to_dict
from ananke.plexus.registry.quality import QualityGate, badges
from ananke.plexus.registry.registry import Registry
from ananke.plexus.registry.schemas import registry_json_schemas, write_schemas
from ananke.plexus.registry.search import (
    parse_query,
    recommend_skills,
    search,
    search_agents,
)
from ananke.plexus.registry.server import make_server
from ananke.plexus.registry.sources import federated_resolve, open_sources, source_statuses
from ananke.plexus.registry.translate import KNOWN_RUNTIMES, check_translations, translate
from ananke.plexus.registry.watcher import read_pending, scan_once


def catalogue(reg) -> None:  # type: ignore[no-untyped-def]
    add(
        reg,
        "graph-review",
        "1.0.0",
        summary="Reviews code changes using the graph",
        capabilities=["graph.query", "architecture.read"],
        metadata={"tags": ["review", "graph"]},
        runtime={"supported": ["pydantic", "microsoft"]},
        approve=True,
    )
    add(
        reg,
        "test-runner",
        "3.4.0",
        summary="Runs tests",
        capabilities=["test.run"],
        metadata={"tags": ["testing"]},
        runtime={"supported": ["pydantic"]},
        permissions={"shell": {"allow": ["pytest"]}},
        approve=True,
    )
    add(
        reg,
        "git-safe",
        "1.8.0",
        summary="Safe git operations",
        capabilities=["git.commit"],
        permissions={"shell": {"allow": ["git"]}},
    )
    add(
        reg,
        "coding-agent",
        "3.2.0",
        kind="agent",
        namespace="engineering",
        summary="Autonomous coder",
        capabilities=["agent.run"],
        skills=[
            {"ref": "core/graph-review", "version": "^1"},
            {"ref": "core/test-runner", "version": "^3"},
        ],
        runtime={"provider": "pydantic", "supported": ["pydantic"]},
    )


class TestSearch:
    def test_ranking_prefers_name_then_tags(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        catalogue(reg)
        assert search(reg, "graph review")[0].name == "graph-review"
        assert [h.name for h in search(reg, "testing")] == ["test-runner"]
        assert search(reg, "graph-review")[0].score >= 100  # exact name boost

    def test_prefix_and_stemming(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        catalogue(reg)
        assert "test-runner" in {h.name for h in search(reg, "runn")}
        assert "test-runner" in {h.name for h in search(reg, "runs")}  # porter stemming via FTS5

    def test_filters(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        catalogue(reg)
        assert {h.name for h in search(reg, kind="agent")} == {"coding-agent"}
        assert {h.name for h in search(reg, capability="graph.query")} == {"graph-review"}
        assert {h.name for h in search(reg, capability="graph")} == {
            "graph-review"
        }  # hierarchical prefix
        assert {h.name for h in search(reg, runtime="microsoft")} == {"graph-review"}
        assert {h.name for h in search(reg, trust="approved")} == {"graph-review", "test-runner"}
        assert {h.name for h in search(reg, channel="stable")} == {"graph-review", "test-runner"}
        assert {h.name for h in search(reg, tag="review")} == {"graph-review"}
        assert len(search(reg, license="apache")) == 4
        assert {h.name for h in search(reg, namespace="engineering")} == {"coding-agent"}

    def test_query_dsl(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        catalogue(reg)
        q = parse_query(
            "kind:skill capability:graph.query runtime:pydantic trust:approved graph review"
        )
        assert (
            q.terms == ["graph", "review"]
            and q.filters["capability"] == ["graph.query"]
            and q.filters["kind"] == ["skill"]
        )
        dsl = "kind:skill capability:graph.query runtime:pydantic trust:approved graph review"
        assert [h.name for h in search(reg, dsl)] == ["graph-review"]
        assert search(reg, "kind:agent graph") == []
        assert parse_query("ns:core cap:a.b").filters == {
            "namespace": ["core"],
            "capability": ["a.b"],
        }

    def test_hidden_states_and_all_versions(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        add(reg, "x", "1.0.0")
        add(reg, "x", "2.0.0")
        reg.yank("core/x@2.0.0")
        assert [h.version for h in search(reg, "x")] == ["1.0.0"]
        assert {h.version for h in search(reg, "x", include_inactive=True, all_versions=True)} == {
            "1.0.0",
            "2.0.0",
        }

    def test_python_fallback_matches_fts(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        catalogue(reg)
        with_fts = [h.name for h in search(reg, "graph")]
        reg.store.fts_available = False
        assert [h.name for h in search(reg, "graph")] == with_fts
        assert [h.name for h in search(reg, "graph review", kind="skill")] == ["graph-review"]

    def test_special_characters_are_safe(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        catalogue(reg)
        for q in [
            '"',
            "graph AND OR",
            "graph*",
            "(",
            "a:b:c",
            "'; DROP TABLE artifacts; --",
            "NEAR(a b)",
            "\x01",
        ]:
            search(reg, q)
        assert reg.store.count_versions() == 4

    def test_agent_search(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        catalogue(reg)
        assert [h.name for h in search_agents(reg, skill="graph-review")] == ["coding-agent"]
        assert [
            h.name for h in search_agents(reg, skill="core/test-runner", runtime="pydantic")
        ] == ["coding-agent"]
        assert [h.name for h in search_agents(reg, capability="test.run")] == ["coding-agent"]
        assert (
            search_agents(reg, capability="git.commit") == []
            and search_agents(reg, runtime="langgraph") == []
        )

    def test_recommendations_are_policy_filtered_and_deterministic(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        add(reg, "gq-a", "2.4.0", capabilities=["graph.query"], approve=True)
        add(reg, "gq-b", "1.8.2", capabilities=["graph.query"], approve=True)
        add(reg, "gq-c", "9.0.0", capabilities=["graph.query"])
        reg.policy = RegistryPolicy(resolve=ResolvePolicy(minimum_trust=TrustStatus.APPROVED))
        recs = [h.name for h in recommend_skills(reg, "graph.query")]
        assert recs == ["gq-a", "gq-b"] and recs == [
            h.name for h in recommend_skills(reg, "graph.query")
        ]

    def test_record_presentation(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        catalogue(reg)
        d = record_to_dict(reg.exact_version("core/graph-review@1.0.0"), reg)
        assert (
            d["ref"] == "core/graph-review@1.0.0"
            and d["versions"] == ["1.0.0"]
            and "APPROVED" in d["badges"]
        )
        json.dumps(d)


class TestQualityGate:
    def test_all_default_checks_pass_for_clean_artifact(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        add(reg, "x", "1.0.0", runtime={"supported": ["pydantic"]})
        report = QualityGate(reg).run(reg.exact_version("core/x@1.0.0"))
        assert report.passed
        names = {c.name for c in report.checks}
        assert {
            "checksum",
            "manifest",
            "provenance",
            "schemas",
            "secrets",
            "dependencies",
            "runtime-translation",
            "license",
            "permission-review",
            "tests",
            "security",
            "signature",
        } <= names

    def test_promote_runs_gate_and_blocks(self, tmp_path: Path) -> None:
        policy = RegistryPolicy(approval=ApprovalPolicy(require=["tests", "vulnerability_scan"]))
        reg = open_registry(tmp_path, policy)
        ref = add(reg, "x", "1.0.0")
        with pytest.raises(PolicyViolationError, match="QUALITY_GATE_FAILED"):
            reg.promote(ref, trust=TrustStatus.APPROVED)
        assert reg.exact_version(ref).trust is TrustStatus.DISCOVERED
        reg.update_quality(ref, Quality(tests_status="pass"))
        reg.update_security(ref, Security(status="clean", scanner="trivy", last_scanned_at="t"))
        assert (
            reg.promote(ref, trust=TrustStatus.APPROVED, channel="stable").trust
            is TrustStatus.APPROVED
        )

    def test_network_permission_requires_human_review(self, tmp_path: Path) -> None:
        policy = RegistryPolicy(
            permissions=PermissionPolicy(network=NetworkPermissionPolicy(require_review=True))
        )
        reg = open_registry(tmp_path, policy)
        ref = add(reg, "net", "1.0.0", permissions={"network": ["api.example.com"]})
        with pytest.raises(PolicyViolationError, match="human review"):
            reg.promote(ref, trust=TrustStatus.VERIFIED)
        assert (
            reg.promote(ref, trust=TrustStatus.VERIFIED, reviewed_by="alice").trust
            is TrustStatus.VERIFIED
        )

    def test_missing_dependency_and_tamper_fail_gate(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        ref = add(reg, "x", "1.0.0", dependencies=[{"skill": "core/ghost", "version": "^1"}])
        rep = QualityGate(reg).run(reg.exact_version(ref))
        dep = next(c for c in rep.checks if c.name == "dependencies")
        assert not dep.ok and "core/ghost" in dep.detail and not rep.passed
        ref2 = add(reg, "y", "1.0.0")
        blob = reg.cas._path(reg.exact_version(ref2).digest_sha256)
        blob.chmod(0o644)
        blob.write_bytes(b"corrupt")
        assert not QualityGate(reg).run(reg.exact_version(ref2)).passed

    def test_quarantined_versions_cannot_be_promoted(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        ref = add(reg)
        reg.quarantine(ref, "bad")
        rep = QualityGate(reg).run(reg.exact_version(ref))
        assert not rep.passed and rep.checks[0].name == "lifecycle"

    def test_tests_are_not_executed_unless_requested(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        marker = tmp_path / "executed"
        body = f"open({str(marker)!r}, 'w').write('ran')\ndef test_x(): pass\n".encode()
        ref = add(reg, "x", "1.0.0", files={"tests/test_boom.py": body})
        QualityGate(reg).run(reg.exact_version(ref))
        assert not marker.exists()  # untrusted tests never run implicitly

    def test_run_tests_records_status(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        ref = add(
            reg, "x", "1.0.0", files={"tests/test_ok.py": b"def test_ok():\n    assert True\n"}
        )
        rep = QualityGate(reg).run(reg.exact_version(ref), run_tests=True)
        tests = next(c for c in rep.checks if c.name == "tests")
        assert tests.ok, tests.detail
        assert reg.exact_version(ref).quality.tests_status == "pass"
        assert "TESTED" in badges(reg.exact_version(ref))

    def test_badges_are_factual(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        ref = add(reg, "x", "1.0.0", approve=True, runtime={"supported": ["pydantic"]})
        b = badges(reg.exact_version(ref))
        assert {
            "APPROVED",
            "STABLE",
            "NO NETWORK",
            "RUNTIME: PYDANTIC",
            "LICENSE: APACHE-2.0",
        } <= set(b)
        assert "SIGNED" not in b and "TESTED" not in b


class TestTranslate:
    def _rec(self, reg):  # type: ignore[no-untyped-def]
        add(
            reg,
            "search",
            "1.0.0",
            kind="skill",
            tools=[
                {
                    "name": "find",
                    "description": "Find",
                    "input_schema": {"type": "object", "properties": {"q": {"type": "string"}}},
                }
            ],
            runtime={"supported": ["pydantic", "microsoft", "generic-mcp", "custom-rt"]},
        )
        return reg.exact_version("core/search@1.0.0")

    def test_each_runtime_shape(self, tmp_path: Path) -> None:
        rec = self._rec(open_registry(tmp_path))
        p = translate(rec, "pydantic")
        assert (
            p["tools"][0]["parameters_json_schema"]["properties"]["q"]["type"] == "string"
            and p["kind"] == "capability"
        )
        assert translate(rec, "microsoft")["functions"][0]["name"] == "find"
        assert translate(rec, "generic-mcp")["tools"][0]["inputSchema"]["type"] == "object"
        assert translate(rec, "langgraph")["nodes"][0]["name"] == "find"
        assert translate(rec, "crewai")["tools"][0]["args_schema"]

    def test_identity_is_never_changed(self, tmp_path: Path) -> None:
        rec = self._rec(open_registry(tmp_path))
        for rt in KNOWN_RUNTIMES:
            assert translate(rec, rt)["registry"] == {
                "uri": rec.uri,
                "version": rec.version,
                "digest": rec.digest,
            }

    def test_agent_translation_lists_skills(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        add(
            reg,
            "coder",
            "1.0.0",
            kind="agent",
            namespace="eng",
            skills=[{"ref": "core/search", "version": "^1"}],
            instructions={"ref": "p.md"},
            files={"p.md": b"x"},
        )
        d = translate(reg.exact_version("eng/coder@1.0.0", "agent"), "microsoft")
        assert (
            d["type"] == "agent"
            and d["plugins"] == ["core/search@^1"]
            and d["instructions_ref"] == "p.md"
        )

    def test_unknown_runtime_and_check_translations(self, tmp_path: Path) -> None:
        rec = self._rec(open_registry(tmp_path))
        with pytest.raises(TranslationError):
            translate(rec, "nope")
        results = dict(check_translations(rec))
        assert results["pydantic"] is None and "custom runtime" in (results["custom-rt"] or "")

    def test_skill_without_tools_gets_synthesized_tool(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        add(
            reg,
            "plain",
            "1.0.0",
            inputs={"json_schema": {"type": "object", "properties": {"a": {"type": "integer"}}}},
        )
        d = translate(reg.exact_version("core/plain@1.0.0"), "pydantic")
        assert d["tools"][0]["name"] == "plain"
        assert d["tools"][0]["parameters_json_schema"]["properties"]["a"]["type"] == "integer"


@pytest.fixture
def server(tmp_path: Path):  # type: ignore[no-untyped-def]
    reg = open_registry(tmp_path)
    catalogue(reg)
    reg.build_docs()
    reg.close()
    srv = make_server(reg.root, "127.0.0.1", 0)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()
    srv.server_close()


def get(
    url: str, method: str = "GET", data: bytes | None = None
) -> tuple[int, dict[str, str], bytes]:
    req = urllib.request.Request(url, method=method, data=data)  # noqa: S310 - loopback test server
    try:
        with urllib.request.urlopen(req, timeout=10) as r:  # noqa: S310
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


class TestHttpServer:
    def test_api_endpoints(self, server: str) -> None:
        s, _h, b = get(f"{server}/api/v1/health")
        assert s == 200 and json.loads(b)["ok"]
        assert len(json.loads(get(f"{server}/api/v1/artifacts")[2])["artifacts"]) == 4
        assert len(json.loads(get(f"{server}/api/v1/artifacts?kind=agent")[2])["artifacts"]) == 1
        detail = json.loads(get(f"{server}/api/v1/artifacts/skill/core/graph-review")[2])
        assert detail["version"] == "1.0.0" and detail["capabilities"] == [
            "architecture.read",
            "graph.query",
        ]
        versions = json.loads(
            get(f"{server}/api/v1/artifacts/skill/core/graph-review/versions")[2]
        )["versions"]
        assert [v["version"] for v in versions] == ["1.0.0"]
        hits = json.loads(get(f"{server}/api/v1/search?q=graph&kind=skill")[2])["results"]
        assert hits[0]["name"] == "graph-review"
        res = json.loads(
            get(f"{server}/api/v1/resolve?ref=core/graph-review@%5E1&runtime=pydantic")[2]
        )
        assert (
            res["ok"]
            and res["selected"]["version"] == "1.0.0"
            and "Selected 1.0.0" in res["explanation"]
        )
        assert "graph.query" in json.loads(get(f"{server}/api/v1/capabilities")[2])["capabilities"]

    def test_errors(self, server: str) -> None:
        assert get(f"{server}/api/v1/artifacts/skill/core/nope")[0] == 404
        assert get(f"{server}/api/v1/nonsense")[0] == 404
        s, _, b = get(f"{server}/api/v1/resolve")
        assert s == 400 and json.loads(b)["error"]["message"] == "ref is required"

    def test_writes_are_disabled(self, server: str) -> None:
        for method in ("POST", "PUT", "DELETE", "PATCH"):
            s, h, b = get(
                f"{server}/api/v1/artifacts", method, b"{}" if method != "DELETE" else None
            )
            assert (
                s == 405
                and h.get("Allow") == "GET, HEAD"
                and json.loads(b)["error"]["code"] == "READ_ONLY"
            )

    def test_static_docs_and_security_headers(self, server: str) -> None:
        s, h, b = get(f"{server}/")
        assert (
            s == 200
            and b"Ananke Plexus Registry" in b
            and h["Content-Type"].startswith("text/html")
        )
        assert (
            h["X-Content-Type-Options"] == "nosniff"
            and "default-src 'none'" in h["Content-Security-Policy"]
        )
        assert h["Cache-Control"] == "no-store"
        assert get(f"{server}/skills/core/graph-review/")[0] == 200
        assert get(f"{server}/missing.html")[0] == 404
        assert get(f"{server}/", "HEAD")[2] == b""

    def test_path_traversal_blocked(self, server: str) -> None:
        for path in (
            "/../registry.db",
            "/..%2fregistry.db",
            "/%2e%2e/registry.db",
            "/assets/../../registry.db",
        ):
            s, _, b = get(f"{server}{path}")
            assert s in {403, 404} and b"SQLite" not in b

    def test_query_length_limit(self, server: str) -> None:
        assert get(f"{server}/api/v1/search?q={'a' * 5000}")[0] == 414


class TestMcp:
    def _project(self, tmp_path: Path) -> Path:
        proj = tmp_path / "p"
        reg = Registry.for_project(proj, create=True, actor="t")
        reg.init()
        catalogue(reg)
        reg.close()
        return proj

    def test_tools_registered_read_only(self) -> None:
        from ananke.plexus.mcp.auth import DEFAULT_PERMISSION_MAP

        assert DEFAULT_PERMISSION_MAP.read_only_tools >= REGISTRY_TOOLS
        assert not (REGISTRY_TOOLS & DEFAULT_PERMISSION_MAP.mutation_tools)

    def test_tool_calls(self, tmp_path: Path) -> None:
        proj = self._project(tmp_path)
        out = call_registry_tool(proj, "registry_search", {"query": "graph", "kind": "skill"})
        assert out["ok"] and out["details"]["results"][0]["name"] == "graph-review"
        got = call_registry_tool(
            proj, "registry_get_skill", {"ref": "core/graph-review", "version": "^1"}
        )
        assert got["ok"] and got["details"]["version"] == "1.0.0"
        agent = call_registry_tool(proj, "registry_get_agent", {"ref": "engineering/coding-agent"})
        assert agent["ok"] and agent["details"]["kind"] == "agent"
        resolved = call_registry_tool(
            proj, "registry_resolve", {"ref": "engineering/coding-agent@^3", "kind": "agent"}
        )
        assert resolved["ok"] and len(resolved["details"]["nodes"]) == 3
        assert "Dependency graph" in resolved["details"]["explanation"]
        assert (
            "test.run"
            in call_registry_tool(proj, "registry_list_capabilities", {})["details"]["capabilities"]
        )

    def test_compare_and_errors(self, tmp_path: Path) -> None:
        proj = tmp_path / "p"
        reg = Registry.for_project(proj, create=True, actor="t")
        reg.init()
        add(reg, "x", "1.0.0", capabilities=["graph.query"])
        add(
            reg,
            "x",
            "1.1.0",
            capabilities=["graph.query", "graph.write"],
            files={"README.md": b"new"},
        )
        reg.close()
        cmp_ = call_registry_tool(
            proj, "registry_compare_versions", {"a": "core/x@1.0.0", "b": "core/x@1.1.0"}
        )
        assert (
            cmp_["ok"] and "MINOR" in cmp_["summary"] and "+ graph.write" in cmp_["details"]["text"]
        )
        assert not call_registry_tool(proj, "registry_compare_versions", {"a": "core/x@1.0.0"})[
            "ok"
        ]
        bad = call_registry_tool(proj, "registry_get_skill", {"ref": "core/missing"})
        assert not bad["ok"] and bad["details"]["code"] == "NOT_FOUND"
        assert not call_registry_tool(tmp_path / "none", "registry_search", {})["ok"]
        assert not call_registry_tool(proj, "registry_nonsense", {})["ok"]

    def test_resources(self, tmp_path: Path) -> None:
        proj = self._project(tmp_path)
        uris = list_registry_resources(proj)
        assert (
            "ananke://registry/skills" in uris
            and "ananke://registry/skill/core/graph-review/1.0.0" in uris
        )
        assert "ananke://registry/agent/engineering/coding-agent/3.2.0" in uris
        listing = json.loads(read_registry_resource(proj, "ananke://registry/skills"))
        assert {i["name"] for i in listing["items"]} == {"graph-review", "test-runner", "git-safe"}
        one = json.loads(
            read_registry_resource(proj, "ananke://registry/skill/core/graph-review/1.0.0")
        )
        assert one["ref"] == "core/graph-review@1.0.0"
        assert read_registry_resource(proj, "ananke://registry/skill/core/none/1.0.0").startswith(
            "registry error"
        )
        assert read_registry_resource(proj, "ananke://registry/other") == "resource not found"

    def test_wired_into_mcp_server(self, tmp_path: Path) -> None:
        from ananke.plexus.mcp.server import handle_message

        proj = self._project(tmp_path)
        assert "registry_search" in handle_message(proj, {"action": "tools.list"})["result"]
        r = handle_message(
            proj, {"action": "tools.call", "tool": "registry_search", "args": {"query": "runner"}}
        )
        assert r["ok"] and r["result"]["details"]["results"][0]["name"] == "test-runner"
        res = handle_message(
            proj, {"action": "resources.read", "resource": "ananke://registry/agents"}
        )
        assert "coding-agent" in res["result"]
        assert (
            "ananke://registry/skills"
            in handle_message(proj, {"action": "resources.list"})["result"]
        )


class TestSchemas:
    def test_published_schemas(self, tmp_path: Path) -> None:
        schemas = registry_json_schemas()
        assert set(schemas) == {
            "skill-manifest",
            "agent-manifest",
            "bundle-manifest",
            "registry-export",
            "lockfile",
        }
        skill = schemas["skill-manifest"]
        assert skill["properties"]["kind"]["const"] == "skill"
        assert schemas["agent-manifest"]["properties"]["kind"]["const"] == "agent"
        assert "graph.query" in json.dumps(skill["properties"]["capabilities"])
        assert set(skill["required"]) >= {"kind", "namespace", "name", "version"}
        files = write_schemas(tmp_path / "schemas")
        assert len(files) == 5 and all(
            json.loads(f.read_text())["$id"].endswith(".schema.json") for f in files
        )

    def test_schema_accepts_a_valid_manifest_and_rejects_bad_one(self) -> None:
        jsonschema = pytest.importorskip("jsonschema")
        skill = registry_json_schemas()["skill-manifest"]
        good = {
            "kind": "skill",
            "namespace": "core",
            "name": "x",
            "version": "1.0.0",
            "capabilities": ["graph.query"],
        }
        jsonschema.validate(good, skill)
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate({**good, "kind": "agent"}, skill)
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate({**good, "capabilities": ["Bad Cap"]}, skill)


class TestSources:
    def test_statuses_and_priority(self, tmp_path: Path) -> None:
        proj = tmp_path / "p"
        reg = Registry.for_project(proj, create=True, actor="t")
        reg.init()
        reg.close()
        st = {s.name: s for s in source_statuses(proj)}
        assert list(st) == ["project", "user", "enterprise", "public"]
        assert st["project"].available and st["project"].type == "local"
        assert not st["enterprise"].enabled and "not configured" in st["enterprise"].note
        assert not st["public"].enabled and "disabled by policy" in st["public"].note

    def test_project_registry_wins_over_user(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        home = tmp_path / "home"
        monkeypatch.setattr(Path, "home", lambda: home)
        proj = tmp_path / "p"
        user = Registry.for_user(create=True, actor="u")
        user.init()
        add(user, "shared", "9.0.0")
        add(user, "user-only", "1.0.0")
        user.close()
        pr = Registry.for_project(proj, create=True, actor="t")
        pr.init()
        add(pr, "shared", "1.0.0")
        pr.close()
        name, reg, res = federated_resolve(proj, "core/shared")
        try:
            assert name == "project" and res.selected and res.selected.version == "1.0.0"
            assert res.nodes[0].source == "project-registry"
        finally:
            reg.close()
        name2, reg2, res2 = federated_resolve(proj, "core/user-only")
        try:
            assert name2 == "user" and res2.selected and res2.selected.version == "1.0.0"
        finally:
            reg2.close()
        with pytest.raises(Exception, match=r"not found|not registered|Could not"):
            federated_resolve(proj, "core/nowhere")

    def test_remote_sources_are_pull_only_and_public_needs_policy(self, tmp_path: Path) -> None:
        proj = tmp_path / "p"
        reg = Registry.for_project(proj, create=True, actor="t")
        reg.init()
        reg.close()
        policy = RegistryPolicy(
            sources={
                "enterprise": SourceConfig(
                    type="remote", url="https://registry.example.internal", enabled=True
                )
            }
        )
        # enabled remote sources never take part in (offline) resolution
        assert [n for n, _r in open_sources(proj, policy)] == ["project"]
        st = {s.name: s for s in source_statuses(proj, policy)}
        assert "pull-only" in st["enterprise"].note or "denied by policy" in st["enterprise"].note
        public = RegistryPolicy(
            sources={
                "public": SourceConfig(type="remote", url="https://public.example", enabled=True)
            }
        )
        with pytest.raises(PolicyViolationError, match="public"):
            open_sources(proj, public)


class TestWatcher:
    def _skill(self, root: Path, version: str = "1.0.0") -> None:
        root.mkdir(parents=True, exist_ok=True)
        (root / "ananke.yaml").write_text(
            f"kind: skill\nnamespace: core\nname: watched\nversion: {version}\nlicense: {{expression: MIT}}\n"
        )

    def test_detects_changes_but_does_not_publish(self, tmp_path: Path) -> None:
        proj = tmp_path / "p"
        reg = Registry.for_project(proj, create=True, actor="t")
        reg.init()
        self._skill(proj / ".ananke/skills/watched")
        rep = scan_once(reg)
        assert len(rep.changes) == 1 and rep.changes[0].action == "dry-run" and rep.registered == []
        assert reg.store.count_versions() == 0  # detect automatically, publish manually
        assert scan_once(reg).changes == []  # unchanged since last scan
        self._skill(proj / ".ananke/skills/watched", "1.1.0")
        again = scan_once(reg)
        assert len(again.changes) == 1 and again.changes[0].suggested_version == "1.1.0"
        assert read_pending(reg)[0].uri == "ananke://skill/core/watched"

    def test_auto_register_policy(self, tmp_path: Path) -> None:
        proj = tmp_path / "p"
        reg = Registry.for_project(
            proj, create=True, actor="t", policy=RegistryPolicy(auto_register=True)
        )
        reg.init()
        self._skill(proj / ".ananke/skills/watched")
        rep = scan_once(reg)
        assert rep.registered == ["ananke://skill/core/watched"] and reg.store.count_versions() == 1

    def test_installed_and_active_dirs_ignored(self, tmp_path: Path) -> None:
        proj = tmp_path / "p"
        reg = Registry.for_project(proj, create=True, actor="t")
        reg.init()
        self._skill(proj / ".ananke/skills/installed/x@1.0.0")
        assert scan_once(reg).changes == []


def test_kinds_enum_is_stable() -> None:
    assert [k.value for k in ArtifactKind] == [
        "skill",
        "agent",
        "tool",
        "workflow",
        "evaluator",
        "prompt",
        "policy",
        "bundle",
        "runtime-profile",
    ]
    assert ArtifactKind.POLICY.plural == "policies" and ArtifactKind.SKILL.plural == "skills"
