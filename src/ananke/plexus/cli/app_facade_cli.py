"""``ananke app``: the app facade as a structured process route (JSON in, JSON out).

Every command prints one JSON document on stdout. A failure prints ``{"error": {code, message, retryable}}`` on
stderr and exits 75 when retrying may help (EX_TEMPFAIL) or 2 when it won't. Products call this when they can't
import Ananke in-process; they never parse human-readable output.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import typer

app_app = typer.Typer(
    help="The versioned app facade as JSON commands (for products such as Dev Guru).",
    no_args_is_help=True,
)
_WS = typer.Option(Path("."), "--workspace", "-w", help="Workspace root")
_PROTO = typer.Option(
    "ananke-app/1",
    "--protocol",
    help="Protocol the caller speaks (negotiated before anything runs)",
)


def _call(protocol: str, fn: Callable[[], Any]) -> None:
    from ananke.plexus.app import AppError, AppFacade

    try:
        AppFacade.negotiate([protocol])
        result = fn()
    except AppError as exc:
        sys.stderr.write(json.dumps({"error": exc.as_dict()}) + "\n")
        raise typer.Exit(75 if exc.retryable else 2) from exc
    typer.echo(json.dumps(result, indent=2, sort_keys=True))


def _facade(workspace: Path):  # type: ignore[no-untyped-def]
    from ananke.plexus.app import AppFacade

    return AppFacade(workspace)


@app_app.command("capabilities")
def capabilities(workspace: Path = _WS, protocol: str = _PROTO) -> None:
    """Protocol, Ananke version, operations and run kinds."""
    _call(protocol, lambda: _facade(workspace).capabilities())


@app_app.command("run")
def run(
    kind: str = typer.Argument(..., help="Run kind, e.g. graph.build"),
    key: str = typer.Option(
        ..., "--key", help="Idempotency key: the same key and request return the same run"
    ),
    params: str = typer.Option("{}", "--params", help="Run parameters as a JSON object"),
    capability: list[str] = typer.Option(
        [], "--capability", help="Capability ref to resolve and lock into the plan"
    ),
    start: bool = typer.Option(True, "--start/--queue-only", help="Execute now, or only queue"),
    workspace: Path = _WS,
    protocol: str = _PROTO,
) -> None:
    """Prepare and submit a run."""
    from ananke.plexus.app import AppError

    def go() -> Any:
        try:
            parameters = json.loads(params)
        except ValueError as exc:
            raise AppError("INVALID_REQUEST", f"--params is not JSON: {exc}") from exc
        if not isinstance(parameters, dict):
            raise AppError("INVALID_REQUEST", "--params must be a JSON object")
        fac = _facade(workspace)
        return fac.submit_run(fac.prepare_run(kind, parameters, list(capability)), key, start=start)

    _call(protocol, go)


@app_app.command("status")
def status(run_id: str, workspace: Path = _WS, protocol: str = _PROTO) -> None:
    """The authoritative run state."""
    _call(protocol, lambda: _facade(workspace).get_run(run_id))


@app_app.command("events")
def events(
    run_id: str,
    after: int = typer.Option(0, "--after", help="Last sequence already seen"),
    workspace: Path = _WS,
    protocol: str = _PROTO,
) -> None:
    """Events after a sequence; a retention gap is reported with the run state."""
    _call(protocol, lambda: _facade(workspace).stream_events(run_id, after))


@app_app.command("cancel")
def cancel(run_id: str, workspace: Path = _WS, protocol: str = _PROTO) -> None:
    """Cooperative cancellation: queued runs stop; finished runs stay finished."""
    _call(protocol, lambda: _facade(workspace).cancel_run(run_id))


@app_app.command("artifacts")
def artifacts(run_id: str, workspace: Path = _WS, protocol: str = _PROTO) -> None:
    """Artifacts of a run with digests, producers and input refs."""
    _call(protocol, lambda: _facade(workspace).list_artifacts(run_id))


@app_app.command("read")
def read(
    run_id: str,
    name: str,
    output: Path = typer.Option(..., "-o", "--output"),
    workspace: Path = _WS,
    protocol: str = _PROTO,
) -> None:
    """Write one artifact to a file after checking its digest."""

    def go() -> Any:
        data = _facade(workspace).read_artifact(run_id, name)
        output.write_bytes(data)
        return {"written": str(output), "bytes": len(data)}

    _call(protocol, go)


def _draft(path: Path) -> Any:
    from ananke.plexus.app import AppError

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AppError("INVALID_DRAFT", f"can't read {path}: {exc}") from exc


@app_app.command("workflow-validate")
def workflow_validate(draft: Path, workspace: Path = _WS, protocol: str = _PROTO) -> None:
    """Every problem in a workflow draft, each with a code and a next step. Runs nothing."""
    _call(protocol, lambda: _facade(workspace).validate_workflow(_draft(draft)))


@app_app.command("workflow-preview")
def workflow_preview(draft: Path, workspace: Path = _WS, protocol: str = _PROTO) -> None:
    """Order, locked versions, permissions, approvals and the plan hash. Runs nothing."""
    _call(protocol, lambda: _facade(workspace).preview_workflow(_draft(draft)))


@app_app.command("workflow-compile")
def workflow_compile(draft: Path, workspace: Path = _WS, protocol: str = _PROTO) -> None:
    """The ExecutionPlan a draft compiles to. The same draft and versions give the same plan and hash."""
    _call(protocol, lambda: _facade(workspace).compile_workflow(_draft(draft)))


@app_app.command("workflow-suggest")
def workflow_suggest(
    draft: Path,
    node: str,
    port: str,
    candidate: list[str] = typer.Option(..., "--candidate", help="Exact ref to assess (repeat)"),
    workspace: Path = _WS,
    protocol: str = _PROTO,
) -> None:
    """Which candidates fit an input port, why others don't, and which are authorized. Activates nothing."""
    _call(
        protocol,
        lambda: _facade(workspace).suggest_capabilities(_draft(draft), node, port, list(candidate)),
    )


@app_app.command("code")
def code(
    path: str = typer.Option("", "--path", help="Only paths under this prefix"),
    kind: list[str] = typer.Option([], "--kind", help="Only these node kinds (repeat)"),
    query: str = typer.Option("", "--query", help="Words to match"),
    budget: int = typer.Option(500, "--budget", help="Most nodes to return"),
    collapse: str = typer.Option("none", "--collapse", help="none, file or directory"),
    depth: int = typer.Option(1, "--depth", help="Directory levels when collapsing by directory"),
    workspace: Path = _WS,
    protocol: str = _PROTO,
) -> None:
    """A slice of the latest code graph within a node budget, optionally collapsed with edges summed."""
    _call(
        protocol,
        lambda: _facade(workspace).code_view(
            path_prefix=path,
            kinds=list(kind),
            query=query,
            budget=budget,
            collapse=collapse,
            depth=depth,
        ),
    )


@app_app.command("source")
def source(
    path: str,
    start: int = typer.Option(1, "--start"),
    end: int | None = typer.Option(None, "--end"),
    workspace: Path = _WS,
    protocol: str = _PROTO,
) -> None:
    """Lines of one source file with its digest. Read-only; stays inside the workspace."""
    _call(protocol, lambda: _facade(workspace).read_source(path, start=start, end=end))


@app_app.command("architecture")
def architecture(workspace: Path = _WS, protocol: str = _PROTO) -> None:
    """The declared architecture and its drift from the code graph."""
    _call(protocol, lambda: _facade(workspace).architecture_view())


@app_app.command("evidence")
def evidence(workspace: Path = _WS, protocol: str = _PROTO) -> None:
    """Recent runs: state, completeness, plan digest and artifacts."""
    _call(protocol, lambda: _facade(workspace).evidence_view())
