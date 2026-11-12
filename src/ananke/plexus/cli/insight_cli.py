"""Commands for the advanced views (ADV01..ADV17), added to the groups they belong to."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import typer


def _emit(
    result: dict[str, Any],
    markdown: Callable[[dict[str, Any]], str],
    as_json: bool,
    output: Path | None,
) -> None:
    text = json.dumps(result, indent=2) + "\n" if as_json else markdown(result)
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text, encoding="utf-8")
        typer.echo(f"wrote {output}", err=True)
    else:
        typer.echo(text, nl=False)


def _fail(exc: Exception) -> None:
    typer.echo(f"error: {exc} (INVALID_INPUT)", err=True)
    raise typer.Exit(2) from exc


_JSON = typer.Option(False, "--json", help="Machine-readable report")
_OUT = typer.Option(None, "--output", "-o", help="Write the report here")
_PROJECT = typer.Option(Path("."), "--project", help="Repository root")


def graph_diff(
    base: str = typer.Option(..., "--base", help="The earlier revision (a commit, tag or branch)"),
    head: str | None = typer.Option(
        None, "--head", help="The later revision; default: the working tree"
    ),
    project: Path = _PROJECT,
    as_json: bool = _JSON,
    output: Path | None = _OUT,
) -> None:
    """Architecture time machine: the code graph at two revisions and exactly which relationships changed, between
    components too. Past revisions are read with git archive; the working tree isn't touched."""
    from ananke.plexus.insight import InsightError
    from ananke.plexus.insight.timemachine import diff, to_markdown

    try:
        result = diff(project, base, head)
    except InsightError as exc:
        _fail(exc)
        return
    _emit(result, to_markdown, as_json, output)


def graph_whatif(
    change: list[str] = typer.Option(
        [], "--change", help="A file, symbol or node id that would change (repeat)"
    ),
    depth: int = typer.Option(3, "--depth", help="How many dependency steps to follow (1-8)"),
    both: bool = typer.Option(
        False,
        "--both",
        help="Follow what the change depends on too (measured best at --depth 1; see --backtest)",
    ),
    backtest_commits: int = typer.Option(
        0,
        "--backtest",
        help="Instead, replay this many recent commits and measure how well it predicts",
    ),
    project: Path = _PROJECT,
    as_json: bool = _JSON,
    output: Path | None = _OUT,
) -> None:
    """What a change would reach before it's made: impacted code with the chain and confidence, tests to run, routes
    and tables. Everything is labelled simulated. --backtest measures it on this repository's own history."""
    from ananke.plexus.insight import InsightError
    from ananke.plexus.insight.whatif import backtest, run, to_markdown

    try:
        if backtest_commits:
            result = backtest(project, backtest_commits, depth)
            typer.echo(json.dumps(result, indent=2) if as_json else _backtest_text(result))
            return
        result = run(project, change, depth, both=both)
    except InsightError as exc:
        _fail(exc)
        return
    _emit(result, to_markdown, as_json, output)


def _backtest_text(r: dict[str, Any]) -> str:
    return (
        f"{r['commits']} commits replayed at depth {r['depth']}.\n"
        f"  dependants only:   recall {r['recall']}, precision {r['precision']}\n"
        f"  both directions:   recall {r['both_directions_recall']}, precision {r['both_directions_precision']}\n"
        f"  same-folder baseline: recall {r['baseline_recall']}, precision {r['baseline_precision']}"
    )


def triage_test_gaps(
    coverage: Path | None = typer.Option(
        None, "--coverage", help="coverage.xml (Cobertura) or coverage.json, with branches"
    ),
    junit: Path | None = typer.Option(None, "--junit", help="JUnit XML from a test run"),
    base: str | None = typer.Option(
        None, "--base", help="Contracts whose handlers changed since this revision"
    ),
    write: Path | None = typer.Option(
        None, "--write", help="Write the pytest skeletons to this file"
    ),
    project: Path = _PROJECT,
    as_json: bool = _JSON,
    output: Path | None = _OUT,
) -> None:
    """Tests to write, built from missing branches, observed failures and changed contracts; each cites its evidence
    and fails until written. Nothing is run."""
    from ananke.plexus.insight import InsightError
    from ananke.plexus.insight.testgaps import pytest_file, synthesize, to_markdown

    try:
        result = synthesize(project, coverage=coverage, junit=junit, base=base)
    except (InsightError, OSError) as exc:
        _fail(exc)
        return
    if write is not None:
        write.parent.mkdir(parents=True, exist_ok=True)
        write.write_text(pytest_file(result), encoding="utf-8")
        typer.echo(
            f"wrote {write}: {len(result['tests'])} tests, each failing until written", err=True
        )
    _emit(result, to_markdown, as_json, output)


def triage_notebooks(
    notebook: list[Path] = typer.Option(
        [], "--notebook", help="A notebook to inspect (repeat); default: all"
    ),
    project: Path = _PROJECT,
    as_json: bool = _JSON,
    output: Path | None = _OUT,
) -> None:
    """Would each notebook give the same results run top to bottom? Execution order, cells never run, hidden runs,
    names used before they're defined, error outputs, undeclared imports and missing inputs. Nothing runs."""
    from ananke.plexus.insight import InsightError
    from ananke.plexus.insight.notebooks import inspect_all, to_markdown

    try:
        result = inspect_all(project, notebook or None)
    except (InsightError, OSError) as exc:
        _fail(exc)
        return
    _emit(result, to_markdown, as_json, output)


def context_observe(
    cases: Path = typer.Argument(
        ..., help='Questions: [{"question": ..., "expect": ["symbol", ...]}]'
    ),
    budgets: str = typer.Option(
        "500,1000,2000,4000", "--budgets", help="Token budgets to measure, comma-separated"
    ),
    save: bool = typer.Option(
        False, "--record", help="Append this run to the history and compare with the last one"
    ),
    history: Path | None = typer.Option(
        None, "--history", help="History file (default .ananke/context/observatory.jsonl)"
    ),
    project: Path = _PROJECT,
    as_json: bool = _JSON,
    output: Path | None = _OUT,
) -> None:
    """How much graph context is worth its tokens: recall and cost at several budgets against reading whole files,
    the smallest budget with the best recall, questions nothing finds, and regressions since the last recorded run."""
    from ananke.plexus.insight import InsightError
    from ananke.plexus.insight.budget import compare, load_cases, observe, record, to_markdown

    try:
        wanted = tuple(int(b) for b in budgets.split(",") if b.strip())
        result = observe(project, load_cases(cases), wanted)
    except ValueError as exc:
        _fail(exc if isinstance(exc, InsightError) else InsightError(f"--budgets: {exc}"))
        return
    if save:
        path, previous = record(project.resolve(), result, history)
        result["regressions"] = compare(previous, result) if previous else []
        typer.echo(f"recorded in {path}", err=True)
    _emit(result, to_markdown, as_json, output)


def triage_drift(
    snapshot: list[Path] = typer.Option(
        [],
        "--snapshot",
        help="kubectl -o json, docker inspect, docker compose ps --format json or terraform state",
    ),
    openapi: str | None = typer.Option(
        None, "--openapi", help="The running service's OpenAPI: a file, or a URL on this machine"
    ),
    project: Path = _PROJECT,
    as_json: bool = _JSON,
    output: Path | None = _OUT,
    fail: bool = typer.Option(False, "--fail-on-drift", help="Exit 1 when anything drifted"),
) -> None:
    """Drift between what the repository declares and what is running: services, images, replicas, ports,
    environment variable names (never values), Terraform resources and API operations."""
    from ananke.plexus.insight import InsightError
    from ananke.plexus.insight.drift import compare, to_markdown

    try:
        result = compare(project, snapshot, openapi)
    except InsightError as exc:
        _fail(exc)
        return
    _emit(result, to_markdown, as_json, output)
    if fail and result["drift"]:
        raise typer.Exit(1)


def triage_patch(
    patch: Path = typer.Argument(..., help="A unified diff (git diff > fix.patch)"),
    base: str = typer.Option("HEAD", "--base", help="The revision the patch applies to"),
    test_command: str | None = typer.Option(
        None, "--test-command", help="Tests to run; default: the tests it reaches"
    ),
    run_tests: bool = typer.Option(
        False, "--run-tests", help="Run the tests in the throwaway worktree"
    ),
    approve: str | None = typer.Option(
        None, "--approve", help="The approval digest a previous run printed"
    ),
    branch: str | None = typer.Option(
        None, "--branch", help="With --approve: commit to this new local branch"
    ),
    message: str = typer.Option(
        "Apply reviewed patch", "--message", "-m", help="Commit message for --branch"
    ),
    project: Path = _PROJECT,
    as_json: bool = _JSON,
    output: Path | None = _OUT,
) -> None:
    """Proposed patch → tests → risk delta → approval → a branch for a pull request. Everything happens in a
    throwaway git worktree; your working tree is never touched. Tests run only with --run-tests."""
    from ananke.plexus.insight import InsightError
    from ananke.plexus.insight.patchloop import commit_branch, evaluate, to_markdown

    try:
        result = evaluate(project, patch, base=base, test_command=test_command, run_tests=run_tests)
        if approve is not None or branch is not None:
            if not (approve and branch):
                raise InsightError("--approve and --branch go together")
            sha = commit_branch(project, patch, result, approve, branch, message)
            result["committed"] = {"branch": branch, "commit": sha}
            typer.echo(
                f"committed {sha[:12]} on {branch}; push it and open the pull request when ready",
                err=True,
            )
    except InsightError as exc:
        _fail(exc)
        return
    _emit(result, to_markdown, as_json, output)
    if not result.get("applies"):
        raise typer.Exit(1)


def triage_mesh(
    project: list[Path] = typer.Option(
        [], "--project", help="A repository (repeat); default: the workspace's"
    ),
    home: Path = typer.Option(Path("."), "--home", help="Where the workspace file is"),
    as_json: bool = _JSON,
    output: Path | None = _OUT,
) -> None:
    """Every contract that ties repositories together: HTTP calls to routes, event topics (publishers and
    subscribers), datasets (writers and readers) and API schemas, with the gaps between them. Only files are read."""
    from ananke.plexus.insight import InsightError
    from ananke.plexus.insight.mesh import build, to_markdown
    from ananke.plexus.intel.topology import load_workspace

    ws = load_workspace(home.resolve())
    pinned = {r["path"]: r for r in ws["roots"]}
    roots = (
        [
            pinned.get(
                str(p.resolve()), {"path": str(p.resolve()), "name": p.resolve().name, "pin": None}
            )
            for p in project
        ]
        if project
        else ws["roots"]
    )
    try:
        missing = [r["path"] for r in roots if not Path(r["path"]).is_dir()]
        if missing:
            raise InsightError(f"not a folder: {', '.join(missing)}")
        result = build(roots)
    except InsightError as exc:
        _fail(exc)
        return
    _emit(result, to_markdown, as_json, output)


def arch_decide(
    decision: Path = typer.Argument(
        ..., help="Decision file (YAML or JSON): title, context, criteria, options"
    ),
    project: Path = _PROJECT,
    as_json: bool = _JSON,
    output: Path | None = _OUT,
) -> None:
    """An ADR whose alternatives carry measured evidence (reach, tests, hotspots, dependencies), each labelled
    observed, simulated or assumed, ranked by stated weights. The decision stays with people: status proposed."""
    from ananke.plexus.insight import InsightError
    from ananke.plexus.insight.decide import assess, load, to_markdown

    try:
        result = assess(project, load(decision))
    except InsightError as exc:
        _fail(exc)
        return
    _emit(result, to_markdown, as_json, output)


def triage_incident(
    console: Path = typer.Argument(..., help="The failed build's console log"),
    last_good: str | None = typer.Option(
        None, "--last-good", help="The last revision that built; ranks suspects"
    ),
    service_logs: Path | None = typer.Option(
        None, "--service-logs", help="docker compose logs captured then"
    ),
    jenkinsfile: Path | None = typer.Option(
        None, "--jenkinsfile", help="The pipeline's Jenkinsfile"
    ),
    commit: str | None = typer.Option(
        None, "--commit", help="The revision built, when the log doesn't say"
    ),
    project: Path | None = typer.Option(None, "--project", help="The repository the build ran"),
    as_json: bool = _JSON,
    output: Path | None = _OUT,
) -> None:
    """From a failed build to its cause: the commit, suspect commits since the last good build, the stage and step,
    the containers and services, and their logs, each link observed or a hypothesis. Nothing is re-run."""
    from ananke.plexus.insight import InsightError
    from ananke.plexus.insight.incident import investigate, to_markdown

    try:
        result = investigate(
            console,
            project.resolve() if project else None,
            last_good=last_good,
            service_logs=service_logs,
            jenkinsfile=jenkinsfile,
            commit=commit,
        )
    except (InsightError, OSError) as exc:
        _fail(exc)
        return
    _emit(result, to_markdown, as_json, output)


def graph_twin(
    snapshot: list[Path] = typer.Option(
        [], "--snapshot", help="A runtime snapshot (see triage drift); repeat"
    ),
    openapi: str | None = typer.Option(
        None, "--openapi", help="A running service's OpenAPI (file or local URL)"
    ),
    project: Path = _PROJECT,
    as_json: bool = _JSON,
    output: Path | None = _OUT,
) -> None:
    """One model of the system per component: source, contract, infrastructure, verification and runtime, each
    fact observed, inferred or unknown, with stale reports flagged."""
    from ananke.plexus.insight import InsightError
    from ananke.plexus.insight.twin import build, to_markdown

    try:
        result = build(project, snapshots=snapshot, openapi=openapi)
    except InsightError as exc:
        _fail(exc)
        return
    _emit(result, to_markdown, as_json, output)


cve_app = typer.Typer(
    help="Vulnerability data kept on this machine, so CVE checks need no network."
)


@cve_app.command("import")
def cve_import(
    source: Path = typer.Argument(
        ..., help="An OSV export (e.g. PyPI/all.zip) or a folder of OSV JSON files"
    ),
    db: Path | None = typer.Option(
        None, "--db", help="Database file; default ~/.cache/ananke/osv.sqlite"
    ),
) -> None:
    """Import OSV advisories into a local database. Then set ANANKE_OSV_DB to it, and --check-vulnerabilities
    answers offline."""
    from ananke.plexus.intel.osv_db import OsvDbError, import_export

    target = db or Path.home() / ".cache" / "ananke" / "osv.sqlite"
    try:
        info = import_export(source, target)
    except (OsvDbError, OSError) as exc:
        _fail(exc)
        return
    typer.echo(json.dumps(info, indent=2))
    typer.echo(
        f"export ANANKE_OSV_DB={target}  # then --check-vulnerabilities works offline", err=True
    )


@cve_app.command("status")
def cve_status(
    db: Path | None = typer.Option(None, "--db", help="Database file; default: ANANKE_OSV_DB"),
) -> None:
    """What the local vulnerability database holds and when it was imported."""
    import os

    from ananke.plexus.intel.osv_db import status

    target = db or Path(
        os.environ.get("ANANKE_OSV_DB") or Path.home() / ".cache" / "ananke" / "osv.sqlite"
    )
    typer.echo(json.dumps(status(target), indent=2))


def register(
    graph_app: typer.Typer, triage_app: typer.Typer, context_app: typer.Typer, arch_app: typer.Typer
) -> None:
    graph_app.command("diff")(graph_diff)
    graph_app.command("whatif")(graph_whatif)
    triage_app.command("test-gaps")(triage_test_gaps)
    triage_app.command("notebooks")(triage_notebooks)
    context_app.command("observe")(context_observe)
    triage_app.command("drift")(triage_drift)
    triage_app.command("patch")(triage_patch)
    triage_app.command("mesh")(triage_mesh)
    arch_app.command("decide")(arch_decide)
    triage_app.command("incident")(triage_incident)
    graph_app.command("twin")(graph_twin)
