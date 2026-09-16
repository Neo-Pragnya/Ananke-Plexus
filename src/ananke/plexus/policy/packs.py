"""Policy pack management — load, list, and activate named packs (B5)."""

from __future__ import annotations

import contextlib
import tomllib
from pathlib import Path

from ananke.plexus.policy.engine import PolicyRule

_BUILTIN_PACKS = {
    "baseline": "baseline",
    "python-library": "python_library",
    "agentic-security": "agentic_security",
    "enterprise-strict": "enterprise_strict",
}

_BASELINE_TOML = """\
[[rule]]
id = "policy.core.fail-closed"
stage = "verify"
severity = "high"
gate = "core"
assert = "config.ananke.fail_closed == true"
on_failure = "block"

[[rule]]
id = "security.no-secret"
stage = "pre_commit"
severity = "critical"
gate = "gitleaks"
assert = "findings.count == 0"
on_failure = "block"
"""

_PYTHON_LIBRARY_TOML = """\
[[rule]]
id = "quality.ruff-check"
stage = "pre_commit"
severity = "medium"
gate = "ruff"
assert = "gates.ruff.exit_code == 0"
on_failure = "block"

[[rule]]
id = "quality.mypy-strict"
stage = "verify"
severity = "high"
gate = "mypy"
assert = "gates.mypy.exit_code == 0"
on_failure = "block"

[[rule]]
id = "tests.minimum-coverage"
stage = "verify"
severity = "medium"
gate = "pytest"
assert = "coverage.line >= 85"
on_failure = "block"

[[rule]]
id = "security.pip-audit"
stage = "verify"
severity = "high"
gate = "pip-audit"
assert = "gates.pip-audit.exit_code == 0"
on_failure = "block"

[[rule]]
id = "security.license-scan"
stage = "verify"
severity = "medium"
gate = "license"
assert = "gates.license.exit_code == 0"
on_failure = "warn"
"""

_AGENTIC_SECURITY_TOML = """\
[[rule]]
id = "agent.no-secret-in-prompt"
stage = "verify"
severity = "critical"
gate = "gitleaks"
assert = "findings.count == 0"
on_failure = "block"

[[rule]]
id = "agent.sast-clean"
stage = "verify"
severity = "high"
gate = "semgrep"
assert = "gates.semgrep.exit_code == 0"
on_failure = "block"

[[rule]]
id = "agent.dependency-audit"
stage = "verify"
severity = "high"
gate = "pip-audit"
assert = "gates.pip-audit.exit_code == 0"
on_failure = "block"

[[rule]]
id = "agent.trivy-clean"
stage = "verify"
severity = "high"
gate = "trivy"
assert = "gates.trivy.exit_code == 0"
on_failure = "block"

[[rule]]
id = "agent.arch.no-domain-cycle"
stage = "pre_push"
severity = "high"
gate = "architecture"
assert = "graph.domain_cycles == 0"
on_failure = "block"
"""

_ENTERPRISE_STRICT_TOML = """\
[[rule]]
id = "strict.fail-closed"
stage = "verify"
severity = "critical"
gate = "core"
assert = "config.ananke.fail_closed == true"
on_failure = "block"

[[rule]]
id = "strict.no-secret"
stage = "pre_commit"
severity = "critical"
gate = "gitleaks"
assert = "findings.count == 0"
on_failure = "block"

[[rule]]
id = "strict.sast"
stage = "verify"
severity = "critical"
gate = "semgrep"
assert = "gates.semgrep.exit_code == 0"
on_failure = "block"

[[rule]]
id = "strict.sca"
stage = "verify"
severity = "critical"
gate = "pip-audit"
assert = "gates.pip-audit.exit_code == 0"
on_failure = "block"

[[rule]]
id = "strict.licenses"
stage = "verify"
severity = "high"
gate = "license"
assert = "gates.license.exit_code == 0"
on_failure = "block"

[[rule]]
id = "strict.arch-no-cycle"
stage = "pre_push"
severity = "critical"
gate = "architecture"
assert = "graph.domain_cycles == 0"
on_failure = "block"

[[rule]]
id = "strict.tests"
stage = "verify"
severity = "critical"
gate = "pytest"
assert = "gates.pytest.exit_code == 0"
on_failure = "block"

[[rule]]
id = "strict.mypy"
stage = "verify"
severity = "high"
gate = "mypy"
assert = "gates.mypy.exit_code == 0"
on_failure = "block"
"""

_PACK_CONTENT: dict[str, str] = {
    "baseline": _BASELINE_TOML,
    "python-library": _PYTHON_LIBRARY_TOML,
    "agentic-security": _AGENTIC_SECURITY_TOML,
    "enterprise-strict": _ENTERPRISE_STRICT_TOML,
}


def list_builtin_packs() -> list[str]:
    return sorted(_PACK_CONTENT.keys())


def load_builtin_pack(name: str) -> list[PolicyRule]:
    content = _PACK_CONTENT.get(name)
    if content is None:
        raise ValueError(f"Unknown builtin pack: {name!r}. Available: {list_builtin_packs()}")
    payload = tomllib.loads(content)
    rules = []
    for raw in payload.get("rule", []):
        if isinstance(raw, dict):
            rules.append(PolicyRule.model_validate(raw))
    return rules


def install_pack(repository_root: Path, pack_name: str) -> Path:
    """Write a built-in pack to the project policy directory."""
    content = _PACK_CONTENT.get(pack_name)
    if content is None:
        raise ValueError(f"Unknown pack: {pack_name!r}")
    policy_dir = repository_root / ".ananke" / "policy"
    policy_dir.mkdir(parents=True, exist_ok=True)
    dest = policy_dir / f"{pack_name}.toml"
    dest.write_text(content, encoding="utf-8")
    return dest


def load_project_packs(repository_root: Path) -> list[PolicyRule]:
    """Load all TOML rules from the project policy directory."""
    policy_dir = repository_root / ".ananke" / "policy"
    rules: list[PolicyRule] = []
    if not policy_dir.exists():
        return rules
    for path in sorted(policy_dir.glob("*.toml")):
        try:
            payload = tomllib.loads(path.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError:
            continue
        for raw in payload.get("rule", []):
            if isinstance(raw, dict):
                with contextlib.suppress(Exception):
                    rules.append(PolicyRule.model_validate(raw))
    return rules
