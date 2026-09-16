from pathlib import Path

from ananke.plexus.api import Ananke


def test_config_migrate_reports_missing_keys(tmp_path: Path) -> None:
    app = Ananke.open(tmp_path)
    app.init_project()

    config_path = tmp_path / ".ananke" / "config.toml"
    config_path.write_text('[project]\nname = "demo"\n', encoding="utf-8")

    result = app.config_migrate(apply=False)
    assert result.ok
    assert int(result.details["missing_count"]) > 0
    assert result.details["changed"] is True


def test_config_migrate_refuses_unknown_keys_on_apply(tmp_path: Path) -> None:
    app = Ananke.open(tmp_path)
    app.init_project()

    config_path = tmp_path / ".ananke" / "config.toml"
    config_path.write_text(
        '[project]\nname = "demo"\n\n[custom]\nfoo = "bar"\n',
        encoding="utf-8",
    )

    result = app.config_migrate(apply=True)
    assert not result.ok
    assert int(result.details["unknown_count"]) > 0


def test_config_migrate_apply_fills_defaults(tmp_path: Path) -> None:
    app = Ananke.open(tmp_path)
    app.init_project()

    config_path = tmp_path / ".ananke" / "config.toml"
    config_path.write_text('[project]\nname = "demo"\n', encoding="utf-8")

    result = app.config_migrate(apply=True)
    assert result.ok
    assert result.details["changed"] is True
    content = config_path.read_text(encoding="utf-8")
    assert "[security]" in content
    assert "[ananke]" in content
