"""Tests for BMAD compiler — behavior, model, architecture (D5-D7)."""

from pathlib import Path

from ananke.plexus.contracts.architecture import compile_architecture
from ananke.plexus.contracts.behavior import compile_behavior, compile_gherkin
from ananke.plexus.contracts.bmad import compile_bmad
from ananke.plexus.contracts.model import compile_model
from ananke.plexus.specs.models import Requirement


def _req(tmp_path: Path) -> Requirement:
    return Requirement(
        requirement_id="DEMO-101",
        title="Idempotent webhook processing",
        body="Process webhooks exactly once.",
        acceptance_criteria=[
            "Duplicate events are deduplicated",
            "Idempotency key is stored",
        ],
    )


def test_compile_behavior_creates_test_skeleton(tmp_path):
    req = _req(tmp_path)
    path = compile_behavior(tmp_path, req)
    assert path.exists()
    content = path.read_text(encoding="utf-8")
    assert "import pytest" in content
    assert "AC-1" in content
    assert "AC-2" in content
    assert "pytest.fail" in content


def test_compile_behavior_traceability(tmp_path):
    req = _req(tmp_path)
    path = compile_behavior(tmp_path, req)
    content = path.read_text(encoding="utf-8")
    assert "DEMO-101" in content
    assert "Traceability" in content


def test_compile_gherkin_creates_feature(tmp_path):
    req = _req(tmp_path)
    path = compile_gherkin(tmp_path, req)
    assert path.exists()
    content = path.read_text(encoding="utf-8")
    assert "Feature:" in content
    assert "Scenario:" in content
    assert "AC-1" in content


def test_compile_model_creates_yaml(tmp_path):
    req = _req(tmp_path)
    path = compile_model(tmp_path, req)
    assert path.exists()
    content = path.read_text(encoding="utf-8")
    assert "demo-101_request" in content
    assert "backward_compatible" in content


def test_compile_architecture_creates_yaml(tmp_path):
    req = _req(tmp_path)
    path = compile_architecture(tmp_path, req)
    assert path.exists()
    content = path.read_text(encoding="utf-8")
    assert "calm_ref" in content
    assert "deny_cross_domain_imports" in content


def test_compile_bmad_creates_all_artifacts(tmp_path):
    req = _req(tmp_path)
    bmad_path = compile_bmad(tmp_path, req)
    assert bmad_path.exists()
    content = bmad_path.read_text(encoding="utf-8")
    assert "BMAD Contract" in content
    assert "behavior:" in content
    assert "model:" in content
    assert "architecture:" in content
    # Check sub-artifacts also created
    assert (tmp_path / "model-contract.yaml").exists()
    assert (tmp_path / "architecture-contract.yaml").exists()
    assert any(tmp_path.glob("tests/test_*.py"))


def test_compile_bmad_scenario_ids(tmp_path):
    req = _req(tmp_path)
    bmad_path = compile_bmad(tmp_path, req)
    content = bmad_path.read_text(encoding="utf-8")
    assert "AC-1" in content
    assert "AC-2" in content
