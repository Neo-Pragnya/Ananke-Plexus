from pathlib import Path

from ananke.plexus.api import Ananke
from ananke.plexus.specs.models import Requirement


def test_init_create_spec_and_verify(tmp_path: Path) -> None:
    app = Ananke.open(tmp_path)

    init_result = app.init_project()
    assert init_result.ok
    assert (tmp_path / ".ananke" / "config.toml").exists()
    assert (tmp_path / ".ananke" / "config.local.toml").exists()
    assert (tmp_path / ".ananke" / "secrets" / "adapters.env").exists()

    spec_result = app.create_spec(
        Requirement(
            requirement_id="DEMO-101",
            title="Create evidence manifest",
            body="Create evidence manifest for verify step",
            acceptance_criteria=["manifest created", "checksum emitted"],
        )
    )
    assert spec_result.ok

    verify_result = app.verify()
    assert verify_result.ok

    evidence_root = tmp_path / ".ananke" / "evidence"
    assert evidence_root.exists()
    assert any(item.is_dir() for item in evidence_root.iterdir())
