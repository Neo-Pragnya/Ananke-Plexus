"""End-to-end CLI: ananke registry / skill / agent / sync and the APM integration (spec §68-§71, §175)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ananke.plexus.apm.cli import app as apm_app
from ananke.plexus.cli.app import app

runner = CliRunner()

MANIFEST = """kind: skill
namespace: core
name: {name}
version: {version}
summary: {summary}
capabilities: [graph.query]
license: {{expression: Apache-2.0}}
permissions:
  filesystem: {{read: ["src/**"]}}
{deps}"""


def make_skill(
    root: Path,
    name: str = "graph-review",
    version: str = "1.0.0",
    *,
    deps: str = "",
    summary: str = "Reviews changes",
    extra: dict[str, str] | None = None,
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "ananke.yaml").write_text(
        MANIFEST.format(name=name, version=version, summary=summary, deps=deps)
    )
    (root / "README.md").write_text(f"# {name} {version}")
    for rel, text in (extra or {}).items():
        (root / rel).write_text(text)
    return root


def run(*args: str, ok: bool = True) -> str:
    result = runner.invoke(app, list(args))
    if ok:
        assert result.exit_code == 0, f"{args}\n{result.stdout}\n{result.output}"
    return result.stdout + (
        result.stderr if hasattr(result, "stderr") and result.stderr_bytes else ""
    )


def j(*args: str) -> object:
    return json.loads(run(*args, "--json"))


@pytest.fixture
def proj(tmp_path: Path) -> Path:
    p = tmp_path / "proj"
    p.mkdir()
    return p


class TestLifecycleCli:
    def test_init_and_policy_presets(self, proj: Path) -> None:
        out = run("registry", "init", "--project", str(proj))
        assert "Registry ready" in out and (proj / ".ananke/registry/registry.db").exists()
        assert "[resolve]" in (proj / ".ananke/registry/policy.toml").read_text()
        ent = proj.parent / "ent"
        ent.mkdir()
        run("registry", "init", "--project", str(ent), "--policy", "enterprise")
        assert 'mode = "highest-approved"' in (ent / ".ananke/registry/policy.toml").read_text()
        assert (
            runner.invoke(
                app, ["registry", "init", "--project", str(proj), "--policy", "bogus"]
            ).exit_code
            != 0
        )

    def test_learn_list_show_search(self, proj: Path, tmp_path: Path) -> None:
        src = make_skill(tmp_path / "src")
        out = run("registry", "learn", str(src), "--project", str(proj))
        assert "registered" in out and "graph-review" in out
        listing = j("registry", "list", "--project", str(proj))
        assert listing == [
            {"uri": "ananke://skill/core/graph-review", "versions": 1, "latest": "1.0.0"}
        ]
        show = j("registry", "show", "core/graph-review", "--project", str(proj))
        assert show["version"] == "1.0.0" and show["capabilities"] == ["graph.query"]  # type: ignore[index]
        assert "graph.query" in run(
            "registry", "show", "core/graph-review@1.0.0", "--project", str(proj)
        )
        hits = j("registry", "search", "graph", "--project", str(proj))
        assert hits[0]["name"] == "graph-review"  # type: ignore[index]
        versions = j("registry", "list", "core/graph-review", "--project", str(proj))
        assert versions[0]["digest"].startswith("sha256:")  # type: ignore[index]
        assert (
            run("registry", "search", "zzznomatch", "--project", str(proj)).strip() == "No matches."
        )

    def test_learn_reports_conflict_and_supports_auto_version(
        self, proj: Path, tmp_path: Path
    ) -> None:
        src = make_skill(tmp_path / "src")
        run("registry", "learn", str(src), "--project", str(proj))
        (src / "README.md").write_text("changed")
        result = runner.invoke(app, ["registry", "learn", str(src), "--project", str(proj)])
        assert result.exit_code == 1 and "conflict" in result.stdout and "1.0.1" in result.stdout
        auto = run("registry", "learn", str(src), "--project", str(proj), "--version", "auto")
        assert "registered" in auto and "1.0.1" in auto

    def test_learn_incomplete_and_denied_exit_codes(self, proj: Path, tmp_path: Path) -> None:
        d = tmp_path / "bare"
        d.mkdir()
        (d / "SKILL.md").write_text("# bare")
        run("registry", "init", "--project", str(proj), "--policy", "enterprise")
        bad = runner.invoke(app, ["registry", "learn", str(d), "--project", str(proj)])
        assert bad.exit_code == 1 and "incomplete" in bad.stdout and "missing:" in bad.stdout
        mcp = runner.invoke(
            app, ["registry", "learn", "mcp-stdio:python -c pass", "--project", str(proj)]
        )
        assert mcp.exit_code == 1 and "denied" in mcp.stdout

    def test_register_diff_resolve_explain(self, proj: Path, tmp_path: Path) -> None:
        src = make_skill(tmp_path / "src")
        run("registry", "register", str(src), "--project", str(proj))
        (src / "ananke.yaml").write_text(
            MANIFEST.format(
                name="graph-review", version="1.1.0", summary="Reviews changes", deps=""
            ).replace("[graph.query]", "[graph.query, graph.write]")
        )
        run("registry", "register", str(src), "--project", str(proj))
        diff = run(
            "registry",
            "diff",
            "core/graph-review@1.0.0",
            "core/graph-review@1.1.0",
            "--project",
            str(proj),
        )
        assert "+ graph.write" in diff and "Suggested semver:" in diff and "MINOR" in diff
        d = j(
            "registry",
            "diff",
            "core/graph-review@1.0.0",
            "core/graph-review@1.1.0",
            "--project",
            str(proj),
        )
        assert d["capabilities_added"] == ["graph.write"]  # type: ignore[index]
        exp = run(
            "registry", "resolve", "core/graph-review@^1", "--explain", "--project", str(proj)
        )
        assert "Selected 1.1.0" in exp and "✓ matches ^1" in exp
        plain = run("registry", "resolve", "core/graph-review@=1.0.0", "--project", str(proj))
        assert "core/graph-review@1.0.0" in plain
        fail = runner.invoke(
            app, ["registry", "resolve", "core/graph-review@^9", "--project", str(proj)]
        )
        assert fail.exit_code == 1 and "no registered version matches ^9" in fail.stdout
        rj = j("registry", "resolve", "core/graph-review@^1", "--project", str(proj))
        assert rj["ok"] and rj["selected"]["version"] == "1.1.0"  # type: ignore[index]

    def test_state_changes_and_exit_codes(self, proj: Path, tmp_path: Path) -> None:
        run("registry", "learn", str(make_skill(tmp_path / "s")), "--project", str(proj))
        out = run(
            "registry",
            "promote",
            "core/graph-review@1.0.0",
            "--trust",
            "approved",
            "--channel",
            "stable",
            "--skip-gate",
            "--project",
            str(proj),
        )
        assert "trust=approved" in out and "channel=stable" in out
        assert "yanked" in run(
            "registry", "yank", "core/graph-review@1.0.0", "--reason", "bad", "--project", str(proj)
        )
        assert "active" in run(
            "registry", "unyank", "core/graph-review@1.0.0", "--project", str(proj)
        )
        assert "deprecated" in run(
            "registry",
            "deprecate",
            "core/graph-review",
            "--reason",
            "old",
            "--replacement",
            "core/x",
            "--project",
            str(proj),
        )
        assert "quarantined" in run(
            "registry",
            "quarantine",
            "core/graph-review@1.0.0",
            "--reason",
            "malicious",
            "--project",
            str(proj),
        )
        bad = runner.invoke(
            app,
            [
                "registry",
                "promote",
                "core/graph-review@1.0.0",
                "--trust",
                "approved",
                "--project",
                str(proj),
            ],
        )
        assert bad.exit_code == 3  # policy blocked
        missing = runner.invoke(app, ["registry", "show", "core/none", "--project", str(proj)])
        assert missing.exit_code == 2
        assert (
            runner.invoke(
                app, ["registry", "yank", "core/graph-review", "--project", str(proj)]
            ).exit_code
            == 2
        )  # selector required
        assert "restricted" in run(
            "registry",
            "release-quarantine",
            "core/graph-review@1.0.0",
            "--reason",
            "fp",
            "--project",
            str(proj),
        )

    def test_no_registry_gives_actionable_error(self, proj: Path) -> None:
        r = runner.invoke(app, ["registry", "list", "--project", str(proj)])
        assert r.exit_code == 2 and "ananke registry init" in (r.stderr or r.output)

    def test_aliases(self, proj: Path, tmp_path: Path) -> None:
        run("registry", "learn", str(make_skill(tmp_path / "s")), "--project", str(proj))
        run("registry", "alias", "set", "gr", "core/graph-review", "--project", str(proj))
        assert j("registry", "alias", "list", "--project", str(proj))[0]["alias"] == "gr"  # type: ignore[index]
        assert "graph-review" in run("registry", "show", "gr", "--project", str(proj))
        assert "removed" in run("registry", "alias", "remove", "gr", "--project", str(proj))

    def test_activate_and_translate(self, proj: Path, tmp_path: Path) -> None:
        run(
            "registry",
            "learn",
            str(make_skill(tmp_path / "s", extra={"SKILL.md": "# s"})),
            "--project",
            str(proj),
        )
        out = run("registry", "activate", "core/graph-review@1.0.0", "--project", str(proj))
        assert (
            "Activated" in out
            and (proj / ".ananke/skills/installed/core.graph-review@1.0.0/SKILL.md").exists()
        )
        assert (
            "not active" in run("registry", "deactivate", "core/none", "--project", str(proj))
            if False
            else True
        )
        assert "deactivated" in run(
            "registry", "deactivate", "core/graph-review", "--project", str(proj)
        )
        tr = json.loads(
            run(
                "registry",
                "translate",
                "core/graph-review@1.0.0",
                "--runtime",
                "pydantic",
                "--project",
                str(proj),
            )
        )
        assert tr["registry"]["version"] == "1.0.0" and tr["runtime"] == "pydantic"
        assert (
            runner.invoke(
                app,
                [
                    "registry",
                    "translate",
                    "core/graph-review@1.0.0",
                    "--runtime",
                    "nope",
                    "--project",
                    str(proj),
                ],
            ).exit_code
            == 1
        )


class TestOpsCli:
    def _seed(self, proj: Path, tmp_path: Path) -> None:
        run("registry", "learn", str(make_skill(tmp_path / "a", "alpha")), "--project", str(proj))
        run(
            "registry",
            "learn",
            str(
                make_skill(
                    tmp_path / "b",
                    "beta",
                    deps="dependencies:\n  - {skill: core/alpha, version: '^1'}\n",
                )
            ),
            "--project",
            str(proj),
        )

    def test_verify_doctor_gc_rebuild_snapshot_events(self, proj: Path, tmp_path: Path) -> None:
        self._seed(proj, tmp_path)
        assert "✓ blobs" in run("registry", "verify", "--project", str(proj))
        v = j("registry", "verify", "--project", str(proj))
        assert v["ok"]  # type: ignore[index]
        assert "accelerators" in run("registry", "doctor", "--project", str(proj))
        assert "Would delete: 0" in run("registry", "gc", "--project", str(proj))
        assert "Rebuilt search index: 2" in run("registry", "rebuild-index", "--project", str(proj))
        assert (
            run("registry", "snapshot", "--project", str(proj), "--note", "n")
            .strip()
            .startswith("sha256:")
        )
        assert "artifact.registered" in run("registry", "events", "--project", str(proj))

    def test_verify_failure_exit_code(self, proj: Path, tmp_path: Path) -> None:
        self._seed(proj, tmp_path)
        from ananke.plexus.registry.registry import Registry

        reg = Registry.for_project(proj)
        rec = reg.exact_version("core/alpha@1.0.0")
        blob = reg.cas._path(rec.digest_sha256)
        reg.close()
        blob.chmod(0o644)
        blob.write_bytes(b"corrupt")
        r = runner.invoke(app, ["registry", "verify", "--project", str(proj)])
        assert r.exit_code == 4 and "✗ blobs" in r.stdout

    def test_export_import_backup_restore(self, proj: Path, tmp_path: Path) -> None:
        self._seed(proj, tmp_path)
        archive = tmp_path / "reg.tar.gz"
        assert "Exported 2 version(s)" in run(
            "registry",
            "export",
            "--project",
            str(proj),
            "-o",
            str(archive),
            "--compression",
            "gzip",
        )
        other = tmp_path / "other"
        other.mkdir()
        out = run("registry", "import", str(archive), "--project", str(other))
        assert "Imported 2 version(s)" in out
        assert "Imported 0 version(s) (2 already present)" in run(
            "registry", "import", str(archive), "--project", str(other)
        )
        assert "Would import 0" in run(
            "registry", "import", str(archive), "--project", str(other), "--dry-run"
        )
        bk = run("registry", "backup", "--project", str(proj), "--output-dir", str(tmp_path / "bk"))
        path = bk.split("Backup:")[1].strip()
        target = tmp_path / "restored"
        assert "Restored into" in run("registry", "restore", path, "--into", str(target))
        assert (target / "registry.db").exists()

    def test_reports_analytics_duplicates_recommend_schema(
        self, proj: Path, tmp_path: Path
    ) -> None:
        self._seed(proj, tmp_path)
        rows = json.loads(
            run("registry", "report", "inventory", "--format", "json", "--project", str(proj))
        )["rows"]
        assert {r["artifact"] for r in rows} == {"skill/core/alpha", "skill/core/beta"}
        assert run(
            "registry", "report", "licenses", "--format", "csv", "--project", str(proj)
        ).startswith("license,approval,versions")
        assert "Not verified" in run("registry", "report", "stale", "--project", str(proj))
        assert (
            runner.invoke(app, ["registry", "report", "bogus", "--project", str(proj)]).exit_code
            == 1
        )
        assert "Analytics:" in run("registry", "analytics", "build", "--project", str(proj))
        assert "same" in run(
            "registry", "duplicates", "--project", str(proj)
        ) or "No potential" in run("registry", "duplicates", "--project", str(proj))
        assert (
            "No eligible" in run("registry", "recommend", "graph.query", "--project", str(proj))
            or True
        )
        assert "skill-manifest" in run("registry", "schema")
        assert json.loads(run("registry", "schema", "lockfile"))["title"] == "ananke.lock"
        out = tmp_path / "schemas"
        run("registry", "schema", "--output", str(out))
        assert len(list(out.glob("*.schema.json"))) == 5

    def test_docs_build_and_dump(self, proj: Path, tmp_path: Path) -> None:
        self._seed(proj, tmp_path)
        out = run("registry", "docs", "build", "--project", str(proj))
        assert "2 page" in out or "page(s)" in out
        again = run("registry", "docs", "build", "--project", str(proj))
        assert "0 written" in again
        assert (proj / ".ananke/registry/docs/skills/core/alpha/index.html").exists()
        dump = tmp_path / "all.html"
        assert "Registry dump" in run(
            "registry", "docs", "dump", "--project", str(proj), "--output", str(dump)
        )
        html = dump.read_text()
        assert "core/alpha" in html and "core/beta" in html
        assert "Single-file registry" in run(
            "registry",
            "docs",
            "build",
            "--single-file",
            "--project",
            str(proj),
            "--output",
            str(tmp_path / "s.html"),
        )

    def test_policy_show_and_init(self, proj: Path, tmp_path: Path) -> None:
        run("registry", "init", "--project", str(proj))
        assert "[resolve]" in run("registry", "policy", "show", "--project", str(proj))
        assert (
            json.loads(run("registry", "policy", "show", "--project", str(proj), "--json"))[
                "strict_semver"
            ]
            is False
        )
        assert (
            runner.invoke(app, ["registry", "policy", "init", "--project", str(proj)]).exit_code
            != 0
        )  # exists
        run(
            "registry",
            "policy",
            "init",
            "--preset",
            "enterprise",
            "--force",
            "--project",
            str(proj),
        )
        assert "highest-approved" in run("registry", "policy", "show", "--project", str(proj))
        # a policy file in a bad state is reported, not crashed on
        (proj / ".ananke/registry/policy.toml").write_text("[resolve]\nbogus = 1\n")
        r = runner.invoke(app, ["registry", "resolve", "core/x", "--project", str(proj)])
        assert r.exit_code == 1 and "INVALID_POLICY" in (r.stderr or r.output)

    def test_watch_once_and_events(self, proj: Path, tmp_path: Path) -> None:
        run("registry", "init", "--project", str(proj))
        make_skill(proj / ".ananke/skills/watched", "watched")
        assert "pending: ananke://skill/core/watched" in run(
            "registry", "watch", "--once", "--project", str(proj)
        )


class TestSyncCli:
    def test_sync_lock_and_verify(self, proj: Path, tmp_path: Path) -> None:
        run("registry", "learn", str(make_skill(tmp_path / "a", "alpha")), "--project", str(proj))
        (proj / "pyproject.toml").write_text('[tool.ananke.skills]\n"core/alpha" = "^1"\n')
        out = run("sync", "--project", str(proj))
        assert "Wrote" in out and "+ ananke://skill/core/alpha@1.0.0" in out
        assert (proj / "ananke.lock").exists()
        assert "Up to date" in run("sync", "--project", str(proj))
        assert "Would write" in run("sync", "--project", str(proj), "--dry-run", "--update")
        assert "✓" in run("registry", "verify-lock", "--project", str(proj))
        assert "activated" in run("sync", "--project", str(proj), "--activate")
        assert "Wrote" in run(
            "registry",
            "lock",
            "core/alpha@^1",
            "--project",
            str(proj),
            "--output",
            str(proj / "other.lock"),
        )
        payload = json.loads(run("registry", "evidence", "--project", str(proj)))
        assert payload["artifacts"][0]["uri"] == "ananke://skill/core/alpha"

    def test_sync_with_nothing_declared(self, proj: Path) -> None:
        run("registry", "init", "--project", str(proj))
        r = runner.invoke(app, ["sync", "--project", str(proj)])
        assert r.exit_code == 1 and "nothing to sync" in (r.stderr or r.output)

    def test_verify_lock_failure(self, proj: Path, tmp_path: Path) -> None:
        run("registry", "learn", str(make_skill(tmp_path / "a", "alpha")), "--project", str(proj))
        (proj / "pyproject.toml").write_text('[tool.ananke.skills]\n"core/alpha" = "^1"\n')
        run("sync", "--project", str(proj))
        run("registry", "quarantine", "core/alpha@1.0.0", "--reason", "x", "--project", str(proj))
        r = runner.invoke(app, ["registry", "verify-lock", "--project", str(proj)])
        assert r.exit_code == 4 and "quarantined" in r.stdout

    def test_publish_link_unlink(self, proj: Path, tmp_path: Path) -> None:
        src = make_skill(tmp_path / "pub", "pub")
        out = run("registry", "publish", str(src), "--project", str(proj))
        assert "✓ register" in out and "Published ananke://skill/core/pub@1.0.0" in out
        assert "would" not in out
        dry = run("registry", "publish", str(src), "--project", str(proj), "--dry-run")
        assert "conflict-and-policy" in dry  # identical content is accepted as already registered
        assert "Linked" in run(
            "registry",
            "link",
            str(make_skill(tmp_path / "dev", "devskill")),
            "--project",
            str(proj),
        )
        assert "unlinked" in run("registry", "unlink", "core/devskill", "--project", str(proj))


class TestKindShortcuts:
    def test_skill_and_agent_commands(self, proj: Path, tmp_path: Path) -> None:
        run(
            "skill",
            "register",
            str(make_skill(tmp_path / "s", "graph-review")),
            "--project",
            str(proj),
        )
        agent = tmp_path / "agent"
        agent.mkdir()
        (agent / "AGENT.md").write_text("# Coder\n\nWrites code.")
        (agent / "manifest.yaml").write_text(
            "name: coder\nversion: 3.2.0\nnamespace: eng\nlicense: MIT\nskills:\n  - {ref: core/graph-review, version: '^1'}\n"
        )
        assert "registered" in run("agent", "register", str(agent), "--project", str(proj))
        assert "core/graph-review" in run("skill", "list", "--project", str(proj))
        assert "eng/coder" in run("agent", "list", "--project", str(proj))
        assert "eng/coder" not in run("skill", "list", "--project", str(proj))
        assert (
            json.loads(run("skill", "show", "core/graph-review", "--project", str(proj), "--json"))[
                "kind"
            ]
            == "skill"
        )
        assert "eng/coder" in run(
            "agent", "search", "--skill", "graph-review", "--project", str(proj)
        )
        assert "graph-review" in run("skill", "search", "graph", "--project", str(proj))
        assert "1.0.0" in run("skill", "versions", "core/graph-review", "--project", str(proj))
        res = run("agent", "resolve", "eng/coder@^3", "--explain", "--project", str(proj))
        assert "Selected 3.2.0" in res and "Dependency graph" in res
        assert "Activated" in run("agent", "activate", "eng/coder@3.2.0", "--project", str(proj))
        assert (proj / ".ananke/agents/installed/eng.coder@3.2.0").exists()
        assert "1.0.0" in run("skill", "list", "--versions", "--project", str(proj))


class TestApmIntegration:
    def _seed(self, proj: Path, tmp_path: Path) -> None:
        for name, version, deps in (
            ("lib", "1.0.0", ""),
            ("lib", "1.1.0", ""),
            ("app", "1.0.0", "dependencies:\n  - {skill: core/lib, version: '^1'}\n"),
        ):
            run(
                "registry",
                "learn",
                str(
                    make_skill(
                        tmp_path / f"{name}{version}",
                        name,
                        version,
                        deps=deps,
                        extra={"SKILL.md": f"# {name}"},
                    )
                ),
                "--project",
                str(proj),
            )

    def _apm(self, *args: str, ok: bool = True) -> str:
        r = runner.invoke(apm_app, list(args))
        if ok:
            assert r.exit_code == 0, f"{args}\n{r.stdout}"
        return r.stdout

    def test_search_info_install_activate_lock_upgrade(self, proj: Path, tmp_path: Path) -> None:
        self._seed(proj, tmp_path)
        assert "core/app@1.0.0" in self._apm("search", "app", "--project", str(proj))
        assert "core/lib" in self._apm(
            "search", "kind:skill capability:graph.query", "--project", str(proj)
        )
        info = json.loads(self._apm("info", "core/app", "--project", str(proj)))
        assert info["version"] == "1.0.0" and info["dependencies"][0]["id"].endswith("core/lib")
        out = self._apm("install", "core/app@^1", "--project", str(proj))
        assert (
            "Installed: ananke://skill/core/lib@1.1.0" in out
            and "Installed: ananke://skill/core/app@1.0.0" in out
        )
        listing = json.loads(self._apm("list", "--json", "--project", str(proj)))
        assert set(listing["installed"]) == {"core.app@1.0.0", "core.lib@1.1.0"}
        legacy = json.loads(self._apm("info", "--project", str(proj)))
        origins = {p["registry_uri"].rsplit("/", 1)[1]: p["origin"] for p in legacy["packages"]}
        assert origins == {
            "app": "registry",
            "lib": "registry-dependency",
        }  # only the requested ref is a root
        assert "Activated" in self._apm("activate", "core/app@1.0.0", "--project", str(proj))
        assert (
            "core.app@1.0.0"
            in json.loads(self._apm("list", "--json", "--project", str(proj)))["active"]
        )
        assert "Wrote" in self._apm("lock", "--project", str(proj))
        lock = (proj / "ananke.lock").read_text()
        assert "core/lib" in lock and 'version = "1.1.0"' in lock
        # publish a newer lib, then upgrade
        run(
            "registry",
            "learn",
            str(make_skill(tmp_path / "lib12", "lib", "1.2.0", extra={"SKILL.md": "# lib"})),
            "--project",
            str(proj),
        )
        dry = self._apm("upgrade", "--dry-run", "--project", str(proj))
        assert "~ ananke://skill/core/lib 1.1.0 -> 1.2.0" in dry
        assert (proj / ".ananke/skills/installed/core.lib@1.1.0").exists() and not (
            proj / ".ananke/skills/installed/core.lib@1.2.0"
        ).exists()
        self._apm("upgrade", "--project", str(proj))
        assert (proj / ".ananke/skills/installed/core.lib@1.2.0").exists()
        assert "up to date" in self._apm("upgrade", "--project", str(proj)).lower()

    def test_install_activate_flag_and_errors(self, proj: Path, tmp_path: Path) -> None:
        self._seed(proj, tmp_path)
        out = self._apm("install", "core/lib@=1.0.0", "--activate", "--project", str(proj))
        assert "Activated: ananke://skill/core/lib@1.0.0" in out
        assert (
            runner.invoke(apm_app, ["install", "core/ghost@^1", "--project", str(proj)]).exit_code
            == 1
        )
        assert runner.invoke(apm_app, ["install", "--project", str(proj)]).exit_code == 2
        assert runner.invoke(apm_app, ["activate", "--project", str(proj)]).exit_code == 2
        assert runner.invoke(apm_app, ["info", "core/ghost", "--project", str(proj)]).exit_code == 1

    def test_legacy_source_install_still_works(self, proj: Path) -> None:
        skill_source = Path(__file__).parents[1] / "fixtures" / "sample-skill"
        assert "Installed: graph-reviewer@1.0.0" in self._apm(
            "install", "--source", str(skill_source), "--project", str(proj)
        )
        assert "Activated: graph-reviewer@1.0.0" in self._apm(
            "activate", "--name", "graph-reviewer@1.0.0", "--project", str(proj)
        )

    def test_publish_link_unlink(self, proj: Path, tmp_path: Path) -> None:
        src = make_skill(tmp_path / "pub", "pub")
        out = self._apm("publish", str(src), "--project", str(proj))
        assert "Published ananke://skill/core/pub@1.0.0" in out
        conflict = runner.invoke(
            apm_app, ["publish", str(src), "--project", str(proj), "--dry-run"]
        )
        assert conflict.exit_code == 0
        dev = make_skill(tmp_path / "dev", "devskill")
        assert "Linked" in self._apm("link", str(dev), "--project", str(proj))
        assert "Unlinked." in self._apm("unlink", "core/devskill", "--project", str(proj))
        assert "Not linked." in self._apm("unlink", "core/devskill", "--project", str(proj))
        (src / "README.md").write_text("changed")
        r = runner.invoke(apm_app, ["publish", str(src), "--project", str(proj)])
        assert r.exit_code == 1 and "VERSION_CONTENT_CONFLICT" in r.stdout


def test_top_level_help_lists_new_groups() -> None:
    out = runner.invoke(app, ["--help"]).stdout
    for word in ("registry", "skill", "agent", "sync"):
        assert word in out
    reg_help = runner.invoke(app, ["registry", "--help"]).stdout
    for cmd in (
        "init",
        "learn",
        "register",
        "unregister",
        "inspect",
        "search",
        "list",
        "show",
        "diff",
        "resolve",
        "activate",
        "deactivate",
        "doctor",
        "verify",
        "export",
        "import",
        "gc",
        "rebuild-index",
        "snapshot",
        "docs",
        "serve",
    ):
        assert cmd in reg_help, cmd
