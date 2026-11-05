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


def _text(result: dict[str, object], as_json: bool) -> None:
    typer.echo(json.dumps(result, indent=1) if as_json else result["text"], nl=as_json)


_JSON = typer.Option(False, "--json")


@context_app.command("outline")
def outline(
    path: str,
    project: Path = _PROJECT,
    signatures: bool = typer.Option(False, "--signatures", help="Add each function's signature"),
    as_json: bool = _JSON,
) -> None:
    """What's in a file, for a fraction of the cost of reading it. Always current."""
    from ananke.plexus.context import ContextError
    from ananke.plexus.context import outline as run

    try:
        _text(run(project, path, signatures=signatures), as_json)
    except ContextError as exc:
        raise _fail(exc) from exc


@context_app.command("symbol")
def symbol(
    name: str,
    project: Path = _PROJECT,
    path: str | None = typer.Option(None, "--path", help="Only in this file"),
    as_json: bool = _JSON,
) -> None:
    """One function, method or class with its source. Always current."""
    from ananke.plexus.context import ContextError
    from ananke.plexus.context import symbol as run

    try:
        _text(run(project, name, path=path), as_json)
    except ContextError as exc:
        raise _fail(exc) from exc


@context_app.command("callers")
def callers(
    name: str,
    project: Path = _PROJECT,
    path: str | None = typer.Option(None, "--path", help="Only the symbol in this file"),
    as_json: bool = _JSON,
) -> None:
    """What calls or inherits from a symbol, and text matches the graph couldn't resolve. Always current."""
    from ananke.plexus.context import ContextError
    from ananke.plexus.context import callers as run

    try:
        _text(run(project, name, path=path), as_json)
    except ContextError as exc:
        raise _fail(exc) from exc


@context_app.command("enable")
def enable(
    project: Path = _PROJECT,
    client: list[str] = typer.Option(
        [],
        "--client",
        help="claude-code, codex, copilot, kiro, cursor, amazon-q or all (repeat). Default: detect",
    ),
    session_map: bool = typer.Option(
        True,
        "--session-map/--no-session-map",
        help="Claude Code: load the map at the start of each session",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Say what would change, and change nothing"
    ),
) -> None:
    """Make this project use the graph by default, for the assistants you use."""
    from ananke.plexus.context import ContextError
    from ananke.plexus.context import enable as run

    try:
        typer.echo(
            json.dumps(
                run(project, clients=list(client), session_map=session_map, dry_run=dry_run),
                indent=1,
            )
        )
    except ContextError as exc:
        raise _fail(exc) from exc


@context_app.command("disable")
def disable(project: Path = _PROJECT) -> None:
    """Remove exactly what enable added, for every assistant."""
    from ananke.plexus.context import ContextError
    from ananke.plexus.context import disable as run

    try:
        typer.echo(json.dumps(run(project), indent=1))
    except ContextError as exc:
        raise _fail(exc) from exc


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
