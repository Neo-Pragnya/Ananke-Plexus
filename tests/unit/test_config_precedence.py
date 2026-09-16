from pathlib import Path

from ananke.plexus.config.loader import load_config, write_default_config
from ananke.plexus.core.paths import project_ananke_dir


def test_load_config_uses_defaults_when_missing(tmp_path: Path) -> None:
    config = load_config(tmp_path)
    assert config.project.name == "ananke-project"
    assert config.ananke.mode == "developer"


def test_load_config_local_overrides_primary(tmp_path: Path) -> None:
    write_default_config(tmp_path)
    ananke_dir = project_ananke_dir(tmp_path)
    (ananke_dir / "config.toml").write_text(
        '[ananke]\nmode = "developer"\noffline = false\n',
        encoding="utf-8",
    )
    (ananke_dir / "config.local.toml").write_text(
        '[ananke]\nmode = "ci"\n',
        encoding="utf-8",
    )

    config = load_config(tmp_path)
    assert config.ananke.mode == "ci"
    assert config.ananke.offline is False


def test_load_config_env_overrides(monkeypatch, tmp_path: Path) -> None:
    write_default_config(tmp_path)
    monkeypatch.setenv("ANANKE_MODE", "strict")
    monkeypatch.setenv("ANANKE_OFFLINE", "true")
    monkeypatch.setenv("ANANKE_SECURITY_SAST", "false")

    config = load_config(tmp_path)
    assert config.ananke.mode == "strict"
    assert config.ananke.offline is True
    assert config.security.sast is False
