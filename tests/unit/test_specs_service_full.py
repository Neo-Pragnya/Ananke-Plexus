"""Tests for specs service and load_requirement_from_dir (D4)."""

from ananke.plexus.specs.service import load_requirement_from_dir


def test_load_requirement_no_file(tmp_path):
    result = load_requirement_from_dir(tmp_path)
    assert result is None


def test_load_requirement_basic(tmp_path):
    req_file = tmp_path / "requirement.md"
    req_file.write_text(
        "# Add idempotent webhook\n\nBody text.\n\n## Acceptance Criteria\n- Events are deduplicated\n- Key is stored\n",
        encoding="utf-8",
    )
    req = load_requirement_from_dir(tmp_path)
    assert req is not None
    assert req.title == "Add idempotent webhook"
    assert "Events are deduplicated" in req.acceptance_criteria


def test_load_requirement_with_id(tmp_path):
    req_file = tmp_path / "requirement.md"
    req_file.write_text(
        "# My Feature\n\n**ID:** PROJ-42\n\nSome body.\n",
        encoding="utf-8",
    )
    req = load_requirement_from_dir(tmp_path)
    assert req is not None
    assert req.requirement_id == "PROJ-42"


def test_load_requirement_uses_dir_name_as_fallback_id(tmp_path):
    req_file = tmp_path / "requirement.md"
    req_file.write_text("# My Feature\n", encoding="utf-8")
    req = load_requirement_from_dir(tmp_path)
    assert req is not None
    assert req.requirement_id == tmp_path.name
