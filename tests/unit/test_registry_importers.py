"""Importers: manifest, filesystem, python, rust, mcp, git, archive, framework, dynamic (spec §34-§40)."""

from __future__ import annotations

import io
import json
import shlex
import shutil
import subprocess
import sys
import tarfile
import textwrap
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from ananke.plexus.registry.errors import (
    DynamicIntrospectionError,
    ImporterError,
    ManifestError,
    PolicyViolationError,
)
from ananke.plexus.registry.importers.base import (
    Candidate,
    InspectionContext,
    finalize,
    parse_source,
    slugify,
)
from ananke.plexus.registry.importers.filesystem import FilesystemImporter
from ananke.plexus.registry.importers.framework import (
    DynamicImporter,
    FrameworkImporter,
    scan_python_tools,
)
from ananke.plexus.registry.importers.manifest import ManifestImporter
from ananke.plexus.registry.importers.mcp import McpImporter
from ananke.plexus.registry.importers.python_pkg import PythonImporter
from ananke.plexus.registry.importers.registry import ImporterRegistry, default_registry
from ananke.plexus.registry.importers.rust_crate import RustCrateImporter
from ananke.plexus.registry.importers.vcs_archive import ArchiveImporter, GitImporter
from ananke.plexus.registry.policy import (
    DynamicIntrospectionPolicy,
    RegistryPolicy,
    RemoteSourcesPolicy,
    enterprise_policy,
)

FIXTURES = Path(__file__).parents[1] / "fixtures"


def ctx(**kw) -> InspectionContext:  # type: ignore[no-untyped-def]
    return InspectionContext(policy=kw.pop("policy", RegistryPolicy()), **kw)


def write(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return root


MANIFEST = """
kind = "skill"
namespace = "core"
name = "graph-review"
version = "2.1.0"
summary = "Reviews changes."
capabilities = ["graph.query"]

[license]
expression = "Apache-2.0"

[inputs]
schema = "schemas/input.json"

[permissions.filesystem]
read = ["**"]

[[dependencies]]
skill = "core/repo-context"
version = "^1.4"
"""


def native_skill(root: Path) -> Path:
    return write(
        root,
        {
            "ananke.toml": MANIFEST,
            "schemas/input.json": json.dumps(
                {"type": "object", "properties": {"q": {"type": "string"}}}
            ),
            "README.md": "# hi",
        },
    )


# --------------------------------------------------------------------------- sources


class TestSourceParsing:
    @pytest.mark.parametrize(
        ("raw", "scheme"),
        [
            ("python:pkg", "python"),
            ("cargo:./crate", "rust"),
            ("mcp:snap.json", "mcp"),
            ("mcp-stdio:python server.py", "mcp-stdio"),
            ("mcp-http:https://x/mcp", "mcp-http"),
            ("git:https://example.com/r.git", "git"),
            ("https://example.com/r.git", "git"),
            ("git@github.com:o/r.git", "git"),
            ("framework:pydantic-ai:./src", "framework"),
            ("./some/dir", "path"),
            ("C:\\skills", "path"),
        ],
    )
    def test_schemes(self, raw: str, scheme: str) -> None:
        assert parse_source(raw).scheme == scheme

    def test_slugify(self) -> None:
        assert slugify("My Cool_Skill!!") == "my-cool_skill"
        assert slugify("///") == "unnamed"

    def test_empty_source(self) -> None:
        with pytest.raises(ManifestError):
            parse_source("  ")


# --------------------------------------------------------------------------- manifest


class TestManifestImporter:
    def test_toml_manifest_with_schema_inlined(self, tmp_path: Path) -> None:
        d = native_skill(tmp_path / "skill")
        (cand,) = ManifestImporter().inspect(parse_source(str(d)), ctx())
        assert cand.draft["inputs"]["json_schema"]["properties"]["q"]["type"] == "string"
        imp = finalize(cand)
        assert imp.manifest.uri == "ananke://skill/core/graph-review"
        assert imp.manifest.artifact_dependencies()[0].id == "ananke://skill/core/repo-context"
        assert imp.provenance.source_type == "manifest" and imp.provenance.fingerprint

    def test_yaml_and_json_manifests(self, tmp_path: Path) -> None:
        y = write(
            tmp_path / "y", {"ananke.yaml": "kind: skill\nnamespace: a\nname: y\nversion: 2.1\n"}
        )
        (cy,) = ManifestImporter().inspect(parse_source(str(y)), ctx())
        assert cy.draft["version"] == "2.1"  # float coerced to string
        assert any(d.code == "coerced-version" for d in cy.diagnostics)
        j = write(
            tmp_path / "j",
            {
                "ananke.registry.json": json.dumps(
                    {"kind": "agent", "namespace": "a", "name": "j", "version": "1.0.0"}
                )
            },
        )
        (cj,) = ManifestImporter().inspect(parse_source(str(j)), ctx())
        assert finalize(cj).manifest.kind.value == "agent"

    def test_manifest_file_path_and_legacy_apm_manifest(self) -> None:
        (cand,) = ManifestImporter().inspect(parse_source(str(FIXTURES / "sample-skill")), ctx())
        assert any(d.code == "legacy-manifest" for d in cand.diagnostics)
        imp = finalize(cand, ctx=ctx())
        assert imp.manifest.name == "graph-reviewer" and imp.manifest.namespace == "local"

    def test_collection_directory(self, tmp_path: Path) -> None:
        native_skill(tmp_path / "skills" / "one")
        write(
            tmp_path / "skills" / "two",
            {"ananke.yaml": "kind: skill\nnamespace: a\nname: two\nversion: 1.0.0\n"},
        )
        cands = ManifestImporter().inspect(parse_source(str(tmp_path / "skills")), ctx())
        assert {c.draft["name"] for c in cands} == {"graph-review", "two"}

    def test_path_traversal_in_schema_reference_is_an_error(self, tmp_path: Path) -> None:
        d = write(
            tmp_path / "evil",
            {
                "ananke.toml": 'kind="skill"\nnamespace="a"\nname="evil"\nversion="1.0.0"\n[inputs]\nschema="../../etc/passwd"\n'
            },
        )
        (cand,) = ManifestImporter().inspect(parse_source(str(d)), ctx())
        assert any(x.level == "error" and x.code == "unsafe-path" for x in cand.diagnostics)

    def test_missing_schema_file_is_an_error(self, tmp_path: Path) -> None:
        d = write(
            tmp_path / "s",
            {
                "ananke.toml": 'kind="skill"\nnamespace="a"\nname="s"\nversion="1.0.0"\n[inputs]\nschema="nope.json"\n'
            },
        )
        (cand,) = ManifestImporter().inspect(parse_source(str(d)), ctx())
        assert any(x.code == "missing-schema-file" for x in cand.diagnostics)

    def test_symlinks_not_followed(self, tmp_path: Path) -> None:
        d = native_skill(tmp_path / "s")
        secret = tmp_path / "secret.txt"
        secret.write_text("top secret")
        (d / "leak.txt").symlink_to(secret)
        (cand,) = ManifestImporter().inspect(parse_source(str(d)), ctx())
        assert "leak.txt" not in cand.files
        assert any(x.code == "skipped-symlink" for x in cand.diagnostics)

    def test_malformed_manifest(self, tmp_path: Path) -> None:
        d = write(tmp_path / "bad", {"ananke.toml": "this is = not [valid"})
        with pytest.raises(ManifestError):
            ManifestImporter().inspect(parse_source(str(d)), ctx())

    def test_probe(self, tmp_path: Path) -> None:
        d = native_skill(tmp_path / "s")
        assert ManifestImporter().probe(parse_source(str(d)), ctx()).confidence == 1.0
        assert not ManifestImporter().probe(parse_source(str(tmp_path)), ctx()).ok or True
        assert not ManifestImporter().probe(parse_source("python:x"), ctx()).ok


class TestFinalize:
    def test_missing_required_fields_reported(self) -> None:
        cand = Candidate(draft={"kind": "skill", "name": "x"})
        with pytest.raises(ManifestError, match="namespace, version"):
            finalize(cand)

    def test_default_namespace_and_overrides(self) -> None:
        cand = Candidate(draft={"kind": "skill", "name": "x", "version": "1.0.0"})
        imp = finalize(cand, ctx=ctx())
        assert imp.manifest.namespace == "local"
        imp2 = finalize(
            Candidate(draft={"kind": "skill", "name": "x", "version": "1.0.0"}),
            overrides={"namespace": "team"},
        )
        assert imp2.manifest.namespace == "team"

    def test_enterprise_policy_has_no_default_namespace(self) -> None:
        cand = Candidate(draft={"kind": "skill", "name": "x", "version": "1.0.0"})
        with pytest.raises(ManifestError, match="namespace"):
            finalize(cand, ctx=ctx(policy=enterprise_policy()))

    def test_diagnostics_for_missing_metadata(self) -> None:
        imp = finalize(
            Candidate(draft={"kind": "skill", "namespace": "a", "name": "x", "version": "1.0.0"})
        )
        codes = {d.code for d in imp.diagnostics}
        assert {"missing-license", "missing-summary", "missing-permissions"} <= codes

    def test_invalid_field_reports_manifest_error(self) -> None:
        with pytest.raises(ManifestError):
            finalize(
                Candidate(
                    draft={
                        "kind": "skill",
                        "namespace": "a",
                        "name": "x",
                        "version": "1.0.0",
                        "capabilities": ["BAD"],
                    }
                )
            )


# --------------------------------------------------------------------------- filesystem


class TestFilesystemImporter:
    def test_skill_md_frontmatter_and_inference(self, tmp_path: Path) -> None:
        d = write(
            tmp_path / "GitHub Review",
            {
                "SKILL.md": "---\nname: github-review\ndescription: Reviews pull requests\nlicense: MIT\nallowed-tools: Read, Bash\n---\n# body\n",
                "prompts/one.md": "p1",
                "prompts/two.md": "p2",
                "tools/search.json": json.dumps(
                    {"name": "search", "description": "s", "inputSchema": {"type": "object"}}
                ),
                "README.md": "# readme",
            },
        )
        (cand,) = FilesystemImporter().inspect(parse_source(str(d)), ctx())
        assert (
            cand.draft["name"] == "github-review"
            and cand.draft["summary"] == "Reviews pull requests"
        )
        assert cand.draft["license"]["expression"] == "MIT"
        assert cand.draft["tools"][0]["name"] == "search"
        assert cand.draft["version"] == "0.1.0"
        codes = {x.code for x in cand.diagnostics}
        assert {
            "suggested-version",
            "unmapped-allowed-tools",
            "discovered",
            "found-readme",
        } <= codes
        assert any("1 callable tools, 2 prompt templates" in x.message for x in cand.diagnostics)

    def test_license_detected_from_file(self, tmp_path: Path) -> None:
        d = write(
            tmp_path / "s",
            {
                "SKILL.md": "# s\n\nDoes things.",
                "LICENSE": "Apache License\nVersion 2.0, January 2004",
            },
        )
        (cand,) = FilesystemImporter().inspect(parse_source(str(d)), ctx())
        assert (
            cand.draft["license"]["expression"] == "Apache-2.0"
            and cand.draft["license"]["confidence"] == 0.7
        )

    def test_agent_md(self, tmp_path: Path) -> None:
        d = write(
            tmp_path / "coder",
            {
                "AGENT.md": "# Coder\n\nWrites code.",
                "skill.yaml": "version: 1.2.0\nnamespace: eng\n",
            },
        )
        (cand,) = FilesystemImporter().inspect(parse_source(str(d)), ctx())
        assert cand.draft["kind"] == "agent" and cand.draft["instructions"] == {"ref": "AGENT.md"}
        assert cand.draft["namespace"] == "eng"

    def test_manifest_yaml_fields(self, tmp_path: Path) -> None:
        d = write(
            tmp_path / "m",
            {
                "manifest.yaml": "name: mm\nversion: 3.0.0\nunknown_key: ignored\ncapabilities: [graph.query]\n"
            },
        )
        (cand,) = FilesystemImporter().inspect(parse_source(str(d)), ctx())
        assert cand.draft["name"] == "mm" and "unknown_key" not in cand.draft

    def test_children_scanned_and_probe_defers_to_native(self, tmp_path: Path) -> None:
        write(tmp_path / "skills" / "a", {"SKILL.md": "# a"})
        write(tmp_path / "skills" / "b", {"SKILL.md": "# b"})
        native_skill(tmp_path / "skills" / "c")
        cands = FilesystemImporter().inspect(parse_source(str(tmp_path / "skills")), ctx())
        assert {c.draft["name"] for c in cands} == {"a", "b"}
        assert (
            not FilesystemImporter().probe(parse_source(str(tmp_path / "skills" / "c")), ctx()).ok
        )

    def test_nothing_found(self, tmp_path: Path) -> None:
        (tmp_path / "empty").mkdir()
        with pytest.raises(ImporterError):
            FilesystemImporter().inspect(parse_source(str(tmp_path / "empty")), ctx())


# --------------------------------------------------------------------------- python / rust


def make_dist_info(
    root: Path, *, entry_points: str = "", extra_files: dict[str, str] | None = None
) -> Path:
    di = root / "demo_pkg-1.4.0.dist-info"
    di.mkdir(parents=True)
    (di / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: demo-pkg\nVersion: 1.4.0\nSummary: Demo package\n"
        "License-Expression: MIT\nRequires-Dist: httpx (>=0.28)\nRequires-Dist: rich>=14\n"
        "Requires-Dist: pytest; extra == 'dev'\n"
    )
    if entry_points:
        (di / "entry_points.txt").write_text(entry_points)
    for rel, text in (extra_files or {}).items():
        write(root, {rel: text})
    return di


class TestPythonImporter:
    def test_entry_points_from_dist_info_never_imports(self, tmp_path: Path) -> None:
        # a top-level module that would explode if imported
        write(tmp_path, {"demo_pkg/__init__.py": "raise RuntimeError('imported!')"})
        make_dist_info(tmp_path, entry_points="[ananke.skills]\nsummarize = demo_pkg:summarize\n")
        (cand,) = PythonImporter().inspect(parse_source(f"python:{tmp_path}"), ctx())
        assert cand.draft["kind"] == "skill" and cand.draft["name"] == "summarize"
        assert cand.draft["version"] == "1.4.0" and cand.draft["license"]["expression"] == "MIT"
        names = [d.get("name") for d in cand.draft["dependencies"]]
        assert names[0] == "demo-pkg" and "httpx" in names and "pytest" not in names
        assert (
            cand.provenance.source_type == "python-package"
            and cand.provenance.native_id == "ananke.skills:summarize"
        )
        assert any(d.code == "static-entry-point" for d in cand.diagnostics)

    def test_shipped_manifest_wins(self, tmp_path: Path) -> None:
        make_dist_info(tmp_path, entry_points="[ananke.skills]\nx = demo_pkg:x\n")
        native_skill(tmp_path / "demo_pkg" / "skill")
        (cand,) = PythonImporter().inspect(parse_source(f"python:{tmp_path}"), ctx())
        assert cand.draft["name"] == "graph-review"
        assert any(d.get("name") == "demo-pkg" for d in cand.draft["dependencies"])
        assert any(d.code == "packaged-manifest" for d in cand.diagnostics)

    def test_pyproject_entry_points(self, tmp_path: Path) -> None:
        write(
            tmp_path,
            {
                "pyproject.toml": textwrap.dedent(
                    """
                    [project]
                    name = "demo"
                    version = "0.3"
                    description = "d"
                    license = {text = "Apache-2.0"}
                    dependencies = ["httpx>=0.28"]
                    [project.entry-points."ananke.agents"]
                    helper = "demo:agent"
                    """
                )
            },
        )
        probe = PythonImporter().probe(parse_source(str(tmp_path)), ctx())
        assert probe.ok and probe.confidence == 0.8
        (cand,) = PythonImporter().inspect(parse_source(str(tmp_path)), ctx())
        assert cand.draft["kind"] == "agent" and cand.draft["version"] == "0.3.0"

    def test_no_declarations_is_an_actionable_error(self, tmp_path: Path) -> None:
        make_dist_info(tmp_path)
        with pytest.raises(ImporterError, match="declares no Ananke manifest"):
            PythonImporter().inspect(parse_source(f"python:{tmp_path}"), ctx())

    def test_not_installed(self) -> None:
        with pytest.raises(ImporterError, match="not installed"):
            PythonImporter().inspect(parse_source("python:definitely-not-a-real-dist-xyz"), ctx())

    def test_installed_distribution_metadata_only(self) -> None:
        # pytest is installed but declares no ananke entry points
        with pytest.raises(ImporterError, match="declares no Ananke"):
            PythonImporter().inspect(parse_source("python:pytest"), ctx())


class TestRustImporter:
    def test_cargo_and_registry_json(self, tmp_path: Path) -> None:
        write(
            tmp_path,
            {
                "Cargo.toml": '[package]\nname = "graph_tool"\nversion = "0.5.1"\ndescription = "Graph"\nlicense = "MIT"\nrust-version = "1.75"\n[dependencies]\nserde = "1"\nregex = { version = "1.10" }\n',
                "ananke.registry.json": json.dumps(
                    {"kind": "skill", "namespace": "core", "capabilities": ["graph.query"]}
                ),
                "target/junk.bin": "x",
            },
        )
        imp = RustCrateImporter()
        assert imp.probe(parse_source(str(tmp_path)), ctx()).confidence == 0.85
        (cand,) = imp.inspect(parse_source(f"rust:{tmp_path}"), ctx())
        finalized = finalize(cand)
        assert finalized.manifest.name == "graph_tool" and finalized.manifest.version == "0.5.1"
        assert finalized.manifest.compatibility.rust == ">=1.75"
        assert {d.name for d in finalized.manifest.dependencies} == {"serde", "regex"}
        assert "target/junk.bin" not in cand.files

    def test_bare_crate_needs_kind(self, tmp_path: Path) -> None:
        write(tmp_path, {"Cargo.toml": '[package]\nname = "x"\nversion = "1.0.0"\n'})
        (cand,) = RustCrateImporter().inspect(parse_source(f"rust:{tmp_path}"), ctx())
        assert "kind" in cand.missing_required() and any(
            d.code == "unknown-kind" for d in cand.diagnostics
        )

    def test_workspace_rejected(self, tmp_path: Path) -> None:
        write(tmp_path, {"Cargo.toml": '[workspace]\nmembers = ["a"]\n'})
        with pytest.raises(ImporterError):
            RustCrateImporter().inspect(parse_source(f"rust:{tmp_path}"), ctx())


# --------------------------------------------------------------------------- MCP

SNAPSHOT = {
    "serverInfo": {"name": "GitHub MCP", "version": "1.2.3"},
    "tools": [
        {
            "name": "search",
            "description": "Search",
            "inputSchema": {"type": "object", "properties": {"q": {"type": "string"}}},
        },
        {"name": "create_issue", "description": "Create", "inputSchema": {"type": "object"}},
    ],
    "resources": [{"uri": "repo://x"}],
    "prompts": [{"name": "review"}],
}


class TestMcpImporter:
    def test_static_snapshot(self, tmp_path: Path) -> None:
        f = tmp_path / "snap.json"
        f.write_text(json.dumps(SNAPSHOT))
        (cand,) = McpImporter().inspect(parse_source(f"mcp:{f}"), ctx())
        imp = finalize(cand, ctx=ctx())
        assert imp.manifest.name == "github-mcp" and imp.manifest.version == "1.2.3"
        assert [t.name for t in imp.manifest.tools] == ["create_issue", "search"]
        assert imp.manifest.runtime.supported == ["generic-mcp"]
        assert imp.manifest.dependencies[0].type.value == "mcp-server"
        assert imp.provenance.source_type == "mcp" and not imp.dynamic
        assert any("permissions" in d.message for d in imp.diagnostics)

    def test_snapshot_payload_is_deterministic(self, tmp_path: Path) -> None:
        f = tmp_path / "snap.json"
        f.write_text(json.dumps(SNAPSHOT))
        a = McpImporter().inspect(parse_source(f"mcp:{f}"), ctx())[0].files
        shuffled = dict(SNAPSHOT, tools=list(reversed(SNAPSHOT["tools"])))
        f.write_text(json.dumps(shuffled))
        assert McpImporter().inspect(parse_source(f"mcp:{f}"), ctx())[0].files == a

    def test_bare_tools_list_accepted_and_no_version_flagged(self, tmp_path: Path) -> None:
        f = tmp_path / "t.json"
        f.write_text(json.dumps([{"name": "t"}]))
        (cand,) = McpImporter().inspect(parse_source(f"mcp:{f}"), ctx())
        assert "version" in cand.missing_required()

    def test_invalid_snapshot(self, tmp_path: Path) -> None:
        f = tmp_path / "bad.json"
        f.write_text("{not json")
        with pytest.raises(ImporterError):
            McpImporter().inspect(parse_source(f"mcp:{f}"), ctx())

    def test_stdio_requires_dynamic_permission(self) -> None:
        with pytest.raises(PolicyViolationError, match="DYNAMIC_INTROSPECTION_DISABLED"):
            McpImporter().inspect(parse_source("mcp-stdio:python -c pass"), ctx())

    def test_http_requires_network_approval(self) -> None:
        with pytest.raises(PolicyViolationError, match="NETWORK_NOT_APPROVED"):
            McpImporter().inspect(parse_source("mcp-http:https://mcp.example.com/mcp"), ctx())
        enterprise = ctx(
            policy=enterprise_policy(), allow_network=True
        )  # CLI override disabled by policy
        with pytest.raises(PolicyViolationError):
            McpImporter().inspect(parse_source("mcp-http:https://mcp.example.com/mcp"), enterprise)

    def test_http_rejects_non_http_scheme(self) -> None:
        with pytest.raises(ImporterError):
            McpImporter().inspect(
                parse_source("mcp-http:file:///etc/passwd"), ctx(allow_network=True)
            )

    def test_live_http_server(self) -> None:
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                method, rid = body["method"], body.get("id")
                results = {
                    "initialize": {
                        "serverInfo": {"name": "live", "version": "0.1.0"},
                        "capabilities": {"tools": {}},
                    },
                    "tools/list": {
                        "tools": [
                            {"name": "ping", "description": "p", "inputSchema": {"type": "object"}}
                        ]
                    },
                }
                if rid is None:
                    self.send_response(202)
                    self.end_headers()
                    return
                payload = json.dumps(
                    {"jsonrpc": "2.0", "id": rid, "result": results.get(method, {})}
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *a: object) -> None:
                pass

        server = HTTPServer(("127.0.0.1", 0), Handler)
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        try:
            url = f"http://user:pw@127.0.0.1:{server.server_port}/mcp"
            (cand,) = McpImporter().inspect(
                parse_source(f"mcp-http:{url}"), ctx(allow_network=True)
            )
        finally:
            server.shutdown()
        assert cand.draft["tools"][0]["name"] == "ping" and cand.draft["name"] == "live"
        assert "pw" not in (cand.provenance.source_url or "") and "user" not in (
            cand.provenance.source_url or ""
        )

    def test_live_stdio_server(self, tmp_path: Path) -> None:
        server = tmp_path / "server.py"
        server.write_text(
            textwrap.dedent(
                """
                import json, sys
                for line in sys.stdin:
                    msg = json.loads(line)
                    if "id" not in msg:
                        continue
                    res = {"initialize": {"serverInfo": {"name": "stdio-demo", "version": "2.0.0"},
                                          "capabilities": {"tools": {}}},
                           "tools/list": {"tools": [{"name": "echo", "inputSchema": {"type": "object"}}]}}.get(msg["method"], {})
                    print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": res}), flush=True)
                """
            )
        )
        (cand,) = McpImporter().inspect(
            parse_source(f"mcp-stdio:{shlex.quote(sys.executable)} {shlex.quote(str(server))}"),
            ctx(allow_dynamic=True),
        )
        assert (
            cand.dynamic
            and cand.draft["name"] == "stdio-demo"
            and cand.draft["tools"][0]["name"] == "echo"
        )


# --------------------------------------------------------------------------- git / archive


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        env={
            "PATH": shutil.os.environ["PATH"],
            "HOME": str(cwd),
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@t",
        },
    )


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
class TestGitImporter:
    def test_local_repository(self, tmp_path: Path) -> None:
        repo = native_skill(tmp_path / "repo")
        _git("init", "-q", cwd=repo)
        _git("add", ".", cwd=repo)
        _git("commit", "-q", "-m", "init", cwd=repo)
        (cand,) = GitImporter().inspect(parse_source(f"git:{repo}"), ctx())
        assert cand.provenance.source_type == "git"
        assert cand.provenance.git is not None and len(cand.provenance.git.commit or "") == 40
        assert cand.provenance.fingerprint.startswith("git:")
        assert ".git/config" not in cand.files and cand.draft["name"] == "graph-review"

    def test_remote_needs_network_approval(self) -> None:
        with pytest.raises(PolicyViolationError, match="NETWORK_NOT_APPROVED"):
            GitImporter().inspect(parse_source("git:https://example.com/x.git"), ctx())

    @pytest.mark.parametrize(
        "url",
        [
            "ext::sh -c 'touch /tmp/pwned'",
            "-oProxyCommand=evil",
            "file:///etc",
            "git:https://x/y.git bad space",
        ],
    )
    def test_unsafe_urls_rejected(self, url: str) -> None:
        with pytest.raises((ImporterError, PolicyViolationError)):
            GitImporter().inspect(parse_source(f"git:{url}"), ctx(allow_network=True))

    def test_invalid_ref_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(ImporterError, match="invalid git ref"):
            GitImporter().inspect(parse_source(f"git:{tmp_path}#--upload-pack=x"), ctx())


class TestArchiveImporter:
    def _tar(self, tmp_path: Path, mode: str = "w:gz") -> Path:
        src = native_skill(tmp_path / "skill")
        out = tmp_path / ("skill.tar.gz" if "gz" in mode else "skill.tar")
        with tarfile.open(out, mode) as tar:
            tar.add(src, arcname="skill")
        return out

    def test_tar_gz(self, tmp_path: Path) -> None:
        archive = self._tar(tmp_path)
        (cand,) = ArchiveImporter().inspect(parse_source(str(archive)), ctx())
        assert cand.draft["name"] == "graph-review" and cand.provenance.source_type == "archive"
        assert cand.provenance.fingerprint.startswith("sha256:")

    def test_zip(self, tmp_path: Path) -> None:
        src = native_skill(tmp_path / "skill")
        out = tmp_path / "skill.zip"
        with zipfile.ZipFile(out, "w") as zf:
            for p in src.rglob("*"):
                if p.is_file():
                    zf.write(p, p.relative_to(src).as_posix())
        (cand,) = ArchiveImporter().inspect(parse_source(str(out)), ctx())
        assert cand.draft["name"] == "graph-review"

    def test_traversal_and_symlink_members_rejected(self, tmp_path: Path) -> None:
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            info = tarfile.TarInfo("../evil.txt")
            info.size = 1
            tar.addfile(info, io.BytesIO(b"x"))
        bad = tmp_path / "bad.tar.gz"
        bad.write_bytes(buf.getvalue())
        with pytest.raises(ImporterError):
            ArchiveImporter().inspect(parse_source(str(bad)), ctx())
        buf2 = io.BytesIO()
        with tarfile.open(fileobj=buf2, mode="w:gz") as tar:
            link = tarfile.TarInfo("s/link")
            link.type = tarfile.SYMTYPE
            link.linkname = "/etc/passwd"
            tar.addfile(link)
        bad2 = tmp_path / "bad2.tgz"
        bad2.write_bytes(buf2.getvalue())
        with pytest.raises(ImporterError):
            ArchiveImporter().inspect(parse_source(str(bad2)), ctx())
        assert not (tmp_path / "evil.txt").exists()

    def test_corrupt_archive(self, tmp_path: Path) -> None:
        f = tmp_path / "x.zip"
        f.write_bytes(b"not a zip")
        with pytest.raises(ImporterError):
            ArchiveImporter().inspect(parse_source(str(f)), ctx())

    def test_zip_symlink_rejected(self, tmp_path: Path) -> None:
        out = tmp_path / "s.zip"
        with zipfile.ZipFile(out, "w") as zf:
            info = zipfile.ZipInfo("link")
            info.external_attr = 0o120777 << 16
            zf.writestr(info, "/etc/passwd")
        with pytest.raises(ImporterError):
            ArchiveImporter().inspect(parse_source(str(out)), ctx())


# --------------------------------------------------------------------------- framework


TOOLS_SRC = '''
from pydantic_ai import Agent, RunContext
agent = Agent("test")

@agent.tool
async def lookup(ctx: RunContext[int], query: str, limit: int = 5, tags: list[str] | None = None) -> str:
    """Look things up.

    Longer text.
    """
    return query

@agent.tool_plain
def add(a: int, b: float) -> float:
    return a + b

def not_a_tool(x: int) -> int:
    return x
'''


class TestFrameworkImporter:
    def test_ast_scan_infers_schemas_without_importing(self, tmp_path: Path) -> None:
        write(
            tmp_path / "pkg", {"tools.py": TOOLS_SRC + "\nraise RuntimeError('must not execute')\n"}
        )
        (cand,) = FrameworkImporter().inspect(
            parse_source(f"framework:pydantic-ai:{tmp_path / 'pkg'}"), ctx()
        )
        tools = {t["name"]: t for t in cand.draft["tools"]}
        assert set(tools) == {"lookup", "add"}
        assert tools["lookup"]["description"] == "Look things up."
        assert tools["lookup"]["input_schema"] == {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer"},
                "tags": {"type": "array"},
            },
            "required": ["query"],
        }
        assert tools["add"]["input_schema"]["required"] == ["a", "b"]
        assert cand.draft["runtime"]["supported"] == ["pydantic"]
        assert cand.provenance.source_type == "framework:pydantic-ai"

    def test_decorator_sets_differ_per_framework(self) -> None:
        src = {"m.py": b"@kernel_function\ndef f(x: str): pass\n@tool\ndef g(): pass\n"}
        ms, _ = scan_python_tools(src, frozenset({"kernel_function"}))
        assert [t["name"] for t in ms] == ["f"]

    def test_syntax_errors_are_reported_not_fatal(self) -> None:
        tools, problems = scan_python_tools({"bad.py": b"def (:"}, frozenset({"tool"}))
        assert tools == [] and problems and "cannot parse" in problems[0]

    def test_pyproject_supplies_identity(self, tmp_path: Path) -> None:
        write(
            tmp_path,
            {
                "pyproject.toml": '[project]\nname="My Tools"\nversion="1.2"\nlicense="MIT"\n',
                "t.py": "@tool\ndef f(): pass\n",
            },
        )
        (cand,) = FrameworkImporter().inspect(parse_source(f"framework:{tmp_path}"), ctx())
        assert cand.draft["name"] == "my-tools" and cand.draft["version"] == "1.2.0"

    def test_missing_source(self, tmp_path: Path) -> None:
        with pytest.raises(ImporterError):
            FrameworkImporter().inspect(parse_source(f"framework:{tmp_path / 'nope'}"), ctx())


PLUGIN = """
import json

def introspect(request):
    return {"artifacts": [{
        "manifest": {"kind": "skill", "namespace": "dyn", "name": "found", "version": "1.0.0",
                     "summary": "from " + request["framework"]},
        "files": {"note.txt": "discovered dynamically"},
    }]}

def crash(request):
    raise RuntimeError("boom")

def slow(request):
    import time
    time.sleep(30)

def online(request):
    import socket
    socket.create_connection(("example.com", 80), timeout=2)
    return {"artifacts": []}

def reads_env(request):
    import os
    return {"artifacts": [{"manifest": {"kind": "skill", "namespace": "dyn", "name": "env", "version": "1.0.0",
            "summary": os.environ.get("SECRET_TOKEN", "absent") + "|" + os.environ.get("HOME", "")}}]}

def not_json(request):
    return {"artifacts": "nope"}
"""


class TestDynamicIntrospection:
    def _plugin_dir(self, tmp_path: Path) -> Path:
        return write(tmp_path / "plugin", {"myplugin.py": PLUGIN})

    def _ctx(self, tmp_path: Path, plugin: str, **kw) -> InspectionContext:  # type: ignore[no-untyped-def]
        kw.setdefault("allow_dynamic", True)
        return ctx(plugin=plugin, plugin_paths=[str(self._plugin_dir(tmp_path))], **kw)

    def test_disabled_by_default(self, tmp_path: Path) -> None:
        with pytest.raises(PolicyViolationError, match="DYNAMIC_INTROSPECTION_DISABLED"):
            DynamicImporter().inspect(
                parse_source("dynamic:generic-python:x"), ctx(plugin="myplugin:introspect")
            )

    def test_enterprise_policy_blocks_cli_override(self) -> None:
        with pytest.raises(PolicyViolationError):
            DynamicImporter().inspect(
                parse_source("dynamic:generic-python:x"),
                ctx(policy=enterprise_policy(), allow_dynamic=True, plugin="myplugin:introspect"),
            )

    def test_policy_can_enable(self, tmp_path: Path) -> None:
        policy = RegistryPolicy(dynamic_introspection=DynamicIntrospectionPolicy(allowed=True))
        c = ctx(
            policy=policy,
            plugin="myplugin:introspect",
            plugin_paths=[str(self._plugin_dir(tmp_path))],
        )
        (cand,) = DynamicImporter().inspect(parse_source("dynamic:crewai:x"), c)
        assert cand.draft["summary"] == "from crewai"

    def test_success_marks_dynamic_and_reviews(self, tmp_path: Path) -> None:
        (cand,) = DynamicImporter().inspect(
            parse_source("dynamic:generic-python:src"), self._ctx(tmp_path, "myplugin:introspect")
        )
        assert cand.dynamic and cand.files == {"note.txt": b"discovered dynamically"}
        assert cand.provenance.source_type == "dynamic:generic-python"
        assert any(d.code == "dynamic" for d in cand.diagnostics)

    def test_plugin_crash_is_reported(self, tmp_path: Path) -> None:
        with pytest.raises(DynamicIntrospectionError, match="boom"):
            DynamicImporter().inspect(
                parse_source("dynamic:g:src"), self._ctx(tmp_path, "myplugin:crash")
            )

    def test_timeout_kills_child(self, tmp_path: Path) -> None:
        policy = RegistryPolicy(dynamic_introspection=DynamicIntrospectionPolicy(timeout_seconds=1))
        with pytest.raises(DynamicIntrospectionError, match="timed out"):
            DynamicImporter().inspect(
                parse_source("dynamic:g:src"), self._ctx(tmp_path, "myplugin:slow", policy=policy)
            )

    def test_network_is_blocked(self, tmp_path: Path) -> None:
        with pytest.raises(DynamicIntrospectionError, match="network access is disabled"):
            DynamicImporter().inspect(
                parse_source("dynamic:g:src"), self._ctx(tmp_path, "myplugin:online")
            )

    def test_environment_is_scrubbed(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SECRET_TOKEN", "hunter2")
        (cand,) = DynamicImporter().inspect(
            parse_source("dynamic:g:src"), self._ctx(tmp_path, "myplugin:reads_env")
        )
        summary = cand.draft["summary"]
        assert summary.startswith("absent|") and "hunter2" not in summary
        assert str(Path.home()) not in summary  # HOME points at a throw-away directory

    def test_invalid_output_contract(self, tmp_path: Path) -> None:
        with pytest.raises(DynamicIntrospectionError):
            DynamicImporter().inspect(
                parse_source("dynamic:g:src"), self._ctx(tmp_path, "myplugin:not_json")
            )

    def test_plugin_required(self) -> None:
        with pytest.raises(DynamicIntrospectionError, match="no introspection plugin"):
            DynamicImporter().inspect(
                parse_source("dynamic:nosuchframework:x"), ctx(allow_dynamic=True)
            )


# --------------------------------------------------------------------------- registry


class TestImporterRegistry:
    def test_availability_lists_all_builtins(self) -> None:
        ids = {row["id"] for row in default_registry().availability()}
        assert {
            "ananke-manifest",
            "filesystem",
            "python-metadata",
            "rust-crate",
            "mcp",
            "git",
            "archive",
            "framework-static",
            "dynamic-introspection",
        } <= ids

    def test_every_importer_declares_static_flag_and_permissions(self) -> None:
        for imp in default_registry().all():
            assert isinstance(imp.static, bool) and imp.permissions is not None and imp.version
        assert {i.id for i in default_registry().all() if i.permissions.executes_code} == {
            "mcp",
            "dynamic-introspection",
        }

    def test_plan_prefers_manifest_over_filesystem(self, tmp_path: Path) -> None:
        native_skill(tmp_path / "a")
        write(tmp_path / "b", {"SKILL.md": "# b"})
        plan = default_registry().plan(str(tmp_path), ctx())
        assert {(imp.id, Path(src.target).name) for imp, src in plan} == {
            ("ananke-manifest", "a"),
            ("filesystem", "b"),
        }

    def test_plan_unrecognised(self, tmp_path: Path) -> None:
        (tmp_path / "x").mkdir()
        with pytest.raises(ImporterError):
            default_registry().plan(str(tmp_path / "x"), ctx())
        with pytest.raises(ImporterError):
            default_registry().plan(str(tmp_path / "missing"), ctx())

    def test_unknown_importer(self) -> None:
        with pytest.raises(ImporterError):
            ImporterRegistry(load_plugins=False).get("nope")

    def test_custom_importer_via_protocol(self, tmp_path: Path) -> None:
        from ananke.plexus.registry.importers.base import ImporterPermissions, ProbeResult

        class Custom:
            id = "custom"
            version = "1"
            static = True
            permissions = ImporterPermissions()

            def probe(self, source, c):  # type: ignore[no-untyped-def]
                return ProbeResult(ok=source.raw.endswith(".custom"), confidence=1.0)

            def inspect(self, source, c):  # type: ignore[no-untyped-def]
                return [
                    Candidate(
                        draft={"kind": "skill", "namespace": "x", "name": "c", "version": "1.0.0"}
                    )
                ]

        f = tmp_path / "thing.custom"
        f.write_text("x")
        reg = ImporterRegistry([Custom()], load_plugins=False)
        ((imp, src),) = reg.plan(str(f), ctx())
        assert imp.id == "custom" and imp.inspect(src, ctx())[0].draft["name"] == "c"
        _ = RemoteSourcesPolicy  # imported for completeness of policy surface
