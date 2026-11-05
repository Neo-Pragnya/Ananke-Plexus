"""``ananke process``: business processes from reviewed text, as a model and as BPMN."""

from __future__ import annotations

import json
from pathlib import Path

import typer

process_app = typer.Typer(
    help="Business processes from reviewed text: a model, and BPMN 2.0. Nothing runs."
)


@process_app.command("extract")
def extract(
    document: Path,
    bpmn: Path | None = typer.Option(None, "--bpmn", help="Also write BPMN 2.0 XML here"),
) -> None:
    """The process a Markdown document states, with what doesn't hold together."""
    from ananke.plexus.process import ProcessError, to_bpmn
    from ananke.plexus.process import extract as run

    try:
        model = run(document.read_text(encoding="utf-8"), document.name)
    except (ProcessError, OSError, UnicodeDecodeError) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(2) from exc
    if bpmn is not None:
        bpmn.write_text(to_bpmn(model), encoding="utf-8")
    typer.echo(json.dumps(model, indent=1))
    if model["problems"]:
        raise typer.Exit(1)


@process_app.command("check")
def check(
    file: Path,
    against: Path | None = typer.Option(None, "--against", help="A document it should match"),
) -> None:
    """Check BPMN XML: IDs, links and reachability, and that it still says what a document says."""
    from ananke.plexus.process import ProcessError, from_bpmn, same_process
    from ananke.plexus.process import extract as read_text

    try:
        model = from_bpmn(file.read_text(encoding="utf-8"))
        problems = list(model["problems"])
        if against is not None:
            problems += same_process(
                read_text(against.read_text(encoding="utf-8"), against.name), model
            )
    except (ProcessError, OSError, UnicodeDecodeError) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(2) from exc
    typer.echo(
        json.dumps(
            {
                "ok": not problems,
                "problems": problems,
                "nodes": len(model["nodes"]),
                "flows": len(model["flows"]),
            },
            indent=1,
        )
    )
    if problems:
        raise typer.Exit(1)
