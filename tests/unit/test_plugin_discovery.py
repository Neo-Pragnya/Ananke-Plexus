from pathlib import Path

from ananke.plexus.api import Ananke


def test_plugin_list_command_result(tmp_path: Path) -> None:
    app = Ananke.open(tmp_path)
    app.init_project()

    result = app.plugin_list()
    assert result.ok
    assert "count" in result.details
