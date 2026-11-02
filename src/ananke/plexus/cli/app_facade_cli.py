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
