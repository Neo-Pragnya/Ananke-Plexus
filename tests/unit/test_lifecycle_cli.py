from pathlib import Path

from typer.testing import CliRunner

from ananke.plexus.cli.app import app


def test_lifecycle_evidence_command(tmp_path: Path) -> None:
    runner = CliRunner()
    init_result = runner.invoke(app, ["init", "--project", str(tmp_path)])
    assert init_result.exit_code == 0

    evidence_result = runner.invoke(
        app,
        [
            "lifecycle",
            "evidence",
            "--project",
            str(tmp_path),
            "--limit",
            "5",
            "--since-hours",
            "24",
            "--format",
            "compact",
        ],
    )
    assert evidence_result.exit_code == 0
    assert "Lifecycle evidence loaded." in evidence_result.stdout


def test_lifecycle_evidence_csv_export_command(tmp_path: Path) -> None:
    runner = CliRunner()
    init_result = runner.invoke(app, ["init", "--project", str(tmp_path)])
    assert init_result.exit_code == 0

    csv_target = tmp_path / "events.csv"
    export_result = runner.invoke(
        app,
        [
            "lifecycle",
            "evidence",
            "--project",
            str(tmp_path),
            "--format",
            "csv",
            "--aggregate",
            "--csv-path",
            str(csv_target),
        ],
    )
    assert export_result.exit_code == 0
    assert csv_target.exists()
    assert "csv_path" in export_result.stdout


def test_lifecycle_evidence_trend_and_severity_command(tmp_path: Path) -> None:
    runner = CliRunner()
    init_result = runner.invoke(app, ["init", "--project", str(tmp_path)])
    assert init_result.exit_code == 0

    result = runner.invoke(
        app,
        [
            "lifecycle",
            "evidence",
            "--project",
            str(tmp_path),
            "--severity",
            "critical",
            "--trend",
            "hour",
            "--format",
            "compact",
        ],
    )
    assert result.exit_code == 0
    assert "trend" in result.stdout
