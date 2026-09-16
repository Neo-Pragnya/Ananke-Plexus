from pathlib import Path

from ananke.plexus.api import Ananke


def test_import_copilot_skills(tmp_path: Path) -> None:
    app = Ananke.open(tmp_path)
    app.init_project()

    source = Path(__file__).parents[1] / "fixtures" / "copilot-skills"
    result = app.apm_import_copilot(source)

    assert result.ok
    assert int(result.details["imported"]) >= 1
