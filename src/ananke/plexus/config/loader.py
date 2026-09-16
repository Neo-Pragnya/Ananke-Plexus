"""Load and write Ananke configuration."""

import os
import tomllib
from pathlib import Path

from ananke.plexus.config.models import ConfigModel
from ananke.plexus.core.paths import project_ananke_dir

DEFAULT_CONFIG_TOML = """[project]
name = "ananke-project"
default_branch = "main"

[ananke]
mode = "developer"
fail_closed = true
offline = false

[spec]
provider = "speckit"
contract_mode = "ananke-bmad"

[security]
allow_network = false
secret_scan = true
sast = true
sca = true
license_scan = true
"""


def write_default_config(repository_root: Path) -> Path:
    config_path = project_ananke_dir(repository_root) / "config.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    if not config_path.exists():
        config_path.write_text(DEFAULT_CONFIG_TOML, encoding="utf-8")
    return config_path


def _merge_sections(base: dict[str, object], override: dict[str, object]) -> dict[str, object]:
    merged = dict(base)
    for key, value in override.items():
        existing = merged.get(key)
        if isinstance(value, dict) and isinstance(existing, dict):
            merged[key] = _merge_sections(
                existing,
                value,
            )
            continue
        merged[key] = value
    return merged


def _to_bool(raw: str, default: bool) -> bool:
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return default


def _apply_env_overrides(data: dict[str, object]) -> dict[str, object]:
    merged = dict(data)
    env_map: dict[str, tuple[str, str]] = {
        "ANANKE_PROJECT_NAME": ("project", "name"),
        "ANANKE_PROJECT_DEFAULT_BRANCH": ("project", "default_branch"),
        "ANANKE_MODE": ("ananke", "mode"),
        "ANANKE_SPEC_PROVIDER": ("spec", "provider"),
        "ANANKE_SPEC_CONTRACT_MODE": ("spec", "contract_mode"),
    }
    bool_map: dict[str, tuple[str, str]] = {
        "ANANKE_FAIL_CLOSED": ("ananke", "fail_closed"),
        "ANANKE_OFFLINE": ("ananke", "offline"),
        "ANANKE_SECURITY_ALLOW_NETWORK": ("security", "allow_network"),
        "ANANKE_SECURITY_SECRET_SCAN": ("security", "secret_scan"),
        "ANANKE_SECURITY_SAST": ("security", "sast"),
        "ANANKE_SECURITY_SCA": ("security", "sca"),
        "ANANKE_SECURITY_LICENSE_SCAN": ("security", "license_scan"),
    }

    for env_key, (section, key) in env_map.items():
        raw = os.getenv(env_key)
        if raw is None:
            continue
        section_obj = merged.get(section, {})
        section_dict = section_obj if isinstance(section_obj, dict) else {}
        section_dict[key] = raw
        merged[section] = section_dict

    for env_key, (section, key) in bool_map.items():
        raw = os.getenv(env_key)
        if raw is None:
            continue
        section_obj = merged.get(section, {})
        section_dict = section_obj if isinstance(section_obj, dict) else {}
        default_value = bool(section_dict.get(key, False))
        section_dict[key] = _to_bool(raw, default_value)
        merged[section] = section_dict

    return merged


def load_config(repository_root: Path) -> ConfigModel:
    config_path = project_ananke_dir(repository_root) / "config.toml"
    local_path = project_ananke_dir(repository_root) / "config.local.toml"

    data: dict[str, object] = ConfigModel().model_dump()
    if config_path.exists():
        primary = tomllib.loads(config_path.read_text(encoding="utf-8"))
        data = _merge_sections(data, primary)
    if local_path.exists():
        local = tomllib.loads(local_path.read_text(encoding="utf-8"))
        data = _merge_sections(data, local)

    data = _apply_env_overrides(data)
    return ConfigModel.model_validate(data)
