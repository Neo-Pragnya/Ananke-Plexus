"""Run a compiled workflow: the ``workflow.run`` run kind.

Uses what already exists: the compiler, the scheduler, agent backends and the facade's events and artifacts.

What it guards, before any step starts:

- the plan is the one the caller previewed (``plan_hash``), compiled again against the registry at run time;
- every step that needs approval was approved in this request;
- the backend was named explicitly and is available, and can do what each capability declares it needs;
- every step can execute at all. Instruction capabilities go to the backend. Other kinds would run code, and have no
  executor, so a workflow containing one is refused rather than partly run.

A backend's capability flags say what it can do; Ananke can't confine a real agent to what a capability declares, so
real runs carry a ``PERMISSIONS_NOT_ENFORCED`` warning. A simulated backend's run is never marked complete.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from ananke.plexus.assembly import STEP_TYPE, CompileError, compile_workflow
from ananke.plexus.backends.base import BackendRequest, BackendTask
from ananke.plexus.backends.registry import BackendRegistry
from ananke.plexus.execution.scheduler import run_plan

if TYPE_CHECKING:
    from pathlib import Path

    from ananke.plexus.app import AppFacade
    from ananke.plexus.execution.plan import ExecutionStep

INSTRUCTION_KINDS = frozenset({"skill", "agent", "instruction", "template"})
INSTRUCTION_FILES = ("SKILL.md", "AGENT.md", "INSTRUCTIONS.md", "README.md")
# permission category → the backend capability it needs. Network has no capability to check against.
NEEDED_CAPABILITY = {
    "filesystem.read": "filesystem_read",
    "filesystem.write": "filesystem_write",
    "shell.allow": "shell",
}


def _app_error(code: str, message: str, *, retryable: bool = False) -> Exception:
    from ananke.plexus.app import AppError

    return AppError(code, message, retryable=retryable)


def _instructions(registry: Any, ref: str) -> str:
    record = registry.exact_version(ref)
    files = registry.files(record)
    for name in INSTRUCTION_FILES:
        if name in files:
            return str(files[name].decode("utf-8", "replace"))
    raise _app_error("NO_INSTRUCTIONS", f"{ref} holds none of {', '.join(INSTRUCTION_FILES)}")


def _warn(app: AppFacade, run_id: str, code: str, message: str) -> None:
    run = app.get_run(run_id)
    run["warnings"].append({"code": code, "message": message, "retryable": False})
    app._save(run)


def execute(app: AppFacade, run_id: str, params: dict[str, Any]) -> None:
    draft, backend_name, plan_hash = (
        params.get("draft"),
        params.get("backend"),
        params.get("plan_hash"),
    )
    if not isinstance(draft, dict) or not backend_name or not plan_hash:
        raise _app_error(
            "INVALID_REQUEST",
            "workflow.run needs parameters.draft, backend (named explicitly) and plan_hash from a preview",
        )
    approved = set(params.get("approved") or [])
    inputs = dict(params.get("inputs") or {})

    registry = app._registry()
    try:
        compiled = compile_workflow(draft, registry)
    except CompileError as exc:
        problems = "; ".join(f"{i.code}: {i.message}" for i in exc.validation.issues)
        raise _app_error("WORKFLOW_INVALID", problems) from exc
    if compiled.plan_hash != plan_hash:
        raise _app_error(
            "STALE_PLAN",
            f"this draft now compiles to {compiled.plan_hash}, not the previewed {plan_hash}; preview it again",
        )
    plan = compiled.plan

    needed = {a.step_id for a in plan.approvals}
    if approved - needed:
        raise _app_error(
            "INVALID_REQUEST", f"nothing to approve on: {', '.join(sorted(approved - needed))}"
        )
    if needed - approved:
        raise _app_error(
            "APPROVAL_REQUIRED",
            f"approve these steps to run the workflow: {', '.join(sorted(needed - approved))}",
        )

    backend: Any = BackendRegistry.load(app.workspace).get_backend(str(backend_name))
    if backend is None:
        raise _app_error(
            "BACKEND_NOT_FOUND", f"no backend {backend_name!r}: see `ananke backend list`"
        )
    if not backend.available():
        raise _app_error(
            "BACKEND_UNAVAILABLE", f"backend {backend_name!r} isn't available here", retryable=True
        )

    for step in plan.steps:
        kind = step.params["kind"]
        if kind not in INSTRUCTION_KINDS:
            raise _app_error(
                "NO_EXECUTOR",
                f"{step.step_id}: nothing can execute a {kind} capability yet; only "
                f"{', '.join(sorted(INSTRUCTION_KINDS))} run. Nothing was started.",
            )
        for permission in step.params["permissions"]:
            capability = NEEDED_CAPABILITY.get(permission)
            if capability and not getattr(backend.capabilities, capability, False):
                raise _app_error(
                    "PERMISSION_NOT_GRANTED",
                    f"{step.step_id} needs {permission}, which backend {backend_name!r} can't provide",
                )
    missing = [w for w in app.preview_workflow(draft)["workflow_inputs"] if w not in inputs]
    if missing:
        raise _app_error("INVALID_REQUEST", f"workflow inputs not supplied: {', '.join(missing)}")
    instructions = {
        s.step_id: _instructions(registry, s.params["capability_ref"]) for s in plan.steps
    }

    if backend.metadata.adapter_type == "fake":
        _warn(app, run_id, "SIMULATED_BACKEND", "a fake backend ran this: no real work was done")
    else:
        _warn(
            app,
            run_id,
            "PERMISSIONS_NOT_ENFORCED",
            "Ananke checks what a capability declares, but can't confine the agent to it",
        )

    app.register_artifact(
        run_id,
        "workflow-plan.json",
        plan.model_dump_json(indent=2).encode(),
        media_type="application/json",
        producer="ananke.assembly",
        input_refs=[f"plan:{compiled.plan_hash}", *(f"approved:{s}" for s in sorted(approved))],
    )

    outputs: dict[str, str] = {}
    refs: dict[str, str] = {}

    def run_step(step: ExecutionStep, root: Path) -> dict[str, object]:
        if app.get_run(run_id)["state"] == "cancelled":
            return {"status": "CANCELLED"}
        app._event(
            run_id, "step.started", {"step": step.step_id, "ref": step.params["capability_ref"]}
        )
        sources = {
            port: [v.split(".", 1)[0] for v in vs] for port, vs in step.params["bindings"].items()
        }
        # a port that accepts several sources receives a list
        bound: dict[str, object] = {
            port: outputs[names[0]] if len(names) == 1 else [outputs[n] for n in names]
            for port, names in sources.items()
        }
        supplied = {
            name.split(".", 1)[1]: value
            for name, value in inputs.items()
            if name.startswith(f"{step.step_id}.")
        }
        session = backend.start(
            BackendRequest(spec_id=plan.spec_id, task_description=step.params["capability_ref"])
        )
        result = backend.send(
            session,
            BackendTask(
                task_id=step.step_id,
                instruction=instructions[step.step_id],
                context={"inputs": {**supplied, **bound}, "config": step.params["config"]},
            ),
        )
        if not result.ok:
            app._event(run_id, "step.failed", {"step": step.step_id})
            raise RuntimeError(result.error or "the backend reported failure")
        name = f"{len(outputs) + 1:02d}-{re.sub(r'[^A-Za-z0-9_.-]', '_', step.step_id)}.output.txt"
        app.register_artifact(
            run_id,
            name,
            result.output.encode(),
            media_type="text/plain",
            producer=f"backend:{backend_name}",
            input_refs=[
                f"capability:{step.params['capability_ref']}#sha256:{step.params['digest']}",
                *(refs[n] for n in sorted({n for names in sources.values() for n in names})),
            ],
        )
        outputs[step.step_id], refs[step.step_id] = result.output, f"artifact:{name}"
        app._event(run_id, "step.succeeded", {"step": step.step_id, "artifact": name})
        return {"status": "SUCCEEDED"}

    plan.run_id = run_id
    done = run_plan(app.workspace, plan, handlers={STEP_TYPE: run_step}, max_workers=1)
    if app.get_run(run_id)["state"] == "cancelled":
        return
    if done.state != "SUCCEEDED":
        failed = [f"{s.step_id}: {s.error}" for s in done.steps if s.state == "FAILED"]
        raise _app_error("WORKFLOW_FAILED", "; ".join(failed) or f"plan ended {done.state}")
