"""Execute a Workflow's steps in order, each as its own subprocess.

Steps run exactly as if typed by hand: an argv list, ``shell=False``, no shell string is ever
built or parsed here. ``${name}`` placeholders in a step's command are substituted from the
``variables`` mapping (``project`` and any ``--set key=value`` passed to ``workflow run``); an
unresolved placeholder is a hard error rather than being passed through literally, so a typo in
a step never silently reaches the underlying command as a bogus argument.
"""

from __future__ import annotations

import re
import subprocess
import time
from pathlib import Path

from pydantic import BaseModel, Field

from ananke.plexus.workflows.models import Workflow, WorkflowStep

_PLACEHOLDER = re.compile(r"\$\{([a-zA-Z_][a-zA-Z0-9_]*)\}")
DEFAULT_TIMEOUT_SECONDS = 900


class WorkflowSubstitutionError(ValueError):
    pass


def _substitute(argv: list[str], variables: dict[str, str]) -> list[str]:
    def replace_in(arg: str) -> str:
        def sub(match: re.Match[str]) -> str:
            key = match.group(1)
            if key not in variables:
                raise WorkflowSubstitutionError(f"'${{{key}}}' is not set — pass --set {key}=VALUE")
            return variables[key]

        return _PLACEHOLDER.sub(sub, arg)

    return [replace_in(arg) for arg in argv]


class WorkflowStepResult(BaseModel):
    index: int
    label: str
    command: list[str]
    ok: bool
    skipped: bool = False
    exit_code: int | None = None
    duration_seconds: float = 0.0
    stdout: str = ""
    stderr: str = ""
    error: str = ""


class WorkflowRunResult(BaseModel):
    workflow: str
    ok: bool
    dry_run: bool = False
    steps: list[WorkflowStepResult] = Field(default_factory=list)


def run_workflow(
    workflow: Workflow,
    *,
    project: Path,
    variables: dict[str, str] | None = None,
    dry_run: bool = False,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> WorkflowRunResult:
    """Run every step in order; stop at the first failing step unless it allows continuing."""
    vars_: dict[str, str] = {"project": str(project), **(variables or {})}
    result = WorkflowRunResult(workflow=workflow.name, ok=True, dry_run=dry_run)
    for index, step in enumerate(workflow.steps, start=1):
        step_result = _run_step(index, step, vars_, project, dry_run=dry_run, timeout=timeout)
        result.steps.append(step_result)
        if not step_result.ok and not step.continue_on_error:
            result.ok = False
            for later_index, later_step in enumerate(workflow.steps[index:], start=index + 1):
                result.steps.append(
                    WorkflowStepResult(
                        index=later_index,
                        label=later_step.label,
                        command=later_step.command,
                        ok=False,
                        skipped=True,
                    )
                )
            break
        if not step_result.ok:
            result.ok = False
    return result


def _run_step(
    index: int,
    step: WorkflowStep,
    variables: dict[str, str],
    project: Path,
    *,
    dry_run: bool,
    timeout: int,
) -> WorkflowStepResult:
    try:
        argv = _substitute(step.command, variables)
    except WorkflowSubstitutionError as exc:
        return WorkflowStepResult(
            index=index, label=step.label, command=step.command, ok=False, error=str(exc)
        )
    if dry_run:
        return WorkflowStepResult(index=index, label=step.label, command=argv, ok=True)
    started = time.monotonic()
    try:
        proc = subprocess.run(
            argv,
            cwd=str(project),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        return WorkflowStepResult(
            index=index,
            label=step.label,
            command=argv,
            ok=False,
            error=f"command not found: {argv[0]} ({exc})",
        )
    except subprocess.TimeoutExpired:
        return WorkflowStepResult(
            index=index,
            label=step.label,
            command=argv,
            ok=False,
            error=f"timed out after {timeout}s",
            duration_seconds=time.monotonic() - started,
        )
    return WorkflowStepResult(
        index=index,
        label=step.label,
        command=argv,
        ok=proc.returncode == 0,
        exit_code=proc.returncode,
        duration_seconds=time.monotonic() - started,
        stdout=proc.stdout,
        stderr=proc.stderr,
    )


def parse_variables(pairs: list[str]) -> dict[str, str]:
    """Parse ``--set key=value`` options into a variables mapping."""
    out: dict[str, str] = {}
    for pair in pairs:
        key, sep, value = pair.partition("=")
        if not sep or not key.strip():
            raise ValueError(f"--set expects KEY=VALUE, got {pair!r}")
        out[key.strip()] = value
    return out


__all__: list[str] = [
    "DEFAULT_TIMEOUT_SECONDS",
    "WorkflowRunResult",
    "WorkflowStepResult",
    "WorkflowSubstitutionError",
    "parse_variables",
    "run_workflow",
]
