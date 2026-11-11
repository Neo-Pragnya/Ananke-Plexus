"""``ananke learning``: capture a lesson, evaluate it, review it, promote it (see ananke/plexus/learning.py)."""

from __future__ import annotations

import json
from pathlib import Path

import typer

learning_app = typer.Typer(
    help="Learning candidates: capture → evaluate → review → promote. Nothing publishes on its own.",
    no_args_is_help=True,
)
_WS = typer.Option(Path("."), "--workspace", "-w", help="Workspace root")


def _folder(workspace: Path, name: str) -> Path:
    from ananke.plexus.learning import candidates_dir

    folder = candidates_dir(workspace) / name
    if not folder.is_dir():
        typer.echo(f"no candidate {name!r}; see `ananke learning list`", err=True)
        raise typer.Exit(2)
    return folder


def _fail(exc: Exception) -> None:
    typer.echo(str(exc), err=True)
    raise typer.Exit(1) from exc


@learning_app.command("capture")
def capture_cmd(
    name: str,
    description: str = typer.Option(
        ..., "--description", help="What the lesson does and when to use it"
    ),
    body_file: Path = typer.Option(
        ..., "--body", help="Markdown file with the lesson's instructions"
    ),
    case: list[str] = typer.Option(..., "--case", help="id:positive|regression:command (repeat)"),
    run: list[str] = typer.Option([], "--run", help="Run ids the lesson came from"),
    public: bool = typer.Option(
        False, "--public", help="Allow publishing to public sources (default: internal)"
    ),
    workspace: Path = _WS,
) -> None:
    """Write a redacted candidate under .ananke/learning/candidates/."""
    from ananke.plexus.learning import LearningError, capture

    cases = []
    for c in case:
        cid, _, rest = c.partition(":")
        kind, _, command = rest.partition(":")
        cases.append({"id": cid, "kind": kind, "command": command})
    try:
        folder = capture(
            workspace,
            name,
            description=description,
            body=body_file.read_text(encoding="utf-8"),
            cases=cases,
            runs=list(run),
            visibility="public" if public else "internal",
        )
    except (LearningError, OSError) as exc:
        _fail(exc)
        return
    typer.echo(f"captured {folder}")


@learning_app.command("evaluate")
def evaluate_cmd(
    name: str,
    allow_commands: bool = typer.Option(
        False, "--allow-commands", help="Agree to run the cases' commands"
    ),
    workspace: Path = _WS,
) -> None:
    """Run the evaluation cases (each must exit 0)."""
    from ananke.plexus.learning import LearningError, evaluate

    try:
        ev = evaluate(_folder(workspace, name), workspace, allow_commands=allow_commands)
    except LearningError as exc:
        _fail(exc)
        return
    for c in ev["cases"]:
        typer.echo(f"{'✓' if c['passed'] else '✗'} {c['id']} ({c['kind']})")


@learning_app.command("review")
def review_cmd(
    name: str,
    reviewer: str = typer.Option(..., "--reviewer"),
    reason: str = typer.Option(..., "--reason"),
    approve: bool = typer.Option(False, "--approve/--reject"),
    workspace: Path = _WS,
) -> None:
    """Record a review decision."""
    from ananke.plexus.learning import LearningError, review

    try:
        d = review(_folder(workspace, name), approve=approve, reviewer=reviewer, reason=reason)
    except LearningError as exc:
        _fail(exc)
        return
    typer.echo(f"{d['decision']} by {d['reviewer']}")


@learning_app.command("promote")
def promote_cmd(
    name: str,
    to: str = typer.Option(..., "--to", help="A writable capability source id"),
    approved_by: str = typer.Option(..., "--approved-by"),
    workspace: Path = _WS,
) -> None:
    """Publish an approved, passing candidate into a writable source."""
    from ananke.plexus.learning import LearningError, promote
    from ananke.plexus.registry.providers import SourceError, from_project

    try:
        receipt = promote(
            _folder(workspace, name), from_project(workspace.resolve()), to, approved_by=approved_by
        )
    except (LearningError, SourceError) as exc:
        _fail(exc)
        return
    typer.echo(json.dumps(receipt, indent=2))


@learning_app.command("list")
def list_cmd(workspace: Path = _WS) -> None:
    """Candidates and what still blocks each one."""
    from ananke.plexus.learning import candidates_dir, gate

    root = candidates_dir(workspace)
    found = sorted(p for p in root.iterdir() if p.is_dir()) if root.is_dir() else []
    for folder in found:
        problems = gate(folder)
        typer.echo(
            f"{folder.name:<32} {'ready to promote' if not problems else '; '.join(problems)}"
        )
    if not found:
        typer.echo("no candidates")


@learning_app.command("draft")
def draft_cmd(
    base: str = typer.Option(
        ..., "--base", help="Where the session started: a branch, tag or commit"
    ),
    run: list[str] = typer.Option(
        [], "--run", help="An Ananke run from the session, for its warnings (repeat)"
    ),
    title: str = typer.Option(
        "", "--title", help="The lesson's title (default: the first commit's subject)"
    ),
    output: Path = typer.Option(Path("lesson.md"), "--output", "-o", help="Write the draft here"),
    workspace: Path = _WS,
) -> None:
    """A lesson drafted from a session: what changed, the decisions in its commit messages, the tests that failed
    and its runs' warnings. Names and messages only, never file contents. Finish it, then capture it."""
    from ananke.plexus.app import AppFacade
    from ananke.plexus.learning_draft import draft

    if output.exists():
        typer.echo(f"{output} already exists; choose another --output", err=True)
        raise typer.Exit(2)
    runs = []
    for run_id in run:
        try:
            runs.append(AppFacade(workspace).get_run(run_id))
        except Exception as exc:  # an unknown run is the user's input, not a crash
            typer.echo(f"no run {run_id!r}: {exc}", err=True)
            raise typer.Exit(2) from exc
    try:
        out = draft(workspace, base, runs=runs, title=title)
    except ValueError as exc:
        typer.echo(f"{exc}", err=True)
        raise typer.Exit(2) from exc
    output.write_text(out["markdown"], encoding="utf-8")
    typer.echo(
        f"wrote {output}: {len(out['commits'])} commits, {len(out['decisions'])} decisions, "
        f"{len(out['failed_tests'])} failed tests. Finish it, check `ananke learning overlap {output}`, then capture it."
    )


@learning_app.command("overlap")
def overlap_cmd(lesson: Path, as_json: bool = typer.Option(False, "--json")) -> None:
    """Does Dex Trove already have this lesson? Items scored by the words they share with it."""
    from ananke.plexus.learning_draft import overlap

    try:
        text = lesson.read_text(encoding="utf-8")
    except OSError as exc:
        typer.echo(f"{exc}", err=True)
        raise typer.Exit(2) from exc
    out = overlap(text)
    if as_json:
        typer.echo(json.dumps(out, indent=2))
        return
    if not out["checked"]:
        typer.echo(f"not checked: {out['reason']}")
        return
    if not out["similar"]:
        typer.echo("nothing similar in Dex Trove")
        return
    for s in out["similar"]:
        typer.echo(
            f"{s['score']:.2f}  {s['id']}  {s['title']}  (shares {', '.join(s['shared'][:6])})"
        )
