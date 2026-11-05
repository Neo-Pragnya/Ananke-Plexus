"""``ananke context``: spend fewer tokens on a repository."""

from __future__ import annotations

import json
from pathlib import Path

import typer

context_app = typer.Typer(
    help="Spend fewer tokens on a repository: a small map, and the right tool for each question"
)
_PROJECT = typer.Option(Path("."), "--project", help="Repository root")


def _fail(exc: Exception) -> typer.Exit:
    typer.echo(f"error: {exc}", err=True)
    return typer.Exit(2)


@context_app.command("map")
def map_(
    project: Path = _PROJECT,
    budget: int = typer.Option(1500, "--budget", help="Most tokens the map may use"),
    refresh: bool = typer.Option(
        False, "--refresh", help="Rebuild even if the sources haven't changed"
    ),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """A small picture of the repository, rebuilt only when its sources change."""
    from ananke.plexus.context import ContextError, repo_map

    try:
        result = repo_map(project, budget=budget, refresh=refresh)
    except ContextError as exc:
        raise _fail(exc) from exc
    typer.echo(json.dumps(result, indent=1) if as_json else result["map"])


@context_app.command("status")
def status(project: Path = _PROJECT) -> None:
    """Which tools are ready, what each is for, and how to get the missing ones."""
    from ananke.plexus.context import status as read

    typer.echo(json.dumps(read(project), indent=1))


@context_app.command("pack")
def pack(
    include: list[str] = typer.Option(
        ..., "--include", help="Files to pack, such as 'src/**/*.py' (repeat)"
    ),
    project: Path = _PROJECT,
    compress: bool = typer.Option(True, "--compress/--no-compress"),
    output: Path | None = typer.Option(None, "--output"),
) -> None:
    """Pack chosen files with repomix, compressed."""
    from ananke.plexus.context import ContextError
    from ananke.plexus.context import pack as run

    try:
        typer.echo(
            json.dumps(
                run(project, include=list(include), compress=compress, output=output), indent=1
            )
        )
    except ContextError as exc:
        raise _fail(exc) from exc


@context_app.command("benchmark")
def benchmark(project: Path = _PROJECT) -> None:
    """What a first look costs here, each way."""
    from ananke.plexus.context import benchmark as run

    typer.echo(json.dumps(run(project), indent=1))
