from pathlib import Path

from typer.testing import CliRunner

from ananke.plexus.cli.app import app


def test_config_migrate_cli(tmp_path: Path) -> None:
    runner = CliRunner()
    init_result = runner.invoke(app, ["init", "--project", str(tmp_path)])
    assert init_result.exit_code == 0

    result = runner.invoke(app, ["config", "migrate", "--project", str(tmp_path)])
    assert result.exit_code == 0
    assert "Config migration analysis complete." in result.stdout


def test_evidence_prune_cli(tmp_path: Path) -> None:
    runner = CliRunner()
    init_result = runner.invoke(app, ["init", "--project", str(tmp_path)])
    assert init_result.exit_code == 0

    result = runner.invoke(
        app,
        ["evidence", "prune", "--project", str(tmp_path), "--older-than", "30d"],
    )
    assert result.exit_code == 0
    assert "Evidence prune evaluated." in result.stdout


def test_policy_explain_cli(tmp_path: Path) -> None:
    runner = CliRunner()
    init_result = runner.invoke(app, ["init", "--project", str(tmp_path)])
    assert init_result.exit_code == 0

    result = runner.invoke(app, ["policy", "explain", "--project", str(tmp_path)])
    assert result.exit_code == 0
    assert "Policy explanation loaded." in result.stdout


def test_arch_render_cli(tmp_path: Path) -> None:
    runner = CliRunner()
    init_result = runner.invoke(app, ["init", "--project", str(tmp_path)])
    assert init_result.exit_code == 0

    result = runner.invoke(app, ["arch", "render", "--project", str(tmp_path)])
    assert result.exit_code == 0
    assert "Architecture render completed." in result.stdout
