"""Threshold configuration — load from .ananke/evals/config.yaml."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_eval_config(project_root: Path) -> dict[str, Any]:
    config_path = project_root / ".ananke" / "evals" / "config.yaml"
    if not config_path.exists():
        return {}
    text = config_path.read_text(encoding="utf-8")
    try:
        import yaml

        result: dict[str, object] = yaml.safe_load(text) or {}
        return result
    except ImportError:
        result2: dict[str, object] = json.loads(text)
        return result2


def check_dependency_allowlist(
    package: str,
    config: dict[str, Any],
) -> tuple[bool, str]:
    """Return (allowed, reason). True = package may be loaded."""
    deps = config.get("dependencies", {})
    denied_licenses = set(deps.get("denied_licenses", []))
    allowed_packages = set(deps.get("allowed_packages", []))
    require_approval = set(deps.get("require_explicit_approval", []))
    deny_saas = deps.get("deny_external_saas", False)

    if package in (denied_licenses or set()):
        return False, f"denied by license policy: {denied_licenses}"
    if allowed_packages and package not in allowed_packages:
        if package in require_approval:
            return False, f"'{package}' requires explicit approval"
        if deny_saas:
            return False, f"'{package}' not in allowed_packages and deny_external_saas=true"
    return True, "allowed"
