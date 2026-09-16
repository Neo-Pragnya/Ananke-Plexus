from pathlib import Path

from typer.testing import CliRunner

from ananke.plexus.apm.cli import app


def test_apm_install_and_activate(tmp_path: Path) -> None:
    runner = CliRunner()
    project = tmp_path / "project"
    project.mkdir(parents=True, exist_ok=True)

    skill_source = Path(__file__).parents[1] / "fixtures" / "sample-skill"

    install_result = runner.invoke(
        app,
        [
            "install",
            "--source",
            str(skill_source),
            "--project",
            str(project),
        ],
    )
    assert install_result.exit_code == 0

    name = "graph-reviewer@1.0.0"
    activate_result = runner.invoke(
        app,
        ["activate", "--name", name, "--project", str(project)],
    )
    assert activate_result.exit_code == 0

    list_result = runner.invoke(app, ["list", "--project", str(project)])
    assert list_result.exit_code == 0
    assert name in list_result.stdout

    info_result = runner.invoke(app, ["info", "--project", str(project)])
    assert info_result.exit_code == 0
    assert "graph-reviewer" in info_result.stdout

    bundle_path = project / "skills-bundle.tar.gz"
    export_result = runner.invoke(
        app,
        [
            "bundle",
            "export",
            "--name",
            name,
            "--project",
            str(project),
            "--output",
            str(bundle_path),
        ],
    )
    assert export_result.exit_code == 0
    assert bundle_path.exists()

    list_bundle_result = runner.invoke(app, ["bundle", "list", "--bundle", str(bundle_path)])
    assert list_bundle_result.exit_code == 0
    assert name in list_bundle_result.stdout
