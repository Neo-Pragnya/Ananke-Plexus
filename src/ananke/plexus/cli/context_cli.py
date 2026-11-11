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
        False,
        "--session-map/--no-session-map",
        help="Claude Code: also load the map at the start of every session, which costs its tokens each time",
    ),
    local: bool = typer.Option(
        False,
        "--local",
        help="Keep it to yourself: nothing goes in a commit, and tracked files are left alone",
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
                run(
                    project,
                    clients=list(client),
                    session_map=session_map,
                    local=local,
                    dry_run=dry_run,
                ),
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


@context_app.command("ask")
def ask(
    question: str,
    budget: int = typer.Option(2000, "--budget", help="Most tokens to return"),
    project: Path = _PROJECT,
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """The code a question is about, within a token budget: the best matching symbols with their source, then what
    calls them and what they call. Kept for this snapshot of the repository, so asking again is free."""
    from ananke.plexus.retrieval import ask as run
    from ananke.plexus.retrieval import to_text

    if not 100 <= budget <= 50_000:
        typer.echo("budget must be between 100 and 50000", err=True)
        raise typer.Exit(2)
    out = run(project, question, budget=budget)
    typer.echo(json.dumps(out, indent=1) if as_json else to_text(out), nl=not as_json)
    if not out["plan"]:
        raise typer.Exit(1)


@context_app.command("eval")
def evaluate(
    cases: Path,
    budget: int = typer.Option(2000, "--budget", help="Token budget per question"),
    project: Path = _PROJECT,
) -> None:
    """Recall and token cost on a list of questions ([{"question": ..., "expect": ["symbol", ...]}]), against
    reading every file that contains one of the question's words."""
    from ananke.plexus.retrieval import evaluate as run

    try:
        data = json.loads(cases.read_text(encoding="utf-8"))
        if not isinstance(data, list) or not all(
            isinstance(c, dict) and c.get("question") for c in data
        ):
            raise ValueError("not a list of questions")
    except (OSError, ValueError) as exc:
        typer.echo(
            f'{cases}: expected a JSON list of {{"question": ..., "expect": [...]}} ({exc})',
            err=True,
        )
        raise typer.Exit(2) from exc
    typer.echo(json.dumps(run(project, data, budget=budget), indent=1))
