"""Config migration utilities."""

from __future__ import annotations

import tomllib
from pathlib import Path

from pydantic import ValidationError

from ananke.plexus.config.models import ConfigModel, TroveConfig
from ananke.plexus.core.paths import project_ananke_dir

SECTION_ORDER = ["project", "ananke", "spec", "security"]
# Free-form map sections: ``[troves.<name>]`` tables, each validated by its own model and kept verbatim.
MAP_SECTIONS = {"troves": TroveConfig}


def _default_tree() -> dict[str, dict[str, object]]:
    raw = ConfigModel().model_dump()
    tree: dict[str, dict[str, object]] = {}
    for section in SECTION_ORDER:
        value = raw.get(section, {})
        tree[section] = value if isinstance(value, dict) else {}
    return tree


def _format_toml_value(value: object) -> str:
    if isinstance(value, list):
        return "[" + ", ".join(_format_toml_value(v) for v in value) + "]"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{text}"'


def _render_config_toml(values: dict[str, dict[str, object]]) -> str:
    lines: list[str] = []
    for section in SECTION_ORDER:
        section_values = values.get(section, {})
        lines.append(f"[{section}]")
        for key, value in section_values.items():
            lines.append(f"{key} = {_format_toml_value(value)}")
        lines.append("")
    for section in MAP_SECTIONS:
        for name, table in (values.get(section) or {}).items():
            if not isinstance(table, dict):
                continue
            lines.append(
                f'[{section}."{name}"]'
                if not name.replace("-", "").replace("_", "").isalnum()
                else f"[{section}.{name}]"
            )
            for key, value in table.items():
                lines.append(f"{key} = {_format_toml_value(value)}")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def migrate_config_file(repository_root: Path, apply: bool = False) -> dict[str, object]:
    config_path = project_ananke_dir(repository_root) / "config.toml"
    defaults = _default_tree()

    if not config_path.exists():
        if not apply:
            return {
                "ok": True,
                "changed": False,
                "created": False,
                "missing_count": 1,
                "unknown_count": 0,
                "summary": "Config file missing; run with --apply to create defaults.",
                "config_path": str(config_path),
            }
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(_render_config_toml(defaults), encoding="utf-8")
        return {
            "ok": True,
            "changed": True,
            "created": True,
            "missing_count": 1,
            "unknown_count": 0,
            "summary": "Config file created from defaults.",
            "config_path": str(config_path),
        }

    parsed = tomllib.loads(config_path.read_text(encoding="utf-8"))
    unknown_sections: list[str] = []
    unknown_keys: list[str] = []
    missing_keys: list[str] = []

    merged: dict[str, dict[str, object]] = {}
    for section in SECTION_ORDER:
        current_section = parsed.get(section, {})
        section_dict = current_section if isinstance(current_section, dict) else {}
        target = dict(defaults[section])
        for key, value in section_dict.items():
            if key in defaults[section]:
                target[key] = value
            else:
                unknown_keys.append(f"{section}.{key}")
        for key in defaults[section]:
            if key not in section_dict:
                missing_keys.append(f"{section}.{key}")
        merged[section] = target

    for section, model in MAP_SECTIONS.items():
        tables = parsed.get(section, {})
        if not isinstance(tables, dict):
            unknown_sections.append(section)
            continue
        kept: dict[str, object] = {}
        for name, table in tables.items():
            try:
                model.model_validate(table)
            except ValidationError as exc:
                unknown_keys += [
                    f"{section}.{name}.{'.'.join(str(p) for p in e['loc'])}" for e in exc.errors()
                ]
                continue
            kept[name] = table
        merged[section] = kept

    for section in parsed:
        if section not in defaults and section not in MAP_SECTIONS:
            unknown_sections.append(section)

    unknown_count = len(unknown_sections) + len(unknown_keys)
    missing_count = len(missing_keys)
    changed = missing_count > 0

    if not apply:
        return {
            "ok": True,
            "changed": changed,
            "created": False,
            "missing_count": missing_count,
            "unknown_count": unknown_count,
            "unknown_sections": unknown_sections,
            "unknown_keys": unknown_keys,
            "missing_keys": missing_keys,
            "summary": "Config migration analysis complete.",
            "config_path": str(config_path),
        }

    if unknown_count > 0:
        return {
            "ok": False,
            "changed": False,
            "created": False,
            "missing_count": missing_count,
            "unknown_count": unknown_count,
            "unknown_sections": unknown_sections,
            "unknown_keys": unknown_keys,
            "summary": "Refusing to rewrite config with unknown keys present.",
            "config_path": str(config_path),
        }

    if changed:
        config_path.write_text(_render_config_toml(merged), encoding="utf-8")

    return {
        "ok": True,
        "changed": changed,
        "created": False,
        "missing_count": missing_count,
        "unknown_count": 0,
        "summary": "Config migration completed.",
        "config_path": str(config_path),
    }
