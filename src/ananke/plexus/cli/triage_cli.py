"""``ananke triage``: why a build failed, from files you already have (CAP-jenkins-triage)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import typer

triage_app = typer.Typer(
    help="Why a build failed, from its log: no access to the CI server needed."
)


def _changed_since(base: str, project: Path) -> list[str]:
    argv = ["git", "-C", str(project), "diff", "--name-only", f"{base}...HEAD"]
    done = subprocess.run(argv, capture_output=True, text=True, timeout=30, check=False)  # noqa: S603 - fixed argv
    if done.returncode != 0:
        raise typer.BadParameter(f"git couldn't compare with {base!r}: {done.stderr.strip()[:200]}")
    return [line for line in done.stdout.splitlines() if line.strip()]


@triage_app.command("jenkins")
def jenkins(
    console: Path,
    jenkinsfile: Path | None = typer.Option(
        None, "--jenkinsfile", help="The pipeline's Jenkinsfile"
    ),
    changed: list[str] = typer.Option([], "--changed", help="A changed file (repeat)"),
    base: str | None = typer.Option(
        None, "--base", help="Compare with this Git ref to find what changed"
    ),
    project: Path = typer.Option(Path("."), "--project", help="Repository root, for --base"),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable report"),
    output: Path | None = typer.Option(None, "--output", "-o", help="Write the report here"),
) -> None:
    """Stages, the first primary failure and its cascade, the changes near it, and a rerun plan.

    Reads a downloaded console log and, optionally, the Jenkinsfile and what changed. Needs no Jenkins access and
    sends nothing anywhere.
    """
    from ananke.plexus.jenkins_triage import TriageError, to_markdown, triage

    try:
        text = console.read_text(encoding="utf-8", errors="replace")
        pipeline = jenkinsfile.read_text(encoding="utf-8") if jenkinsfile else ""
    except OSError as exc:
        typer.echo(f"error: {exc} (INVALID_INPUT)", err=True)
        raise typer.Exit(2) from exc
    files = list(changed) + (_changed_since(base, project) if base else [])
    try:
        report = triage(text, jenkinsfile=pipeline, changed=files, source=console.name)
    except TriageError as exc:
        typer.echo(f"error: {exc} (INVALID_INPUT)", err=True)
        raise typer.Exit(2) from exc
    out = json.dumps(report, indent=2) + "\n" if as_json else to_markdown(report)
    if output:
        output.write_text(out, encoding="utf-8")
        typer.echo(f"wrote {output}", err=True)
    else:
        typer.echo(out, nl=False)
    if report["first_failure"]:
        raise typer.Exit(1)


@triage_app.command("findings")
def findings(
    project: Path = typer.Option(Path("."), "--project", help="Repository root"),
    changed: list[str] = typer.Option([], "--changed", help="A changed file (repeat)"),
    base: str | None = typer.Option(
        None, "--base", help="Compare with this Git ref to find what changed"
    ),
    check_vulnerabilities: bool = typer.Option(
        False,
        "--check-vulnerabilities",
        help="Also look up dependencies at osv.dev. Sends package names and versions, and nothing else",
    ),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable report"),
    output: Path | None = typer.Option(None, "--output", "-o", help="Write the report here"),
) -> None:
    """What to fix first: scanner findings, failing tests, low coverage on changed files and vulnerable dependencies,
    ranked against what changed and what it affects, with the gates to rerun.

    Reads the SARIF, JUnit and coverage reports already in the repository; runs no scanner.
    """
    from ananke.plexus import intel
    from ananke.plexus.graph.service import build_graph
    from ananke.plexus.intel.priorities import prioritize, to_markdown

    root = project.resolve()
    if not root.is_dir():
        typer.echo(f"error: {project} isn't a folder (INVALID_INPUT)", err=True)
        raise typer.Exit(2)
    files = list(changed) + (_changed_since(base, root) if base else [])
    report = intel.analyze(root, check_vulnerabilities=check_vulnerabilities)
    snap = build_graph(root)
    graph = {"edges": [e.model_dump(mode="json") for e in snap.edges]}
    result = prioritize(report, graph, files)
    out = json.dumps(result, indent=2) + "\n" if as_json else to_markdown(result)
    if output:
        output.write_text(out, encoding="utf-8")
        typer.echo(f"wrote {output}", err=True)
    else:
        typer.echo(out, nl=False)


@triage_app.command("contracts")
def contracts(
    project: Path = typer.Option(Path("."), "--project", help="Repository root"),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable report"),
    output: Path | None = typer.Option(None, "--output", "-o", help="Write the report here"),
) -> None:
    """API contract gaps: routes no test calls, routes nothing calls, calls to missing routes, OpenAPI operations
    the code doesn't serve, and recommended contract tests for each untested route. Nothing is run or written."""
    from ananke.plexus import intel
    from ananke.plexus.intel.contracts import gaps, to_markdown

    root = project.resolve()
    if not root.is_dir():
        typer.echo(f"error: {project} isn't a folder (INVALID_INPUT)", err=True)
        raise typer.Exit(2)
    report = intel.analyze(root)
    api = (report.get("sections") or {}).get("api") or {}
    files = [
        p.relative_to(root).as_posix()
        for p in root.rglob("*")
        if p.is_file() and ".git" not in p.parts
    ]
    result = gaps(root, api, files)
    out = json.dumps(result, indent=2) + "\n" if as_json else to_markdown(result)
    if output:
        output.write_text(out, encoding="utf-8")
        typer.echo(f"wrote {output}", err=True)
    else:
        typer.echo(out, nl=False)


@triage_app.command("infra")
def infra(
    project: list[Path] = typer.Option(
        [Path(".")], "--project", help="A repository root (repeat for several)"
    ),
    log: Path | None = typer.Option(
        None, "--log", help="A `docker compose logs` output to triage by service"
    ),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable report"),
    output: Path | None = typer.Option(None, "--output", "-o", help="Write the report here"),
) -> None:
    """Compose services, Dockerfiles, Kubernetes manifests and Terraform resources with their links (across
    projects too), what a container log says about each service, and a safe order to check them in. Nothing is
    started, applied or contacted."""
    from ananke.plexus.intel.infra import scan, to_markdown, triage_log, validation_sequence

    roots = [p.resolve() for p in project]
    missing = [str(p) for p in roots if not p.is_dir()]
    if missing:
        typer.echo(f"error: not a folder: {', '.join(missing)} (INVALID_INPUT)", err=True)
        raise typer.Exit(2)
    found = scan(roots)
    triaged = None
    if log is not None:
        try:
            triaged = triage_log(found, log.read_text(encoding="utf-8", errors="replace"))
        except OSError as exc:
            typer.echo(f"error: {exc} (INVALID_INPUT)", err=True)
            raise typer.Exit(2) from exc
    if as_json:
        out = (
            json.dumps({**found, "log": triaged, "sequence": validation_sequence(found)}, indent=2)
            + "\n"
        )
    else:
        out = to_markdown(found, triaged)
    if output:
        output.write_text(out, encoding="utf-8")
        typer.echo(f"wrote {output}", err=True)
    else:
        typer.echo(out, nl=False)
