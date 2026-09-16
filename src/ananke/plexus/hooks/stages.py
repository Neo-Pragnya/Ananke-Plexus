"""Stage-specific check definitions for each hook stage.

pre-commit:  fast, low-noise checks against staged changes.
post-commit: cache refresh only; no tracked-file mutation.
pre-push:    full verification suite.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from ananke.plexus.hooks.models import HookRunResult, HookStage


@dataclass
class StageCheck:
    name: str
    command: list[str]
    required_binary: str = ""
    targets: list[str] = field(default_factory=list)

    def run(self, repository_root: Path) -> dict[str, object]:
        binary = self.required_binary or self.command[0]
        if not shutil.which(binary):
            return {"check": self.name, "status": "UNAVAILABLE", "summary": f"{binary} not found"}

        if self.targets and not any((repository_root / t).exists() for t in self.targets):
            return {"check": self.name, "status": "SKIPPED", "summary": "no matching targets"}

        result = subprocess.run(  # noqa: S603
            [shutil.which(binary), *self.command[1:]],  # type: ignore[list-item]
            cwd=str(repository_root),
            capture_output=True,
            text=True,
            check=False,
        )
        ok = result.returncode == 0
        tail = result.stderr.strip() or result.stdout.strip() or ""
        summary = tail.splitlines()[-1][:160] if tail else "ok"
        return {
            "check": self.name,
            "status": "PASS" if ok else "BLOCKED",
            "summary": summary,
            "exit_code": result.returncode,
        }


_PRE_COMMIT_CHECKS = [
    StageCheck("ruff-check", ["ruff", "check", "src", "tests"], targets=["src"]),
    StageCheck("ruff-format", ["ruff", "format", "--check", "src", "tests"], targets=["src"]),
    StageCheck(
        "secret-scan",
        ["gitleaks", "protect", "--staged", "--no-banner"],
        targets=[".git"],
    ),
    StageCheck("schema-validate", ["ananke", "spec", "validate"], required_binary="ananke"),
]

_POST_COMMIT_CHECKS: list[StageCheck] = []

_PRE_PUSH_CHECKS = [
    StageCheck("pytest", ["pytest", "--tb=short", "-q"], targets=["tests"]),
    StageCheck("mypy", ["mypy", "src"], targets=["src"]),
    StageCheck("semgrep", ["semgrep", "scan", "--config", "auto", "src"], targets=["src"]),
    StageCheck("pip-audit", ["pip-audit"], targets=["pyproject.toml"]),
    StageCheck("trivy", ["trivy", "fs", "."], targets=["pyproject.toml"]),
    StageCheck(
        "architecture",
        ["ananke", "arch", "validate"],
        required_binary="ananke",
    ),
]

_STAGE_MAP: dict[HookStage, list[StageCheck]] = {
    "pre-commit": _PRE_COMMIT_CHECKS,
    "post-commit": _POST_COMMIT_CHECKS,
    "pre-push": _PRE_PUSH_CHECKS,
}


def run_stage(repository_root: Path, stage: HookStage) -> HookRunResult:
    checks = _STAGE_MAP.get(stage, [])
    results = [c.run(repository_root) for c in checks]
    blocked = [r for r in results if r.get("status") == "BLOCKED"]
    ok = len(blocked) == 0
    summary = (
        f"{stage}: {len(results)} checks, {len(blocked)} blocked"
        if results
        else f"{stage}: no checks configured"
    )
    return HookRunResult(stage=stage, ok=ok, summary=summary, checks=results)
