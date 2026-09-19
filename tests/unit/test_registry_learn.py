"""The learn workflow: identity, diffs, conflicts, fingerprints, pre-registration reports (spec §41-§43, §108, §118)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from registry_support import add, open_registry

from ananke.plexus.registry.learn import inspect_source, learn
from ananke.plexus.registry.models import ArtifactKind
from ananke.plexus.registry.policy import RegistryPolicy, enterprise_policy
from ananke.plexus.registry.registry import Registry
from ananke.plexus.registry.search import search
from ananke.plexus.registry.views import KindView

MANIFEST = """
kind = "skill"
namespace = "core"
name = "graph-review"
version = "{version}"
summary = "Reviews changes."
capabilities = ["graph.query"]
[license]
expression = "Apache-2.0"
[permissions.filesystem]
read = ["src/**"]
"""


def skill_dir(root: Path, version: str = "1.0.0", extra: dict[str, str] | None = None) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "ananke.toml").write_text(MANIFEST.format(version=version))
    (root / "README.md").write_text("# graph review")
    for rel, text in (extra or {}).items():
        (root / rel).write_text(text)
    return root


def test_learn_registers_and_is_idempotent(tmp_path: Path) -> None:
    reg = open_registry(tmp_path)
    src = skill_dir(tmp_path / "src")
    rep = learn(reg, str(src))
    (cand,) = rep.candidates
    assert cand.action == "registered" and rep.ok
    assert cand.result and cand.result.version_uri == "ananke://skill/core/graph-review@1.0.0"
    again = learn(reg, str(src), force=True)
    assert again.candidates[0].action == "unchanged"
    kinds = [e["event_type"] for e in reg.store.list_events()]
    assert (
        kinds.count("artifact.registered") == 1
        and "artifact.imported" in kinds
        and "artifact.discovered" in kinds
    )


def test_fingerprint_skips_unchanged_sources(tmp_path: Path) -> None:
    reg = open_registry(tmp_path)
    src = skill_dir(tmp_path / "src")
    learn(reg, str(src))
    second = learn(reg, str(src))
    assert (
        second.candidates[0].action == "skipped" and "fingerprint" in second.candidates[0].message
    )
    (src / "README.md").write_text("# changed")
    third = learn(reg, str(src))
    assert third.candidates[0].action == "conflict"  # same version, new content


def test_changed_content_reports_diff_and_suggested_version(tmp_path: Path) -> None:
    reg = open_registry(tmp_path)
    src = skill_dir(tmp_path / "src")
    learn(reg, str(src))
    (src / "ananke.toml").write_text(
        MANIFEST.format(version="1.0.0").replace(
            '["graph.query"]', '["graph.query", "graph.write"]'
        )
    )
    (cand,) = learn(reg, str(src)).candidates
    assert cand.action == "conflict" and "VERSION_CONTENT_CONFLICT" in cand.message
    assert cand.diff is not None and cand.diff.capabilities_added == ["graph.write"]
    assert cand.suggested_version == "1.1.0"
    auto = learn(reg, str(src), version="auto").candidates[0]
    assert auto.action == "registered" and auto.version == "1.1.0"


def test_new_version_registered_side_by_side(tmp_path: Path) -> None:
    reg = open_registry(tmp_path)
    src = skill_dir(tmp_path / "src")
    learn(reg, str(src))
    (src / "ananke.toml").write_text(MANIFEST.format(version="1.1.0"))
    assert learn(reg, str(src)).candidates[0].action == "registered"
    assert [r.version for r in reg.versions("core/graph-review")] == ["1.0.0", "1.1.0"]


def test_collection_of_skills(tmp_path: Path) -> None:
    reg = open_registry(tmp_path)
    for n in ("a", "b"):
        d = tmp_path / "skills" / n
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(
            f"---\nname: {n}\ndescription: skill {n}\nlicense: MIT\n---\n# {n}"
        )
    rep = learn(reg, str(tmp_path / "skills"))
    assert {c.uri for c in rep.candidates} == {"ananke://skill/local/a", "ananke://skill/local/b"}
    assert all(c.action == "registered" for c in rep.candidates)
    assert len(search(reg, "skill")) == 2


def test_dry_run_and_inspect_write_nothing(tmp_path: Path) -> None:
    reg = open_registry(tmp_path)
    src = skill_dir(tmp_path / "src")
    assert learn(reg, str(src), dry_run=True).candidates[0].action == "dry-run"
    assert inspect_source(reg, str(src)).candidates[0].action == "dry-run"
    assert reg.store.count_versions() == 0


def test_missing_metadata_reported_not_guessed(tmp_path: Path) -> None:
    reg = open_registry(tmp_path, enterprise_policy())  # no default namespace
    d = tmp_path / "s"
    d.mkdir()
    (d / "SKILL.md").write_text("# x")
    (cand,) = learn(reg, str(d)).candidates
    assert cand.action == "incomplete" and "namespace" in cand.missing
    ok = learn(reg, str(d), namespace="team", license="MIT")
    assert ok.candidates[0].action in {
        "registered",
        "denied",
    }  # enterprise policy may still deny; never guessed


def test_overrides_supply_missing_fields(tmp_path: Path) -> None:
    reg = open_registry(tmp_path, RegistryPolicy(default_namespace=None))
    d = tmp_path / "s"
    d.mkdir()
    (d / "SKILL.md").write_text("# x")
    rep = learn(reg, str(d), namespace="team", name="my-skill", version="3.0.0", license="MIT")
    (cand,) = rep.candidates
    assert (
        cand.action == "registered"
        and cand.uri == "ananke://skill/team/my-skill"
        and cand.version == "3.0.0"
    )


def test_policy_denial_is_reported(tmp_path: Path) -> None:
    reg = open_registry(tmp_path, enterprise_policy())
    d = skill_dir(tmp_path / "src")
    (d / "ananke.toml").write_text(
        MANIFEST.format(version="1.0.0").replace("Apache-2.0", "GPL-3.0-only")
    )
    (cand,) = learn(reg, str(d)).candidates
    assert cand.action == "denied" and "LICENSE_DENIED" in cand.message


def test_secret_in_source_denied(tmp_path: Path) -> None:
    reg = open_registry(tmp_path)
    d = skill_dir(tmp_path / "src", extra={"config.env": "AWS=AKIAABCDEFGHIJKLMNOP"})
    (cand,) = learn(reg, str(d)).candidates
    assert cand.action == "denied" and "SECRET_DETECTED" in cand.message
    assert reg.store.count_versions() == 0


def test_identity_never_merged_silently_by_name(tmp_path: Path) -> None:
    reg = open_registry(tmp_path)
    add(reg, "graph-review", "1.0.0", namespace="team")
    src = skill_dir(tmp_path / "src")
    (cand,) = learn(reg, str(src)).candidates
    assert cand.action == "registered"  # different namespace -> separate artifact
    assert any("low confidence" in n for n in cand.identity_notes)
    assert {a.namespace for a in reg.list_artifacts("skill")} == {"team", "core"}


def test_same_native_id_under_new_name_needs_review(tmp_path: Path) -> None:
    reg = open_registry(tmp_path)

    def write(d: Path, name: str) -> None:
        d.mkdir(parents=True, exist_ok=True)
        (d / "ananke.yaml").write_text(
            f"kind: skill\nnamespace: core\nname: {name}\nversion: 1.0.0\n"
            "ananke_id: urn:same\nlicense: {expression: MIT}\n"
        )

    write(tmp_path / "one", "first")
    learn(reg, str(tmp_path / "one"))
    write(tmp_path / "two", "second")
    (cand,) = learn(reg, str(tmp_path / "two")).candidates
    assert cand.action == "review" and any("ananke_id" in n for n in cand.identity_notes)
    assert reg.store.count_versions() == 1


def test_permission_concerns_surface(tmp_path: Path) -> None:
    reg = open_registry(tmp_path)
    d = tmp_path / "s"
    d.mkdir()
    (d / "ananke.yaml").write_text(
        "kind: skill\nnamespace: a\nname: risky\nversion: 1.0.0\npermissions:\n  network: [api.example.com]\n"
        "  filesystem: {read: ['**'], write: ['**']}\n  shell: {allow: [git]}\n"
    )
    (cand,) = inspect_source(reg, str(d)).candidates
    text = " | ".join(cand.permission_concerns)
    assert (
        "network access" in text
        and "unrestricted filesystem read" in text
        and "shell execution" in text
    )


def test_mcp_snapshot_learned_and_searchable(tmp_path: Path) -> None:
    reg = open_registry(tmp_path)
    f = tmp_path / "s.json"
    f.write_text(
        json.dumps(
            {
                "serverInfo": {"name": "GitHub", "version": "1.0.0"},
                "tools": [{"name": "create_issue", "description": "Create an issue"}],
            }
        )
    )
    (cand,) = learn(reg, f"mcp:{f}", license="MIT").candidates
    assert cand.action == "registered"
    assert [h.name for h in search(reg, "issue")] == ["github"]


def test_denied_and_dynamic_gates(tmp_path: Path) -> None:
    reg = open_registry(tmp_path)
    (cand,) = learn(reg, "mcp-stdio:python -c pass").candidates
    assert cand.action == "denied" and "DYNAMIC_INTROSPECTION_DISABLED" in cand.message


def test_unrecognised_source_raises(tmp_path: Path) -> None:
    from ananke.plexus.registry.errors import ImporterError

    reg = open_registry(tmp_path)
    with pytest.raises(ImporterError):
        learn(reg, str(tmp_path / "nope"))


def test_kind_views_register_and_get(tmp_path: Path) -> None:
    reg = open_registry(tmp_path)
    src = skill_dir(tmp_path / "src")
    (res,) = reg.skills.register(src)
    assert res.created
    assert isinstance(reg.skills, KindView) and reg.agents.kind is ArtifactKind.AGENT
    rec = reg.skills.get("core/graph-review", "^1")
    assert rec.version == "1.0.0"
    assert [r.name for r in reg.skills.list()] == ["graph-review"] and reg.agents.list() == []
    assert reg.skills.versions("core/graph-review")[0].version == "1.0.0"
    assert reg.skills.search("review")[0].name == "graph-review"


def test_reopen_project_registry(tmp_path: Path) -> None:
    reg = Registry.for_project(tmp_path, create=True, actor="t")
    reg.init()
    learn(reg, str(skill_dir(tmp_path / "src")))
    reg.close()
    again = Registry.for_project(tmp_path)
    assert (
        again.store.count_versions() == 1 and (tmp_path / ".ananke/registry/events.jsonl").exists()
    )
    lines = [
        json.loads(x) for x in (tmp_path / ".ananke/registry/events.jsonl").read_text().splitlines()
    ]
    assert "registry.version.registered" in {e["event_type"] for e in lines}
