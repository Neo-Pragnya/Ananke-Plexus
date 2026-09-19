"""Materialization, activation, sync/lock, overrides, dev links, publish, evidence (spec §85-§87, §139, §150-§153)."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest
from registry_support import add, open_registry

from ananke.plexus.registry.activation import (
    MARKER,
    activate,
    apply_profile,
    deactivate,
    install,
    list_installed,
    materialize,
    read_profile,
    uninstall,
)
from ananke.plexus.registry.errors import PolicyViolationError, QuarantinedError, RegistryError
from ananke.plexus.registry.evidence import project_capability_evidence, write_run_evidence
from ananke.plexus.registry.lockfile import load_lock
from ananke.plexus.registry.models import TrustStatus
from ananke.plexus.registry.policy import OverridePolicy, RegistryPolicy
from ananke.plexus.registry.sync import (
    link,
    publish,
    read_links,
    read_project_requirements,
    sync,
    unlink,
)


def project(tmp_path: Path) -> Path:
    p = tmp_path / "proj"
    p.mkdir(exist_ok=True)
    return p


def write_pyproject(p: Path, body: str) -> None:
    (p / "pyproject.toml").write_text(body)


class TestMaterializeActivate:
    def test_materialize_is_content_addressed_and_idempotent(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        add(reg, files={"a.txt": b"hello", "sub/b.txt": b"x"})
        rec = reg.exact_version("core/graph-review@1.0.0")
        d1 = materialize(reg, rec)
        assert d1 == reg.root / "materialized" / rec.digest_sha256
        assert (d1 / "a.txt").read_bytes() == b"hello" and (d1 / MARKER).exists()
        assert materialize(reg, rec) == d1
        (d1 / "a.txt").write_bytes(b"local edit")
        assert (
            materialize(reg, rec) / "a.txt"
        ).read_bytes() == b"local edit"  # marker trusted; gc/verify handle drift

    def test_quarantined_cannot_materialize_or_activate(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        ref = add(reg)
        reg.quarantine(ref, "bad")
        with pytest.raises(QuarantinedError):
            materialize(reg, reg.exact_version(ref))
        with pytest.raises(QuarantinedError):
            activate(reg, project(tmp_path), ref)

    def test_tampered_blob_refuses_materialization(self, tmp_path: Path) -> None:
        from ananke.plexus.registry.errors import IntegrityError

        reg = open_registry(tmp_path)
        ref = add(reg)
        rec = reg.exact_version(ref)
        blob = reg.cas._path(rec.digest_sha256)
        blob.chmod(0o644)
        blob.write_bytes(b"corrupt")
        with pytest.raises(IntegrityError):
            materialize(reg, rec)

    def test_activate_layout_profile_and_apm_compat(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        add(reg, "graph-review", "2.1.0", files={"SKILL.md": b"# skill"})
        proj = project(tmp_path)
        res = activate(reg, proj, "core/graph-review@2.1.0", requirement="^2")
        installed = proj / ".ananke/skills/installed/core.graph-review@2.1.0"
        assert (
            Path(res.path) == installed
            and (installed / "SKILL.md").exists()
            and not (installed / MARKER).exists()
        )
        assert (proj / ".ananke/skills/active/core.graph-review@2.1.0.active").exists()
        assert read_profile(proj)["skills"] == {"core/graph-review": "2.1.0"}
        prof = tomllib.loads((proj / ".ananke/activation.toml").read_text())
        assert prof["skills"]["core/graph-review"] == "2.1.0"
        from ananke.plexus.apm.lockfile import read_lock
        from ananke.plexus.apm.registry import list_active_skills, list_installed_skills

        assert list_installed_skills(proj) == ["core.graph-review@2.1.0"]
        assert list_active_skills(proj) == ["core.graph-review@2.1.0"]
        (pkg,) = read_lock(proj)["packages"]  # type: ignore[misc]
        assert (
            pkg["origin"] == "registry"
            and pkg["requirement"] == "^2"
            and pkg["registry_uri"].endswith("graph-review")
        )

    def test_only_one_active_version_and_state_tracking(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        add(reg, version="1.0.0")
        add(reg, version="1.1.0")
        proj = project(tmp_path)
        activate(reg, proj, "core/graph-review@1.0.0")
        activate(reg, proj, "core/graph-review@1.1.0")
        assert [p.name for p in (proj / ".ananke/skills/active").glob("*.active")] == [
            "core.graph-review@1.1.0.active"
        ]
        (row,) = list_installed(reg, proj)
        assert row["version"] == "1.1.0" and row["state"] == "activated"
        assert "activation.changed" in [e["event_type"] for e in reg.store.list_events()]

    def test_install_does_not_activate(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        ref = add(reg)
        proj = project(tmp_path)
        res = install(reg, proj, ref)
        assert (
            res.state == "materialized" and list_installed(reg, proj)[0]["state"] == "materialized"
        )
        assert (
            not list((proj / ".ananke/skills/active").glob("*.active"))
            if (proj / ".ananke/skills/active").exists()
            else True
        )
        assert activate(reg, proj, ref).state == "activated"

    def test_deactivate_and_uninstall(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        ref = add(reg)
        proj = project(tmp_path)
        activate(reg, proj, ref)
        assert deactivate(reg, proj, "core/graph-review")
        assert (
            list_installed(reg, proj)[0]["state"] == "materialized"
            and read_profile(proj)["skills"] == {}
        )
        assert uninstall(reg, proj, "core/graph-review")
        assert (
            list_installed(reg, proj) == []
            and not (proj / ".ananke/skills/installed/core.graph-review@1.0.0").exists()
        )
        assert not deactivate(reg, proj, "core/graph-review")

    def test_link_mode_and_agents_layout(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path, RegistryPolicy(activation_mode="link"))
        add(
            reg,
            "coder",
            "1.0.0",
            kind="agent",
            namespace="eng",
            instructions={"ref": "p.md"},
            files={"p.md": b"x"},
        )
        proj = project(tmp_path)
        res = activate(reg, proj, "eng/coder@1.0.0", kind="agent")
        dest = Path(res.path)
        assert (
            res.mode == "link"
            and dest.is_symlink()
            and dest.parent == proj / ".ananke/agents/installed"
        )
        assert read_profile(proj)["agent"] == {"eng/coder": "1.0.0"}

    def test_apply_profile_restores_state(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        add(reg, "a", "1.0.0")
        add(reg, "b", "2.0.0")
        proj = project(tmp_path)
        activate(reg, proj, "core/a@1.0.0")
        activate(reg, proj, "core/b@2.0.0")
        import shutil

        shutil.rmtree(proj / ".ananke/skills")
        results = apply_profile(reg, proj)
        assert {r.version for r in results} == {"1.0.0", "2.0.0"}
        assert (proj / ".ananke/skills/installed/core.a@1.0.0").exists()


class TestSync:
    def _registry(self, tmp_path: Path):  # type: ignore[no-untyped-def]
        reg = open_registry(tmp_path)
        add(reg, "graph-query", "1.8.0", approve=True)
        add(reg, "graph-query", "1.8.2", approve=True)
        add(
            reg,
            "graph-review",
            "2.1.3",
            approve=True,
            dependencies=[{"skill": "core/graph-query", "version": "^1"}],
        )
        add(
            reg,
            "coding-agent",
            "3.2.0",
            kind="agent",
            namespace="engineering",
            approve=True,
            skills=[{"ref": "core/graph-review", "version": "^2.0"}],
        )
        return reg

    def test_pyproject_requirements_resolve_and_write_lock(self, tmp_path: Path) -> None:
        reg = self._registry(tmp_path)
        proj = project(tmp_path)
        write_pyproject(
            proj, '[tool.ananke.agent]\nname = "engineering/coding-agent"\nversion = "^3"\n'
        )
        res = sync(reg, proj)
        assert res.written and res.lock_path.endswith("ananke.lock")
        lock = load_lock(proj / "ananke.lock")
        assert {e.id.rsplit("/", 1)[1]: e.version for e in lock.entries} == {
            "graph-query": "1.8.2",
            "graph-review": "2.1.3",
            "coding-agent": "3.2.0",
        }
        assert lock.requirements[0].req == "^3"
        assert "+ ananke://agent/engineering/coding-agent@3.2.0" in res.changes

    def test_sync_is_idempotent_and_prefers_lock(self, tmp_path: Path) -> None:
        reg = self._registry(tmp_path)
        proj = project(tmp_path)
        write_pyproject(proj, '[tool.ananke.skills]\n"core/graph-query" = "^1"\n')
        sync(reg, proj)
        first = (proj / "ananke.lock").read_text()
        add(reg, "graph-query", "1.9.0", approve=True)
        again = sync(reg, proj)
        assert (
            not again.written
            and again.changes == []
            and (proj / "ananke.lock").read_text() == first
        )
        updated = sync(reg, proj, update=True)
        assert updated.written and updated.changes == [
            "~ ananke://skill/core/graph-query 1.8.2 -> 1.9.0"
        ]

    def test_prefer_lock_reresolves_when_requirement_changes(self, tmp_path: Path) -> None:
        reg = self._registry(tmp_path)
        proj = project(tmp_path)
        write_pyproject(proj, '[tool.ananke.skills]\n"core/graph-query" = "=1.8.0"\n')
        sync(reg, proj)
        assert load_lock(proj / "ananke.lock").entries[0].version == "1.8.0"
        write_pyproject(proj, '[tool.ananke.skills]\n"core/graph-query" = "^1.8.1"\n')
        sync(reg, proj)
        assert load_lock(proj / "ananke.lock").entries[0].version == "1.8.2"

    def test_nothing_to_sync(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        with pytest.raises(RegistryError, match="nothing to sync"):
            sync(reg, project(tmp_path))

    def test_unresolvable_fails_with_explanation(self, tmp_path: Path) -> None:
        from ananke.plexus.registry.errors import ResolutionError

        reg = self._registry(tmp_path)
        proj = project(tmp_path)
        write_pyproject(proj, '[tool.ananke.skills]\n"core/graph-query" = "^9"\n')
        with pytest.raises(ResolutionError, match="no registered version matches"):
            sync(reg, proj)
        assert not (proj / "ananke.lock").exists()

    def test_dry_run_and_activate_all(self, tmp_path: Path) -> None:
        reg = self._registry(tmp_path)
        proj = project(tmp_path)
        write_pyproject(proj, '[tool.ananke.skills]\n"core/graph-review" = "^2"\n')
        dry = sync(reg, proj, dry_run=True)
        assert not dry.written and not (proj / "ananke.lock").exists()
        done = sync(reg, proj, activate_all=True)
        assert len(done.activated) == 2
        assert (proj / ".ananke/skills/installed/core.graph-query@1.8.2").exists()

    def test_apm_installed_requirements_are_roots(self, tmp_path: Path) -> None:
        reg = self._registry(tmp_path)
        proj = project(tmp_path)
        install(reg, proj, "core/graph-review@2.1.3", requirement="^2")
        reqs = read_project_requirements(proj)
        assert [r.req for r in reqs.roots] == ["^2"] and reqs.sources == ["apm.lock"]
        assert sync(reg, proj).written

    def test_runtime_environment_filters(self, tmp_path: Path) -> None:
        from ananke.plexus.registry.resolver import ResolutionEnvironment

        reg = open_registry(tmp_path)
        add(reg, "x", "1.0.0", runtime={"supported": ["microsoft"]})
        proj = project(tmp_path)
        write_pyproject(proj, '[tool.ananke.skills]\n"core/x" = "*"\n')
        with pytest.raises(RegistryError):
            sync(reg, proj, env=ResolutionEnvironment.detect(runtime="pydantic"))
        assert sync(reg, proj, env=ResolutionEnvironment.detect(runtime="microsoft")).written


class TestOverridesAndLinks:
    def _dev_skill(self, tmp_path: Path, version: str = "9.9.9") -> Path:
        d = tmp_path / "dev-skill"
        d.mkdir(exist_ok=True)
        (d / "ananke.yaml").write_text(
            f"kind: skill\nnamespace: core\nname: graph-review\nversion: {version}\nlicense: {{expression: MIT}}\n"
        )
        return d

    def test_path_override_is_dirty_in_lock(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        add(reg, "graph-review", "1.0.0", approve=True)
        proj = project(tmp_path)
        dev = self._dev_skill(tmp_path)
        write_pyproject(
            proj,
            f'[tool.ananke.skills]\n"core/graph-review" = "*"\n[tool.ananke.overrides]\n"core/graph-review" = {{ path = "{dev}" }}\n',
        )
        res = sync(reg, proj)
        (entry,) = load_lock(proj / "ananke.lock").entries
        assert (
            entry.version == "9.9.9"
            and entry.source == "path"
            and entry.dirty
            and entry.path == str(dev.resolve())
        )
        assert any("path overrides" in w for w in res.warnings)
        text = (proj / "ananke.lock").read_text()
        assert 'source = "path"' in text and "dirty = true" in text

    def test_release_profile_forbids_overrides(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        add(reg, "graph-review", "1.0.0", approve=True)
        proj = project(tmp_path)
        dev = self._dev_skill(tmp_path)
        write_pyproject(
            proj,
            f'[tool.ananke.skills]\n"core/graph-review" = "*"\n[tool.ananke.overrides]\n"core/graph-review" = "{dev}"\n',
        )
        with pytest.raises(PolicyViolationError, match="OVERRIDE_IN_RELEASE"):
            sync(reg, proj, release=True)
        reg.policy = RegistryPolicy(overrides=OverridePolicy(allow_in_release=True))
        assert sync(reg, proj, release=True).written

    def test_dev_link_lifecycle(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        proj = project(tmp_path)
        dev = self._dev_skill(tmp_path, "0.1.0")
        res = link(reg, proj, dev)
        assert res.uri == "ananke://skill/core/graph-review" and res.version == "0.1.0"
        assert (proj / ".ananke/skills/linked/core.graph-review").is_symlink()
        assert read_links(proj)[res.uri]["path"] == str(dev.resolve())
        assert reg.store.count_versions() == 0  # linked skills never enter the immutable registry
        write_pyproject(proj, '[tool.ananke.skills]\n"core/graph-review" = "*"\n')
        sync(reg, proj)
        assert load_lock(proj / "ananke.lock").entries[0].dirty
        assert unlink(reg, proj, "core/graph-review") and not unlink(reg, proj, "core/graph-review")
        assert (
            not (proj / ".ananke/skills/linked/core.graph-review").exists()
            and read_links(proj) == {}
        )


class TestPublish:
    def _skill(self, tmp_path: Path, extra: str = "") -> Path:
        d = tmp_path / "pub"
        d.mkdir(exist_ok=True)
        (d / "ananke.yaml").write_text(
            f"kind: skill\nnamespace: core\nname: pub\nversion: 1.0.0\nlicense: {{expression: MIT}}\n{extra}"
        )
        (d / "README.md").write_text("# pub")
        return d

    def test_full_pipeline(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        report = publish(reg, self._skill(tmp_path))
        assert report.ok and [s.name for s in report.steps] == [
            "validate",
            "test",
            "hash",
            "resolve-dependencies",
            "conflict-and-policy",
            "register",
            "docs",
        ]
        assert report.result and report.result.created

    def test_conflict_stops_pipeline(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        d = self._skill(tmp_path)
        publish(reg, d)
        (d / "README.md").write_text("# changed")
        report = publish(reg, d)
        assert not report.ok and report.result is None
        assert (
            report.steps[-1].name == "conflict-and-policy"
            and "VERSION_CONTENT_CONFLICT" in report.steps[-1].detail
        )

    def test_unregistered_dependency_flagged(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        d = self._skill(tmp_path, "dependencies:\n  - {skill: core/ghost, version: '^1'}\n")
        report = publish(reg, d)
        step = next(s for s in report.steps if s.name == "resolve-dependencies")
        assert not step.ok and "core/ghost" in step.detail and not report.ok

    def test_dry_run_and_bad_source(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        report = publish(reg, self._skill(tmp_path), dry_run=True)
        assert report.result is None and reg.store.count_versions() == 0
        empty = tmp_path / "empty"
        empty.mkdir()
        bad = publish(reg, empty)
        assert not bad.ok and bad.steps[0].name == "validate"

    def test_auto_version_and_channel(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        d = self._skill(tmp_path)
        publish(reg, d)
        (d / "README.md").write_text("docs")
        rep = publish(reg, d, version="auto", channel="beta")
        assert rep.result and rep.result.version == "1.0.1"
        assert reg.exact_version("core/pub@1.0.1").channel == "beta"


class TestEvidence:
    def test_lock_becomes_run_evidence(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        add(reg, "coder", "1.0.0", kind="agent", namespace="eng", approve=True)
        proj = project(tmp_path)
        write_pyproject(proj, '[tool.ananke.agent]\nname = "eng/coder"\nversion = "^1"\n')
        sync(reg, proj)
        payload = project_capability_evidence(proj)
        assert payload and payload["agents"] == ["ananke://agent/eng/coder@1.0.0"]
        assert payload["registry_snapshot"].startswith("sha256:") and payload["artifacts"][0][
            "digest"
        ].startswith("sha256:")
        run = proj / ".ananke/evidence/run-1"
        out = write_run_evidence(proj, run)
        assert out and json.loads(out.read_text())["lock_hash"] == payload["lock_hash"]

    def test_no_lock_no_evidence(self, tmp_path: Path) -> None:
        assert project_capability_evidence(project(tmp_path)) is None
        assert write_run_evidence(project(tmp_path), tmp_path / "e") is None

    def test_evidence_bundle_records_capabilities(self, tmp_path: Path) -> None:
        from ananke.plexus.evidence.bundle import create_evidence_bundle

        reg = open_registry(tmp_path)
        add(reg, "s", "1.0.0", approve=True)
        proj = project(tmp_path)
        write_pyproject(proj, '[tool.ananke.skills]\n"core/s" = "^1"\n')
        sync(reg, proj)
        bundle = create_evidence_bundle(proj, {"lint": "PASS"})
        manifest = json.loads((bundle / "manifest.json").read_text())
        assert "registry-capabilities.json" in manifest["files"]
        assert "registry-capabilities.json" in (bundle / "checksums.sha256").read_text()
        assert TrustStatus.APPROVED  # keep import used
