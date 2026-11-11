"""``ananke connector``: graph, test results, API docs and build logs as typed capabilities (ASM-A09)."""

from __future__ import annotations

import json
from pathlib import Path

import typer

connector_app = typer.Typer(
    help="Built-in typed connectors: each runs in its own process and its output is checked before it's written."
)


@connector_app.command("list")
def list_connectors() -> None:
    """Each connector with its typed input and output ports."""
    from ananke.plexus.connectors import describe

    typer.echo(json.dumps(describe(), indent=2))


@connector_app.command("run")
def run_connector(
    name: str,
    source: Path,
    output: Path | None = typer.Option(
        None, "--output", "-o", help="Write here instead of printing"
    ),
) -> None:
    """Convert SOURCE with connector NAME, in a separate process; nothing is written unless its output checks out."""
    from ananke.plexus.connectors import ConnectorError, run

    try:
        out = run(name, source)
    except ConnectorError as exc:
        typer.echo(f"error: {exc.message} ({exc.code})", err=True)
        raise typer.Exit(2) from exc
    text = json.dumps(out, indent=2, ensure_ascii=False) + "\n"
    if output is None:
        typer.echo(text, nl=False)
    else:
        output.write_text(text, encoding="utf-8")
        typer.echo(f"wrote {output}", err=True)


@connector_app.command("register")
def register_connectors(
    project: Path = typer.Option(Path("."), "--project", help="Repository root"),
    approve: bool = typer.Option(
        False,
        "--approve",
        help="Also approve them, so workflows can use them without another review",
    ),
) -> None:
    """Add the connectors to this project's registry as tools with typed ports, for workflow assembly."""
    from ananke.plexus.connectors import register
    from ananke.plexus.registry.registry import Registry

    reg = Registry.for_project(project, create=True)
    try:
        refs = register(reg, approve=approve)
    finally:
        reg.close()
    typer.echo(json.dumps({"registered": refs, "approved": approve}, indent=2))
