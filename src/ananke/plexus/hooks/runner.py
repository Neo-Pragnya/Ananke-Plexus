"""Hook dispatch entry point called by installed hook scripts."""

from __future__ import annotations

import sys
from pathlib import Path

from ananke.plexus.hooks.models import HookRunResult, HookStage
from ananke.plexus.hooks.stages import run_stage


def dispatch(
    repository_root: Path,
    stage: HookStage,
    *,
    verbose: bool = False,
) -> HookRunResult:
    result = run_stage(repository_root, stage)
    if verbose:
        for check in result.checks:
            status = str(check.get("status", "?"))
            name = str(check.get("check", "?"))
            summary = str(check.get("summary", ""))
            print(f"  [{status}] {name}: {summary}", file=sys.stderr)
    return result
