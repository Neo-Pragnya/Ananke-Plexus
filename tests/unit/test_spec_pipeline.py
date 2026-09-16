from pathlib import Path

from ananke.plexus.api import Ananke
from ananke.plexus.specs.models import Requirement


def test_converge_creates_full_spec_bundle(tmp_path: Path) -> None:
    app = Ananke.open(tmp_path)
    app.init_project()

    requirement = Requirement(
        requirement_id="ANANKE-101",
        title="End to end spec lifecycle",
        body="Generate complete spec artifacts using provider abstraction",
        acceptance_criteria=["plan exists", "tasks exists", "lock exists"],
    )

    result = app.converge(requirement)
    assert result.ok

    feature_dir = Path(result.details["feature_dir"])
    assert (feature_dir / "requirement.md").exists()
    assert (feature_dir / "spec.md").exists()
    assert (feature_dir / "plan.md").exists()
    assert (feature_dir / "tasks.md").exists()
    assert (feature_dir / "bmad.yaml").exists()
    assert (feature_dir / "spec.lock.json").exists()


def test_validate_and_diff_return_clean_after_lock(tmp_path: Path) -> None:
    app = Ananke.open(tmp_path)
    app.init_project()

    requirement = Requirement(
        requirement_id="ANANKE-102",
        title="Spec drift check",
        body="Ensure lock and drift detection are wired",
        acceptance_criteria=["no drift after lock"],
    )

    create_result = app.converge(requirement)
    feature_dir = Path(create_result.details["feature_dir"])

    validate_result = app.validate(feature_dir)
    assert validate_result.ok

    diff_result = app.diff(feature_dir)
    assert diff_result.ok
