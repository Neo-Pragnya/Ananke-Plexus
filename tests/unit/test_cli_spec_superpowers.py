from pathlib import Path

from typer.testing import CliRunner

from ananke.plexus.cli.app import app


def test_cli_spec_superpowers(tmp_path: Path) -> None:
    runner = CliRunner()

    init_result = runner.invoke(app, ["init", "--project", str(tmp_path)])
    assert init_result.exit_code == 0

    result = runner.invoke(
        app,
        [
            "spec",
            "superpowers",
            "--id",
            "ANANKE-103",
            "--title",
            "CLI superpowers flow",
            "--acceptance",
            "creates spec",
            "--acceptance",
            "creates lock",
            "--project",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0
    assert "Spec pipeline converged." in result.stdout
