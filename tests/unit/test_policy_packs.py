"""Tests for policy packs (B5)."""

import pytest

from ananke.plexus.policy.packs import (
    install_pack,
    list_builtin_packs,
    load_builtin_pack,
    load_project_packs,
)


def test_list_builtin_packs():
    packs = list_builtin_packs()
    assert "baseline" in packs
    assert "python-library" in packs
    assert "agentic-security" in packs
    assert "enterprise-strict" in packs


def test_load_baseline_pack():
    rules = load_builtin_pack("baseline")
    assert len(rules) > 0
    rule_ids = [r.rule_id for r in rules]
    assert "policy.core.fail-closed" in rule_ids


def test_load_python_library_pack():
    rules = load_builtin_pack("python-library")
    ids = [r.rule_id for r in rules]
    assert "quality.ruff-check" in ids
    assert "quality.mypy-strict" in ids
    assert "tests.minimum-coverage" in ids


def test_load_agentic_security_pack():
    rules = load_builtin_pack("agentic-security")
    ids = [r.rule_id for r in rules]
    assert any("sast" in i or "semgrep" in i or "agent" in i for i in ids)


def test_load_enterprise_strict_pack():
    rules = load_builtin_pack("enterprise-strict")
    assert len(rules) >= 6


def test_load_unknown_pack_raises():
    with pytest.raises(ValueError, match="Unknown builtin pack"):
        load_builtin_pack("does-not-exist")


def test_install_pack(tmp_path):
    dest = install_pack(tmp_path, "baseline")
    assert dest.exists()
    content = dest.read_text(encoding="utf-8")
    assert "[[rule]]" in content
    assert "policy.core.fail-closed" in content


def test_install_pack_unknown_raises(tmp_path):
    with pytest.raises(ValueError):
        install_pack(tmp_path, "non-existent-pack")


def test_load_project_packs_empty(tmp_path):
    rules = load_project_packs(tmp_path)
    assert rules == []


def test_load_project_packs_from_disk(tmp_path):
    install_pack(tmp_path, "baseline")
    # load_project_packs takes repository_root, not the policy dir
    rules = load_project_packs(tmp_path)
    assert len(rules) > 0
    rule_ids = [r.rule_id for r in rules]
    assert "policy.core.fail-closed" in rule_ids
