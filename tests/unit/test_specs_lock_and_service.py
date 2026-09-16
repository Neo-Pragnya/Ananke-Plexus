"""Tests for spec lock and full service (D8/D9)."""

import json
from pathlib import Path

from ananke.plexus.specs.lock import detect_drift, write_spec_lock
from ananke.plexus.specs.service import (
    diff_spec,
    lock_spec,
    validate_spec,
)


def _make_feature_dir(tmp: Path) -> Path:
    feature_dir = tmp / "DEMO-1"
    feature_dir.mkdir()
    for name in ("requirement.md", "spec.md", "plan.md", "tasks.md", "bmad.yaml"):
        (feature_dir / name).write_text(f"# {name}\n", encoding="utf-8")
    return feature_dir


def test_write_spec_lock(tmp_path):
    feature_dir = _make_feature_dir(tmp_path)
    lock_path = write_spec_lock(feature_dir)
    assert lock_path.exists()
    data = json.loads(lock_path.read_text(encoding="utf-8"))
    # The lock stores either spec_hash or content_hash
    assert any(k in data for k in ("spec_hash", "content_hash", "files"))


def test_detect_drift_no_lock(tmp_path):
    feature_dir = _make_feature_dir(tmp_path)
    drift = detect_drift(feature_dir)
    assert "LOCK_MISSING" in drift


def test_detect_drift_after_lock(tmp_path):
    feature_dir = _make_feature_dir(tmp_path)
    write_spec_lock(feature_dir)
    drift = detect_drift(feature_dir)
    assert drift == []


def test_detect_drift_after_modification(tmp_path):
    feature_dir = _make_feature_dir(tmp_path)
    write_spec_lock(feature_dir)
    # Modify spec after lock
    (feature_dir / "spec.md").write_text("# Modified\n", encoding="utf-8")
    drift = detect_drift(feature_dir)
    assert len(drift) > 0
    assert any("spec.md" in d for d in drift)


def test_validate_spec_missing_files(tmp_path):
    feature_dir = tmp_path / "incomplete"
    feature_dir.mkdir()
    ok, errors = validate_spec(feature_dir)
    assert ok is False
    assert len(errors) > 0


def test_validate_spec_complete(tmp_path):
    feature_dir = _make_feature_dir(tmp_path)
    write_spec_lock(feature_dir)
    ok, _errors = validate_spec(feature_dir)
    assert ok is True


def test_diff_spec_no_lock(tmp_path):
    feature_dir = _make_feature_dir(tmp_path)
    result = diff_spec(feature_dir)
    assert "LOCK_MISSING" in result


def test_lock_spec_service(tmp_path):
    feature_dir = _make_feature_dir(tmp_path)
    lock_path = lock_spec(feature_dir)
    assert lock_path.exists()
