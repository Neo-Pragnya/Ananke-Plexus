"""Troves: installed capability libraries that a project switches on in [troves.<name>]."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ananke.plexus.api import Ananke
from ananke.plexus.config.loader import load_config
from ananke.plexus.config.migration import migrate_config_file
from ananke.plexus.config.models import TroveConfig
from ananke.plexus.registry import troves
from ananke.plexus.registry.registry import Registry


def _skill(root: Path, ns: str, name: str) -> dict:  # type: ignore[type-arg]
    d = root / "skills" / f"{ns}.{name}"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: Does {name}.\nlicense: MIT\n---\n\n# {name}\n"
    )
    (d / "ananke.yaml").write_text(
        f"kind: skill\nnamespace: {ns}\nname: {name}\nversion: 1.0.0\nsummary: Does {name}.\n"
        "runtime:\n  supported: [generic]\nlicense:\n  expression: MIT\n  approval: approved\n"
    )
    return {"kind": "skill", "namespace": ns, "name": name, "dir": f"skills/{ns}.{name}"}


@pytest.fixture
def fake_trove(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "trove"
    items = [
        _skill(root, "acme", "alpha"),
        _skill(root, "acme", "beta"),
        _skill(root, "other", "gamma"),
    ]
    items[1]["secret_flags"] = ["placeholder api key"]
    (root / "catalog.json").write_text(json.dumps({"items": items}))
    monkeypatch.setattr(
        troves, "discover", lambda: [troves.TroveInfo("fake-trove", "fake-trove", "1.0", root, 3)]
    )
    return root


def test_select_filters_by_kind_namespace_and_flags(fake_trove: Path) -> None:
    picked = troves.select(fake_trove, TroveConfig(enabled=True, namespaces=["acme"]))
    assert picked is not None and [i["name"] for i in picked] == ["alpha"]  # beta is secret-flagged
    flagged = troves.select(
        fake_trove, TroveConfig(enabled=True, namespaces=["acme"], include_flagged=True)
    )
    assert flagged is not None and {i["name"] for i in flagged} == {"alpha", "beta"}
    assert (
        troves.select(fake_trove, TroveConfig(enabled=True, include_flagged=True)) is None
    )  # everything


def test_sync_learns_only_enabled_troves(fake_trove: Path, tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    reg = Registry.for_project(project, create=True)
    reg.init()
    with reg:
        assert troves.sync(reg, {"fake-trove": TroveConfig(enabled=False)}) == []
        [res] = troves.sync(reg, {"fake-trove": TroveConfig(enabled=True, namespaces=["acme"])})
    assert res.ok and res.selected == 1 and res.registered == 1, res.denied_examples
    with Registry.for_project(project) as reg2:
        [missing] = troves.sync(reg2, {"not-installed": TroveConfig(enabled=True)})
    assert "not installed" in missing.error


def test_config_round_trip_and_migration(tmp_path: Path) -> None:
    Ananke.open(tmp_path).init_project()
    config = tmp_path / ".ananke" / "config.toml"
    troves.write_config(config, "dex-trove", TroveConfig(enabled=True, kinds=["skill"]))
    troves.write_config(
        config, "dex-trove", TroveConfig(enabled=False, kinds=["agent"])
    )  # replaces, not appends
    text = config.read_text()
    assert (
        text.count("[troves.dex-trove]") == 1
        and 'kinds = ["agent"]' in text
        and "[security]" in text
    )
    assert load_config(tmp_path).troves["dex-trove"].kinds == ["agent"]
    assert migrate_config_file(tmp_path)["unknown_count"] == 0
    config.write_text(text + "\n[troves.bad]\nenabeld = true\n")
    report = migrate_config_file(tmp_path)
    assert report["unknown_count"] == 1 and "troves.bad.enabeld" in report["unknown_keys"]
