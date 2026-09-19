"""Main Typer CLI entrypoint."""

import json
from datetime import UTC
from pathlib import Path

import typer

from ananke.plexus import __version__
from ananke.plexus.api import Ananke
from ananke.plexus.cli.rendering import render_result
from ananke.plexus.mcp.http import serve_http
from ananke.plexus.mcp.server import serve_stdio
from ananke.plexus.registry.cli import (
    agent_app,
    registry_app,
    skill_app,
    sync_command,
)
from ananke.plexus.specs.models import Requirement

app = typer.Typer(help="Ananke Plexus ADLC control plane")

spec_app = typer.Typer(help="Specification operations")
graph_app = typer.Typer(help="Graph topology operations")
arch_app = typer.Typer(help="Architecture operations")
run_app = typer.Typer(help="Autonomous run operations")
plugin_app = typer.Typer(help="Plugin discovery operations")
lifecycle_app = typer.Typer(help="Lifecycle operations")
configure_app = typer.Typer(help="Configuration operations")
config_app = typer.Typer(help="Config migration operations")
evidence_app = typer.Typer(help="Evidence retention operations")
policy_app = typer.Typer(help="Policy operations")
hooks_app = typer.Typer(help="Git hook management")
backend_app = typer.Typer(help="Agent backend operations")
bmad_app = typer.Typer(help="Ananke BMAD contract operations")
eval_app = typer.Typer(help="Agent evaluation harness")
eval_suite_app = typer.Typer(help="Evaluation suite operations")
eval_dataset_app = typer.Typer(help="Evaluation dataset operations")
eval_baseline_app = typer.Typer(help="Evaluation baseline operations")
eval_trace_app = typer.Typer(help="Trace operations")
eval_adapter_app = typer.Typer(help="Eval adapter management")
eval_judge_app = typer.Typer(help="Judge management")
app.add_typer(spec_app, name="spec")
app.add_typer(graph_app, name="graph")
app.add_typer(arch_app, name="arch")
app.add_typer(run_app, name="run")
app.add_typer(plugin_app, name="plugin")
app.add_typer(lifecycle_app, name="lifecycle")
app.add_typer(configure_app, name="configure")
app.add_typer(config_app, name="config")
app.add_typer(evidence_app, name="evidence")
app.add_typer(policy_app, name="policy")
app.add_typer(hooks_app, name="hooks")
app.add_typer(backend_app, name="backend")
app.add_typer(bmad_app, name="bmad")
app.add_typer(eval_app, name="eval")
app.add_typer(registry_app, name="registry")
app.add_typer(skill_app, name="skill")
app.add_typer(agent_app, name="agent")
app.command("sync")(sync_command)
eval_app.add_typer(eval_suite_app, name="suite")
eval_app.add_typer(eval_dataset_app, name="dataset")
eval_app.add_typer(eval_baseline_app, name="baseline")
eval_app.add_typer(eval_trace_app, name="trace")
eval_app.add_typer(eval_adapter_app, name="adapter")
eval_app.add_typer(eval_judge_app, name="judge")


@app.command("version")
def version() -> None:
    """Print package version."""
    typer.echo(__version__)


@app.command("init")
def init(project: Path = typer.Option(Path("."), "--project", help="Repository root")) -> None:
    result = Ananke.open(project).init_project()
    render_result(result.summary, result.details)


@app.command("doctor")
def doctor(
    project: Path = typer.Option(Path("."), "--project", help="Repository root"),
    as_json: bool = typer.Option(False, "--json", help="Render machine-readable JSON output"),
) -> None:
    result = Ananke.open(project).doctor()
    if as_json:
        typer.echo(json.dumps(result.model_dump(), indent=2))
        return
    render_result(result.summary, result.details)


@app.command("verify")
def verify(project: Path = typer.Option(Path("."), "--project", help="Repository root")) -> None:
    result = Ananke.open(project).verify()
    render_result(result.summary, result.details)


@app.command("serve-mcp")
def serve_mcp(
    project: Path = typer.Option(Path("."), "--project", help="Repository root"),
    transport: str = typer.Option("stdio", "--transport", help="stdio or http"),
    host: str = typer.Option("127.0.0.1", "--host", help="HTTP host"),
    port: int = typer.Option(8765, "--port", help="HTTP port"),
    token: str = typer.Option("", "--token", help="Optional bearer token for HTTP transport"),
    allow_mutations: bool = typer.Option(
        False, "--allow-mutations", help="Enable mutation tools (H4)"
    ),
) -> None:
    """Serve MCP-compatible server with read-only or mutation-enabled surface."""
    if transport == "http":
        serve_http(project, host, port, auth_token=token or None)
        return
    serve_stdio(project, allow_mutations=allow_mutations)


@spec_app.command("create")
def spec_create(
    requirement_id: str = typer.Option(..., "--id", help="Requirement ID, e.g. DEMO-101"),
    title: str = typer.Option(..., "--title", help="Requirement title"),
    body: str = typer.Option("", "--body", help="Requirement body text"),
    acceptance: list[str] = typer.Option(
        [],
        "--acceptance",
        help="Acceptance criterion (repeat for multiple)",
    ),
    provider: str = typer.Option(
        "",
        "--provider",
        help="Spec provider (speckit or native). If omitted, uses config.",
    ),
    project: Path = typer.Option(Path("."), "--project", help="Repository root"),
) -> None:
    requirement = Requirement(
        requirement_id=requirement_id,
        title=title,
        body=body or title,
        acceptance_criteria=acceptance,
    )
    result = Ananke.open(project).create_spec(requirement, provider_name=provider or None)
    render_result(result.summary, result.details)


@spec_app.command("plan")
def spec_plan(
    feature_dir: Path = typer.Option(..., "--feature-dir", help="Path to a spec feature directory"),
    provider: str = typer.Option(
        "",
        "--provider",
        help="Spec provider (speckit or native). If omitted, uses config.",
    ),
    project: Path = typer.Option(Path("."), "--project", help="Repository root"),
) -> None:
    result = Ananke.open(project).plan(feature_dir, provider_name=provider or None)
    render_result(result.summary, result.details)


@spec_app.command("tasks")
def spec_tasks(
    feature_dir: Path = typer.Option(..., "--feature-dir", help="Path to a spec feature directory"),
    provider: str = typer.Option(
        "",
        "--provider",
        help="Spec provider (speckit or native). If omitted, uses config.",
    ),
    project: Path = typer.Option(Path("."), "--project", help="Repository root"),
) -> None:
    result = Ananke.open(project).tasks(feature_dir, provider_name=provider or None)
    render_result(result.summary, result.details)


@spec_app.command("lock")
def spec_lock(
    feature_dir: Path = typer.Option(..., "--feature-dir", help="Path to a spec feature directory"),
    project: Path = typer.Option(Path("."), "--project", help="Repository root"),
) -> None:
    result = Ananke.open(project).lock(feature_dir)
    render_result(result.summary, result.details)


@spec_app.command("validate")
def spec_validate(
    feature_dir: Path = typer.Option(..., "--feature-dir", help="Path to a spec feature directory"),
    project: Path = typer.Option(Path("."), "--project", help="Repository root"),
) -> None:
    result = Ananke.open(project).validate(feature_dir)
    render_result(result.summary, result.details)
    if not result.ok:
        raise typer.Exit(code=4)


@spec_app.command("diff")
def spec_diff(
    feature_dir: Path = typer.Option(..., "--feature-dir", help="Path to a spec feature directory"),
    project: Path = typer.Option(Path("."), "--project", help="Repository root"),
) -> None:
    result = Ananke.open(project).diff(feature_dir)
    render_result(result.summary, result.details)
    if not result.ok:
        raise typer.Exit(code=8)


@spec_app.command("converge")
def spec_converge(
    requirement_id: str = typer.Option(..., "--id", help="Requirement ID, e.g. DEMO-101"),
    title: str = typer.Option(..., "--title", help="Requirement title"),
    body: str = typer.Option("", "--body", help="Requirement body text"),
    acceptance: list[str] = typer.Option(
        [],
        "--acceptance",
        help="Acceptance criterion (repeat for multiple)",
    ),
    provider: str = typer.Option(
        "",
        "--provider",
        help="Spec provider (speckit or native). If omitted, uses config.",
    ),
    project: Path = typer.Option(Path("."), "--project", help="Repository root"),
) -> None:
    requirement = Requirement(
        requirement_id=requirement_id,
        title=title,
        body=body or title,
        acceptance_criteria=acceptance,
    )
    result = Ananke.open(project).converge(requirement, provider_name=provider or None)
    render_result(result.summary, result.details)


@spec_app.command("superpowers")
def spec_superpowers(
    requirement_id: str = typer.Option(..., "--id", help="Requirement ID, e.g. DEMO-101"),
    title: str = typer.Option(..., "--title", help="Requirement title"),
    body: str = typer.Option("", "--body", help="Requirement body text"),
    acceptance: list[str] = typer.Option(
        [],
        "--acceptance",
        help="Acceptance criterion (repeat for multiple)",
    ),
    provider: str = typer.Option(
        "",
        "--provider",
        help="Spec provider (speckit or native). If omitted, uses config.",
    ),
    project: Path = typer.Option(Path("."), "--project", help="Repository root"),
) -> None:
    """Run create+plan+tasks+BMAD+lock+drift-check in one command."""
    requirement = Requirement(
        requirement_id=requirement_id,
        title=title,
        body=body or title,
        acceptance_criteria=acceptance,
    )
    result = Ananke.open(project).converge(requirement, provider_name=provider or None)
    render_result(result.summary, result.details)


@graph_app.command("build")
def graph_build(
    project: Path = typer.Option(Path("."), "--project", help="Repository root"),
) -> None:
    result = Ananke.open(project).graph_build()
    render_result(result.summary, result.details)


@graph_app.command("query")
def graph_query(
    text: str = typer.Option(..., "--text", help="Query text"),
    project: Path = typer.Option(Path("."), "--project", help="Repository root"),
) -> None:
    result = Ananke.open(project).graph_query(text)
    render_result(result.summary, result.details)


@graph_app.command("impact")
def graph_impact(
    changed_file: list[str] = typer.Option(
        [],
        "--changed-file",
        help="Changed file path (repeatable)",
    ),
    project: Path = typer.Option(Path("."), "--project", help="Repository root"),
) -> None:
    result = Ananke.open(project).graph_impact(changed_file)
    render_result(result.summary, result.details)


@graph_app.command("export")
def graph_export(
    fmt: str = typer.Option("json", "--format", help="json or markdown"),
    project: Path = typer.Option(Path("."), "--project", help="Repository root"),
) -> None:
    result = Ananke.open(project).graph_export(fmt)
    render_result(result.summary, result.details)


@arch_app.command("init")
def arch_init(
    project: Path = typer.Option(Path("."), "--project", help="Repository root"),
) -> None:
    result = Ananke.open(project).arch_init()
    render_result(result.summary, result.details)


@arch_app.command("validate")
def arch_validate(
    project: Path = typer.Option(Path("."), "--project", help="Repository root"),
) -> None:
    result = Ananke.open(project).arch_validate()
    render_result(result.summary, result.details)
    if not result.ok:
        raise typer.Exit(code=5)


@arch_app.command("diff")
def arch_diff(
    project: Path = typer.Option(Path("."), "--project", help="Repository root"),
) -> None:
    result = Ananke.open(project).arch_diff()
    render_result(result.summary, result.details)


@arch_app.command("reconcile")
def arch_reconcile(
    apply: bool = typer.Option(False, "--apply", help="Write missing graph components into CALM"),
    project: Path = typer.Option(Path("."), "--project", help="Repository root"),
) -> None:
    result = Ananke.open(project).arch_reconcile(apply=apply)
    render_result(result.summary, result.details)


@arch_app.command("render")
def arch_render(
    project: Path = typer.Option(Path("."), "--project", help="Repository root"),
) -> None:
    result = Ananke.open(project).arch_render()
    render_result(result.summary, result.details)


@run_app.command("start")
def run_start(
    spec_id: str = typer.Option(..., "--spec-id", help="Spec identifier"),
    project: Path = typer.Option(Path("."), "--project", help="Repository root"),
) -> None:
    result = Ananke.open(project).run_start(spec_id)
    render_result(result.summary, result.details)


@run_app.command("status")
def run_status(
    run_id: str = typer.Option(..., "--run-id", help="Run identifier"),
    project: Path = typer.Option(Path("."), "--project", help="Repository root"),
) -> None:
    result = Ananke.open(project).run_status(run_id)
    render_result(result.summary, result.details)
    if not result.ok:
        raise typer.Exit(code=1)


@run_app.command("cancel")
def run_cancel(
    run_id: str = typer.Option(..., "--run-id", help="Run identifier"),
    project: Path = typer.Option(Path("."), "--project", help="Repository root"),
) -> None:
    result = Ananke.open(project).run_cancel(run_id)
    render_result(result.summary, result.details)
    if not result.ok:
        raise typer.Exit(code=10)


@run_app.command("replay")
def run_replay(
    run_id: str = typer.Option(..., "--run-id", help="Run identifier"),
    project: Path = typer.Option(Path("."), "--project", help="Repository root"),
) -> None:
    result = Ananke.open(project).run_replay(run_id)
    render_result(result.summary, result.details)
    if not result.ok:
        raise typer.Exit(code=1)


@run_app.command("approvals")
def run_approvals(
    run_id: str = typer.Option(..., "--run-id", help="Run identifier"),
    project: Path = typer.Option(Path("."), "--project", help="Repository root"),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output"),
) -> None:
    """List pending and resolved approvals for a run."""
    from ananke.plexus.execution.approvals import list_approvals

    reqs = list_approvals(project, run_id)
    if as_json:
        import json

        typer.echo(json.dumps([r.model_dump() for r in reqs], indent=2))
        return
    details: dict[str, object] = {
        r.approval_id[:8]: f"{r.approval_class} | {r.status}" for r in reqs
    }
    render_result(f"approvals for {run_id}: {len(reqs)}", details)


@run_app.command("approve")
def run_approve(
    run_id: str = typer.Option(..., "--run-id", help="Run identifier"),
    approval_id: str = typer.Option(..., "--approval-id", help="Approval ID to resolve"),
    deny: bool = typer.Option(False, "--deny", help="Deny instead of grant"),
    notes: str = typer.Option("", "--notes", help="Optional notes"),
    project: Path = typer.Option(Path("."), "--project", help="Repository root"),
) -> None:
    """Grant or deny a pending approval for a run step."""
    from ananke.plexus.execution.approvals import resolve_approval

    result = resolve_approval(project, run_id, approval_id, grant=not deny, notes=notes)
    if result is None:
        render_result(f"approval {approval_id[:8]} not found or already resolved", {})
        raise typer.Exit(code=1)
    render_result(
        f"approval {approval_id[:8]}: {result.status}",
        {"step": result.step_id, "class": result.approval_class},
    )


@plugin_app.command("list")
def plugin_list(
    project: Path = typer.Option(Path("."), "--project", help="Repository root"),
) -> None:
    result = Ananke.open(project).plugin_list()
    render_result(result.summary, result.details)


@lifecycle_app.command("branch")
def lifecycle_branch(
    change_type: str = typer.Option("feature", "--type", help="Change type, e.g. feature"),
    ticket: str = typer.Option(..., "--ticket", help="Ticket key, e.g. PROJ-101"),
    slug: str = typer.Option(..., "--slug", help="Short branch slug"),
    project: Path = typer.Option(Path("."), "--project", help="Repository root"),
) -> None:
    result = Ananke.open(project).lifecycle_branch(change_type, ticket, slug)
    render_result(result.summary, result.details)


@lifecycle_app.command("issue-transition")
def lifecycle_issue_transition(
    issue_key: str = typer.Option(..., "--issue", help="Issue key"),
    target_state: str = typer.Option(..., "--to", help="Target issue state"),
    idempotency_key: str = typer.Option(..., "--idempotency-key", help="Idempotency key"),
    mode: str = typer.Option("auto", "--mode", help="local|auto|remote-dry-run|remote-live"),
    project: Path = typer.Option(Path("."), "--project", help="Repository root"),
) -> None:
    result = Ananke.open(project).lifecycle_transition_issue(
        issue_key,
        target_state,
        idempotency_key,
        mode,
    )
    render_result(result.summary, result.details)


@lifecycle_app.command("pr-create")
def lifecycle_pr_create(
    title: str = typer.Option(..., "--title", help="PR title"),
    branch: str = typer.Option(..., "--branch", help="Source branch"),
    idempotency_key: str = typer.Option(..., "--idempotency-key", help="Idempotency key"),
    mode: str = typer.Option("auto", "--mode", help="local|auto|remote-dry-run|remote-live"),
    project: Path = typer.Option(Path("."), "--project", help="Repository root"),
) -> None:
    result = Ananke.open(project).lifecycle_create_pr(
        title,
        branch,
        idempotency_key,
        mode,
    )
    render_result(result.summary, result.details)


@lifecycle_app.command("worktrees")
def lifecycle_worktrees(
    project: Path = typer.Option(Path("."), "--project", help="Repository root"),
) -> None:
    result = Ananke.open(project).lifecycle_worktrees()
    render_result(result.summary, result.details)


@lifecycle_app.command("telemetry")
def lifecycle_telemetry(
    project: Path = typer.Option(Path("."), "--project", help="Repository root"),
) -> None:
    result = Ananke.open(project).lifecycle_telemetry()
    render_result(result.summary, result.details)


@lifecycle_app.command("evidence")
def lifecycle_evidence(
    service: str = typer.Option("", "--service", help="Filter by service name"),
    status: str = typer.Option("", "--status", help="Filter by event status"),
    severity: str = typer.Option(
        "",
        "--severity",
        help="Filter by severity: info|warning|critical",
    ),
    limit: int = typer.Option(20, "--limit", min=1, help="Max events to show"),
    since_hours: float = typer.Option(
        0.0,
        "--since-hours",
        min=0.0,
        help="Only include events from the last N hours",
    ),
    output_format: str = typer.Option("compact", "--format", help="compact|json|csv"),
    trend: str = typer.Option(
        "",
        "--trend",
        help="Group over time buckets: hour|day",
    ),
    aggregate: bool = typer.Option(
        False,
        "--aggregate",
        help="Group output by service and status",
    ),
    csv_path: str = typer.Option(
        "",
        "--csv-path",
        help="Optional output path when --format csv is used",
    ),
    project: Path = typer.Option(Path("."), "--project", help="Repository root"),
) -> None:
    result = Ananke.open(project).lifecycle_evidence(
        service=service or None,
        status=status or None,
        severity=severity or None,
        limit=limit,
        since_hours=since_hours if since_hours > 0 else None,
        output_format=output_format,
        aggregate=aggregate,
        trend=trend or None,
        csv_path=csv_path or None,
    )
    render_result(result.summary, result.details)


@lifecycle_app.command("confluence-upsert")
def lifecycle_confluence_upsert(
    space_key: str = typer.Option(..., "--space", help="Confluence space key"),
    title: str = typer.Option(..., "--title", help="Confluence page title"),
    content_path: str = typer.Option(..., "--content", help="Path to page content file"),
    idempotency_key: str = typer.Option(..., "--idempotency-key", help="Idempotency key"),
    mode: str = typer.Option("auto", "--mode", help="local|auto|remote-dry-run|remote-live"),
    project: Path = typer.Option(Path("."), "--project", help="Repository root"),
) -> None:
    result = Ananke.open(project).lifecycle_confluence_upsert(
        space_key,
        title,
        content_path,
        idempotency_key,
        mode,
    )
    render_result(result.summary, result.details)


@configure_app.command("auth")
def configure_auth(
    project: Path = typer.Option(Path("."), "--project", help="Repository root"),
    remote_live_enabled: str = typer.Option(
        "",
        "--remote-live-enabled",
        help="Enable remote-live mode: true|false",
    ),
    remote_live_services: str = typer.Option(
        "",
        "--remote-live-services",
        help="Comma-separated services, e.g. jira,bitbucket,confluence",
    ),
    jira_base_url: str = typer.Option("", "--jira-base-url", help="Jira base URL"),
    jira_email: str = typer.Option("", "--jira-email", help="Jira user email"),
    jira_token: str = typer.Option("", "--jira-token", help="Jira API token"),
    jira_bearer_token: str = typer.Option(
        "",
        "--jira-bearer-token",
        help="Jira bearer token",
    ),
    jira_bearer_token_cmd: str = typer.Option(
        "",
        "--jira-bearer-token-cmd",
        help="Command to fetch Jira bearer token",
    ),
    jira_token_cmd: str = typer.Option("", "--jira-token-cmd", help="Command to fetch Jira token"),
    confluence_base_url: str = typer.Option(
        "",
        "--confluence-base-url",
        help="Confluence base URL",
    ),
    confluence_email: str = typer.Option(
        "",
        "--confluence-email",
        help="Confluence user email",
    ),
    confluence_token: str = typer.Option(
        "",
        "--confluence-token",
        help="Confluence API token",
    ),
    confluence_bearer_token: str = typer.Option(
        "",
        "--confluence-bearer-token",
        help="Confluence bearer token",
    ),
    confluence_bearer_token_cmd: str = typer.Option(
        "",
        "--confluence-bearer-token-cmd",
        help="Command to fetch Confluence bearer token",
    ),
    bitbucket_base_url: str = typer.Option(
        "",
        "--bitbucket-base-url",
        help="Bitbucket API base URL",
    ),
    bitbucket_workspace: str = typer.Option(
        "",
        "--bitbucket-workspace",
        help="Bitbucket workspace slug",
    ),
    bitbucket_repo_slug: str = typer.Option(
        "",
        "--bitbucket-repo",
        help="Bitbucket repository slug",
    ),
    bitbucket_dest_branch: str = typer.Option(
        "",
        "--bitbucket-destination",
        help="Bitbucket destination branch for PR",
    ),
    bitbucket_username: str = typer.Option(
        "",
        "--bitbucket-username",
        help="Bitbucket username",
    ),
    bitbucket_app_password: str = typer.Option(
        "",
        "--bitbucket-app-password",
        help="Bitbucket app password",
    ),
    bitbucket_bearer_token: str = typer.Option(
        "",
        "--bitbucket-bearer-token",
        help="Bitbucket bearer token",
    ),
    bitbucket_bearer_token_cmd: str = typer.Option(
        "",
        "--bitbucket-bearer-token-cmd",
        help="Command to fetch Bitbucket bearer token",
    ),
    bitbucket_app_password_cmd: str = typer.Option(
        "",
        "--bitbucket-app-password-cmd",
        help="Command to fetch Bitbucket app password",
    ),
    mcp_http_token: str = typer.Option("", "--mcp-http-token", help="MCP HTTP bearer token"),
    mcp_http_token_cmd: str = typer.Option(
        "",
        "--mcp-http-token-cmd",
        help="Command to fetch MCP HTTP token",
    ),
    confluence_token_cmd: str = typer.Option(
        "",
        "--confluence-token-cmd",
        help="Command to fetch Confluence token",
    ),
) -> None:
    result = Ananke.open(project).configure_auth(
        remote_live_enabled=remote_live_enabled or None,
        remote_live_services=remote_live_services or None,
        jira_base_url=jira_base_url or None,
        jira_email=jira_email or None,
        jira_token=jira_token or None,
        jira_bearer_token=jira_bearer_token or None,
        jira_bearer_token_cmd=jira_bearer_token_cmd or None,
        jira_token_cmd=jira_token_cmd or None,
        confluence_base_url=confluence_base_url or None,
        confluence_email=confluence_email or None,
        confluence_token=confluence_token or None,
        confluence_bearer_token=confluence_bearer_token or None,
        confluence_bearer_token_cmd=confluence_bearer_token_cmd or None,
        confluence_token_cmd=confluence_token_cmd or None,
        bitbucket_base_url=bitbucket_base_url or None,
        bitbucket_workspace=bitbucket_workspace or None,
        bitbucket_repo_slug=bitbucket_repo_slug or None,
        bitbucket_dest_branch=bitbucket_dest_branch or None,
        bitbucket_username=bitbucket_username or None,
        bitbucket_app_password=bitbucket_app_password or None,
        bitbucket_bearer_token=bitbucket_bearer_token or None,
        bitbucket_bearer_token_cmd=bitbucket_bearer_token_cmd or None,
        bitbucket_app_password_cmd=bitbucket_app_password_cmd or None,
        mcp_http_token=mcp_http_token or None,
        mcp_http_token_cmd=mcp_http_token_cmd or None,
    )
    render_result(result.summary, result.details)


@configure_app.command("auth-export")
def configure_auth_export(
    project: Path = typer.Option(Path("."), "--project", help="Repository root"),
    shell: str = typer.Option(
        "zsh",
        "--shell",
        help="zsh|bash|sh|fish|pwsh|cmd|docker-env",
    ),
) -> None:
    result = Ananke.open(project).configure_auth_export(shell)
    render_result(result.summary, result.details)


@configure_app.command("auth-validate")
def configure_auth_validate(
    project: Path = typer.Option(Path("."), "--project", help="Repository root"),
) -> None:
    result = Ananke.open(project).configure_auth_validate()
    render_result(result.summary, result.details)
    if not result.ok:
        raise typer.Exit(code=2)


@configure_app.command("auth-interactive")
def configure_auth_interactive(
    project: Path = typer.Option(Path("."), "--project", help="Repository root"),
) -> None:
    app_api = Ananke.open(project)
    current = app_api.configure_auth_values().details

    remote_live_enabled = typer.prompt(
        "Remote-live enabled (true/false)",
        default=str(current.get("ANANKE_REMOTE_LIVE_ENABLED", "false")),
    )
    remote_live_services = typer.prompt(
        "Remote-live services (comma-separated)",
        default=str(current.get("ANANKE_REMOTE_LIVE_SERVICES", "jira,confluence,bitbucket")),
    )

    jira_base_url = typer.prompt("Jira base URL", default=str(current["ANANKE_JIRA_BASE_URL"]))
    jira_email = typer.prompt("Jira email", default=str(current["ANANKE_JIRA_EMAIL"]))
    jira_token = typer.prompt(
        "Jira API token (leave blank to keep current)",
        default="",
        hide_input=True,
        show_default=False,
    )
    jira_bearer_token = typer.prompt(
        "Jira bearer token (leave blank to keep current)",
        default="",
        hide_input=True,
        show_default=False,
    )

    confluence_base_url = typer.prompt(
        "Confluence base URL",
        default=str(current["ANANKE_CONFLUENCE_BASE_URL"]),
    )
    confluence_email = typer.prompt(
        "Confluence email",
        default=str(current["ANANKE_CONFLUENCE_EMAIL"]),
    )
    confluence_token = typer.prompt(
        "Confluence API token (leave blank to keep current)",
        default="",
        hide_input=True,
        show_default=False,
    )
    confluence_bearer_token = typer.prompt(
        "Confluence bearer token (leave blank to keep current)",
        default="",
        hide_input=True,
        show_default=False,
    )

    bitbucket_base_url = typer.prompt(
        "Bitbucket base URL",
        default=str(current["ANANKE_BITBUCKET_BASE_URL"]),
    )
    bitbucket_workspace = typer.prompt(
        "Bitbucket workspace",
        default=str(current["ANANKE_BITBUCKET_WORKSPACE"]),
    )
    bitbucket_repo_slug = typer.prompt(
        "Bitbucket repo slug",
        default=str(current["ANANKE_BITBUCKET_REPO_SLUG"]),
    )
    bitbucket_dest_branch = typer.prompt(
        "Bitbucket destination branch",
        default=str(current["ANANKE_BITBUCKET_DEST_BRANCH"]),
    )
    bitbucket_username = typer.prompt(
        "Bitbucket username",
        default=str(current["ANANKE_BITBUCKET_USERNAME"]),
    )
    bitbucket_app_password = typer.prompt(
        "Bitbucket app password (leave blank to keep current)",
        default="",
        hide_input=True,
        show_default=False,
    )
    bitbucket_bearer_token = typer.prompt(
        "Bitbucket bearer token (leave blank to keep current)",
        default="",
        hide_input=True,
        show_default=False,
    )

    mcp_http_token = typer.prompt(
        "MCP HTTP token (leave blank to keep current)",
        default="",
        hide_input=True,
        show_default=False,
    )
    jira_token_cmd = typer.prompt(
        "Jira token command (optional)",
        default=str(current.get("ANANKE_JIRA_TOKEN_CMD", "")),
    )
    jira_bearer_token_cmd = typer.prompt(
        "Jira bearer token command (optional)",
        default=str(current.get("ANANKE_JIRA_BEARER_TOKEN_CMD", "")),
    )
    confluence_token_cmd = typer.prompt(
        "Confluence token command (optional)",
        default=str(current.get("ANANKE_CONFLUENCE_TOKEN_CMD", "")),
    )
    confluence_bearer_token_cmd = typer.prompt(
        "Confluence bearer token command (optional)",
        default=str(current.get("ANANKE_CONFLUENCE_BEARER_TOKEN_CMD", "")),
    )
    bitbucket_app_password_cmd = typer.prompt(
        "Bitbucket app password command (optional)",
        default=str(current.get("ANANKE_BITBUCKET_APP_PASSWORD_CMD", "")),
    )
    bitbucket_bearer_token_cmd = typer.prompt(
        "Bitbucket bearer token command (optional)",
        default=str(current.get("ANANKE_BITBUCKET_BEARER_TOKEN_CMD", "")),
    )
    mcp_http_token_cmd = typer.prompt(
        "MCP HTTP token command (optional)",
        default=str(current.get("ANANKE_MCP_HTTP_TOKEN_CMD", "")),
    )

    result = app_api.configure_auth(
        remote_live_enabled=remote_live_enabled,
        remote_live_services=remote_live_services,
        jira_base_url=jira_base_url,
        jira_email=jira_email,
        jira_token=jira_token or None,
        jira_bearer_token=jira_bearer_token or None,
        jira_bearer_token_cmd=jira_bearer_token_cmd or None,
        jira_token_cmd=jira_token_cmd or None,
        confluence_base_url=confluence_base_url,
        confluence_email=confluence_email,
        confluence_token=confluence_token or None,
        confluence_bearer_token=confluence_bearer_token or None,
        confluence_bearer_token_cmd=confluence_bearer_token_cmd or None,
        confluence_token_cmd=confluence_token_cmd or None,
        bitbucket_base_url=bitbucket_base_url,
        bitbucket_workspace=bitbucket_workspace,
        bitbucket_repo_slug=bitbucket_repo_slug,
        bitbucket_dest_branch=bitbucket_dest_branch,
        bitbucket_username=bitbucket_username,
        bitbucket_app_password=bitbucket_app_password or None,
        bitbucket_bearer_token=bitbucket_bearer_token or None,
        bitbucket_bearer_token_cmd=bitbucket_bearer_token_cmd or None,
        bitbucket_app_password_cmd=bitbucket_app_password_cmd or None,
        mcp_http_token=mcp_http_token or None,
        mcp_http_token_cmd=mcp_http_token_cmd or None,
    )
    render_result(result.summary, result.details)


@config_app.command("migrate")
def config_migrate(
    apply: bool = typer.Option(
        False,
        "--apply",
        help="Apply migration changes (default is analysis only)",
    ),
    project: Path = typer.Option(Path("."), "--project", help="Repository root"),
) -> None:
    result = Ananke.open(project).config_migrate(apply=apply)
    render_result(result.summary, result.details)
    if not result.ok:
        raise typer.Exit(code=2)


@evidence_app.command("prune")
def evidence_prune(
    older_than: str = typer.Option("30d", "--older-than", help="Age threshold, e.g. 30d, 12h"),
    apply: bool = typer.Option(False, "--apply", help="Delete prunable evidence bundles"),
    project: Path = typer.Option(Path("."), "--project", help="Repository root"),
) -> None:
    result = Ananke.open(project).evidence_prune(older_than=older_than, apply=apply)
    render_result(result.summary, result.details)
    if not result.ok:
        raise typer.Exit(code=2)


@policy_app.command("explain")
def policy_explain(
    stage: str = typer.Option("verify", "--stage", help="Policy stage to evaluate"),
    project: Path = typer.Option(Path("."), "--project", help="Repository root"),
) -> None:
    result = Ananke.open(project).policy_explain(stage=stage)
    render_result(result.summary, result.details)


@policy_app.command("list")
def policy_list(
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output"),
    project: Path = typer.Option(Path("."), "--project", help="Repository root"),
) -> None:
    """List active policy rules for this project."""
    from ananke.plexus.policy.packs import load_project_packs

    rules = load_project_packs(project)
    if as_json:
        import json

        typer.echo(json.dumps([r.model_dump(by_alias=True) for r in rules], indent=2))
        return
    details: dict[str, object] = {r.rule_id: f"{r.stage} | {r.severity}" for r in rules}
    render_result(f"policy rules: {len(rules)}", details)


@policy_app.command("check")
def policy_check(
    stage: str = typer.Option("verify", "--stage", help="Stage to evaluate"),
    project: Path = typer.Option(Path("."), "--project", help="Repository root"),
) -> None:
    """Run policy evaluation for a stage and exit 3 on block."""
    result = Ananke.open(project).policy_explain(stage=stage)
    blocked = result.details.get("blocked_count", 0)
    render_result(result.summary, result.details)
    if blocked:
        raise typer.Exit(code=3)


@policy_app.command("install-pack")
def policy_install_pack(
    name: str = typer.Argument(
        ..., help="Pack name: baseline, python-library, agentic-security, enterprise-strict"
    ),
    project: Path = typer.Option(Path("."), "--project", help="Repository root"),
) -> None:
    """Install a builtin policy pack into the project policy directory."""
    from ananke.plexus.policy.packs import install_pack, list_builtin_packs

    try:
        dest = install_pack(project, name)
        render_result(f"pack installed: {name}", {"path": str(dest)})
    except ValueError:
        render_result(f"unknown pack: {name}", {"available": ", ".join(list_builtin_packs())})
        raise typer.Exit(code=2) from None


# ---------------------------------------------------------------------------
# hooks commands (Epic G)
# ---------------------------------------------------------------------------


@hooks_app.command("install")
def hooks_install(
    stage: str = typer.Argument(
        "all", help="Hook stage: pre-commit, post-commit, pre-push, or all"
    ),
    chain: bool = typer.Option(False, "--chain", help="Chain existing hook instead of refusing"),
    mode: str = typer.Option(
        "native", "--mode", help="Hook mode: native, pre-commit-framework, delegated"
    ),
    delegate_cmd: str = typer.Option("", "--delegate-cmd", help="Command for delegated mode"),
    project: Path = typer.Option(Path("."), "--project", help="Repository root"),
) -> None:
    """Install Ananke-managed git hooks."""
    from ananke.plexus.hooks.manager import (
        HookStage,
        install_delegated,
        install_hook,
        install_pre_commit_framework,
    )

    if mode == "pre-commit-framework":
        result = install_pre_commit_framework(project)
        render_result("hooks install (pre-commit-framework)", result)
        return

    if mode == "delegated":
        if not delegate_cmd:
            render_result("--delegate-cmd required for delegated mode", {})
            raise typer.Exit(code=2)
        stages_d: list[HookStage] = (
            ["pre-commit", "post-commit", "pre-push"] if stage == "all" else [stage]  # type: ignore[list-item]
        )
        results_d = [install_delegated(project, s, delegate_cmd) for s in stages_d]
        details_d: dict[str, object] = {
            r.stage: "installed" if r.installed else "failed" for r in results_d
        }
        render_result("hooks install (delegated)", details_d)
        return

    stages: list[HookStage] = (
        ["pre-commit", "post-commit", "pre-push"] if stage == "all" else [stage]  # type: ignore[list-item]
    )
    results = [install_hook(project, s, chain=chain) for s in stages]
    details: dict[str, object] = {
        r.stage: "installed" if r.installed else "skipped (unmanaged hook exists)" for r in results
    }
    render_result(
        f"hooks install: {sum(r.installed for r in results)}/{len(results)} installed", details
    )


@hooks_app.command("uninstall")
def hooks_uninstall(
    stage: str = typer.Argument("all", help="Hook stage or 'all'"),
    project: Path = typer.Option(Path("."), "--project", help="Repository root"),
) -> None:
    """Remove Ananke-managed git hooks and restore any backups."""
    from ananke.plexus.hooks.manager import HookStage, uninstall_hook

    stages: list[HookStage] = (
        ["pre-commit", "post-commit", "pre-push"] if stage == "all" else [stage]  # type: ignore[list-item]
    )
    results = [uninstall_hook(project, s) for s in stages]
    details: dict[str, object] = {
        r.stage: "removed" if not r.installed else "unchanged" for r in results
    }
    render_result("hooks uninstall complete", details)


@hooks_app.command("status")
def hooks_status(
    project: Path = typer.Option(Path("."), "--project", help="Repository root"),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output"),
) -> None:
    """Show current hook installation status for all stages."""
    from ananke.plexus.hooks.manager import all_hook_statuses

    statuses = all_hook_statuses(project)
    if as_json:
        import json

        typer.echo(json.dumps([s.model_dump() for s in statuses], indent=2))
        return
    details: dict[str, object] = {
        s.stage: f"{'managed' if s.managed else 'unmanaged'} | {'installed' if s.installed else 'not installed'}"
        for s in statuses
    }
    render_result("hook status", details)


@hooks_app.command("run")
def hooks_run(
    stage: str = typer.Argument(..., help="Hook stage: pre-commit, post-commit, pre-push"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show individual check results"),
    project: Path = typer.Option(Path("."), "--project", help="Repository root"),
) -> None:
    """Execute the checks for a given hook stage."""
    from ananke.plexus.hooks.runner import dispatch

    result = dispatch(project, stage, verbose=verbose)  # type: ignore[arg-type]
    render_result(result.summary, {"ok": result.ok, "checks": len(result.checks)})
    if not result.ok:
        raise typer.Exit(code=1)


# ---------------------------------------------------------------------------
# backend commands (Epic K)
# ---------------------------------------------------------------------------


@backend_app.command("list")
def backend_list(
    project: Path = typer.Option(Path("."), "--project", help="Repository root"),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output"),
) -> None:
    """List registered agent backends."""
    from ananke.plexus.backends.registry import BackendRegistry

    reg = BackendRegistry.load(project)
    if as_json:
        import json

        typer.echo(json.dumps(reg.to_list(), indent=2))
        return
    entries = reg.to_list()
    details: dict[str, object] = {e["name"]: e["status"] for e in entries}
    render_result(f"backends: {len(entries)} registered", details)


@backend_app.command("doctor")
def backend_doctor(
    name: str = typer.Argument(..., help="Backend name to diagnose"),
    project: Path = typer.Option(Path("."), "--project", help="Repository root"),
) -> None:
    """Run availability check for a named backend."""
    from ananke.plexus.backends.registry import BackendRegistry

    reg = BackendRegistry.load(project)
    result = reg.doctor(name)
    render_result(result["summary"], result)


@backend_app.command("test")
def backend_test(
    name: str = typer.Argument(..., help="Backend name to test"),
    project: Path = typer.Option(Path("."), "--project", help="Repository root"),
) -> None:
    """Send a ping task to a backend and report capability."""
    from ananke.plexus.backends.registry import BackendRegistry

    reg = BackendRegistry.load(project)
    result = reg.test(name)
    render_result(result["summary"], result)
    if not result.get("ok"):
        raise typer.Exit(code=1)


# ---------------------------------------------------------------------------
# bmad commands (Epic D5-D7)
# ---------------------------------------------------------------------------


@bmad_app.command("compile")
def bmad_compile(
    feature_dir: Path = typer.Option(..., "--feature-dir", help="Feature spec directory"),
    project: Path = typer.Option(Path("."), "--project", help="Repository root"),
) -> None:
    """Compile full BMAD contracts (behavior + model + architecture) for a feature."""
    from ananke.plexus.contracts.bmad import compile_bmad
    from ananke.plexus.specs.service import load_requirement_from_dir

    req = load_requirement_from_dir(feature_dir)
    if req is None:
        render_result("no requirement found in feature dir", {"path": str(feature_dir)})
        raise typer.Exit(code=2)
    bmad_path = compile_bmad(feature_dir, req)
    render_result("BMAD compiled", {"bmad": str(bmad_path)})


@bmad_app.command("show")
def bmad_show(
    feature_dir: Path = typer.Option(..., "--feature-dir", help="Feature spec directory"),
) -> None:
    """Show the compiled BMAD contract for a feature."""
    bmad_path = feature_dir / "bmad.yaml"
    if not bmad_path.exists():
        render_result("no bmad.yaml found — run `ananke bmad compile` first", {})
        raise typer.Exit(code=2)
    typer.echo(bmad_path.read_text(encoding="utf-8"))


@bmad_app.command("trace")
def bmad_trace(
    feature_dir: Path = typer.Option(..., "--feature-dir", help="Feature spec directory"),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output"),
) -> None:
    """Show requirement → AC → test traceability for a feature."""
    import json

    from ananke.plexus.specs.service import load_requirement_from_dir

    req = load_requirement_from_dir(feature_dir)
    if req is None:
        render_result("no requirement found", {})
        raise typer.Exit(code=2)
    trace = {f"AC-{i}": crit for i, crit in enumerate(req.acceptance_criteria, start=1)}
    if as_json:
        typer.echo(json.dumps({"requirement_id": req.requirement_id, "trace": trace}, indent=2))
        return
    render_result(f"trace for {req.requirement_id}", trace)


@bmad_app.command("verify")
def bmad_verify(
    feature_dir: Path = typer.Option(..., "--feature-dir", help="Feature spec directory"),
) -> None:
    """Check that all BMAD contract files are present."""
    required_files = ["bmad.yaml", "model-contract.yaml", "architecture-contract.yaml"]
    missing = [f for f in required_files if not (feature_dir / f).exists()]
    tests_dir = feature_dir / "tests"
    has_tests = tests_dir.exists() and any(tests_dir.glob("test_*.py"))
    if not has_tests:
        missing.append("tests/test_*_behavior.py")
    ok = len(missing) == 0
    render_result(
        "BMAD verify: ok" if ok else f"BMAD verify: missing {len(missing)} artifacts",
        {"missing": ", ".join(missing) if missing else "none"},
    )
    if not ok:
        raise typer.Exit(code=4)


# ─── Eval CLI ───────────────────────────────────────────────────────────────


@eval_app.command("run")
def eval_run(
    suite: str = typer.Option("standard", "--suite", help="Eval suite id"),
    case_id: str = typer.Option("", "--case", help="Specific case id"),
    trace_path: str = typer.Option("", "--trace", help="Path to recorded trace JSON"),
    output_value: str = typer.Option("", "--output", help="Agent output for quick evaluation"),
    project: Path = typer.Option(Path("."), "--project", help="Project root"),
    as_json: bool = typer.Option(False, "--json", help="JSON output"),
) -> None:
    """Run an evaluation suite against a case or recorded trace."""

    from ananke.plexus.evals.api import evaluate_trace, run_evaluation
    from ananke.plexus.evals.models.case import EvalCase
    from ananke.plexus.evals.models.suite import EvalSuite, GatePolicy

    eval_suite = EvalSuite(id=suite, policy=GatePolicy())
    eval_case = EvalCase(id=case_id or "cli-case", input=output_value or "(no input)")

    if trace_path:
        from pathlib import Path as P

        report = evaluate_trace(
            suite=eval_suite,
            trace_path=P(trace_path),
            case=eval_case,
            project_root=project,
            evaluators=[],
        )
    else:
        report = run_evaluation(
            suite=eval_suite,
            case=eval_case,
            output=output_value or None,
            project_root=project,
            evaluators=[],
            save_evidence=True,
        )

    if as_json:
        typer.echo(report.model_dump_json(indent=2))
        return

    from ananke.plexus.evals.reports.console import generate_console_report

    typer.echo(generate_console_report(report))
    if report.gate_decision and report.gate_decision.blocks:
        raise typer.Exit(code=1)


@eval_app.command("report")
def eval_report(
    run_id: str = typer.Option(..., "--run", help="Run ID to report on"),
    fmt: str = typer.Option(
        "console", "--format", help="Output format: console|markdown|json|junit"
    ),
    project: Path = typer.Option(Path("."), "--project", help="Project root"),
) -> None:
    """Generate an evaluation report for a completed run."""
    import json as _json

    evidence_root = project / ".ananke" / "evidence"
    scores_file = evidence_root / run_id / "eval" / "scores.json"
    if not scores_file.exists():
        typer.echo(f"No eval evidence for run '{run_id}'")
        raise typer.Exit(code=2)

    from ananke.plexus.evals.models.report import EvalReport
    from ananke.plexus.evals.models.score import EvalScore

    scores_raw = _json.loads(scores_file.read_text(encoding="utf-8"))
    scores = [EvalScore(**s) for s in scores_raw]
    report = EvalReport(run_id=run_id, suite_id="unknown", scores=scores)

    if fmt == "markdown":
        from ananke.plexus.evals.reports.markdown import generate_markdown_report

        typer.echo(generate_markdown_report(report))
    elif fmt == "json":
        typer.echo(report.model_dump_json(indent=2))
    elif fmt == "junit":
        from ananke.plexus.evals.reports.junit import generate_junit_xml

        typer.echo(generate_junit_xml(report))
    else:
        from ananke.plexus.evals.reports.console import generate_console_report

        typer.echo(generate_console_report(report))


@eval_app.command("compare")
def eval_compare(
    baseline_id: str = typer.Option(..., "--baseline", help="Baseline ID"),
    run_id: str = typer.Option(..., "--run", help="Candidate run ID"),
    project: Path = typer.Option(Path("."), "--project", help="Project root"),
) -> None:
    """Compare a candidate eval run against an approved baseline."""
    import json as _json

    baselines_dir = project / ".ananke" / "evals" / "baselines"
    baseline_file = baselines_dir / f"{baseline_id}.json"
    if not baseline_file.exists():
        typer.echo(f"Baseline '{baseline_id}' not found in {baselines_dir}")
        raise typer.Exit(code=2)

    evidence_root = project / ".ananke" / "evidence"
    scores_file = evidence_root / run_id / "eval" / "scores.json"
    if not scores_file.exists():
        typer.echo(f"No eval evidence for run '{run_id}'")
        raise typer.Exit(code=2)

    from ananke.plexus.evals.models.baseline import Baseline
    from ananke.plexus.evals.models.report import EvalReport
    from ananke.plexus.evals.models.score import EvalScore
    from ananke.plexus.evals.regression.compare import compare_to_baseline

    baseline = Baseline(**_json.loads(baseline_file.read_text(encoding="utf-8")))
    scores = [EvalScore(**s) for s in _json.loads(scores_file.read_text(encoding="utf-8"))]
    report = EvalReport(run_id=run_id, suite_id="unknown", scores=scores)
    comparison = compare_to_baseline(report, baseline)

    render_result(
        f"regression comparison: {run_id} vs {baseline_id}",
        {
            "regressions": comparison.regressions,
            "improvements": comparison.improvements,
            "stable": comparison.stable,
        },
    )
    if comparison.regression_blocked:
        raise typer.Exit(code=1)


# ─── Eval Suite sub-commands ─────────────────────────────────────────────────


@eval_suite_app.command("list")
def eval_suite_list(
    project: Path = typer.Option(Path("."), "--project", help="Project root"),
    as_json: bool = typer.Option(False, "--json", help="JSON output"),
) -> None:
    """List available evaluation suites."""
    suites_dir = project / ".ananke" / "evals" / "suites"
    if not suites_dir.exists():
        render_result("no suites directory", {"path": str(suites_dir)})
        return
    files = (
        list(suites_dir.glob("*.yaml"))
        + list(suites_dir.glob("*.yml"))
        + list(suites_dir.glob("*.json"))
    )
    names = [f.stem for f in sorted(files)]
    if as_json:
        typer.echo(json.dumps({"suites": names}, indent=2))
        return
    render_result(f"{len(names)} eval suite(s)", {"suites": ", ".join(names) or "none"})


@eval_suite_app.command("validate")
def eval_suite_validate(
    suite_path: Path = typer.Option(..., "--path", help="Path to suite YAML/JSON"),
) -> None:
    """Validate an eval suite configuration file."""
    if not suite_path.exists():
        typer.echo(f"Suite file not found: {suite_path}")
        raise typer.Exit(code=2)
    import json as _json

    try:
        text = suite_path.read_text(encoding="utf-8")
        data = _json.loads(text)
        required = ["id", "evaluators"]
        missing = [k for k in required if k not in data]
        if missing:
            typer.echo(f"Suite validation failed: missing fields {missing}")
            raise typer.Exit(code=1)
        render_result(
            "suite valid", {"id": data.get("id"), "evaluators": len(data.get("evaluators", []))}
        )
    except Exception as exc:
        typer.echo(f"Suite validation error: {exc}")
        raise typer.Exit(code=1) from None


# ─── Eval Dataset sub-commands ───────────────────────────────────────────────


@eval_dataset_app.command("list")
def eval_dataset_list(
    project: Path = typer.Option(Path("."), "--project", help="Project root"),
    as_json: bool = typer.Option(False, "--json", help="JSON output"),
) -> None:
    """List available evaluation datasets."""
    ds_dir = project / ".ananke" / "evals" / "datasets"
    if not ds_dir.exists():
        render_result("no datasets directory", {"path": str(ds_dir)})
        return
    from ananke.plexus.evals.datasets.loader import load_datasets_from_dir

    datasets = load_datasets_from_dir(ds_dir)
    summary = {ds_id: f"{len(ds.cases)} cases" for ds_id, ds in datasets.items()}
    if as_json:
        typer.echo(json.dumps(summary, indent=2))
        return
    render_result(f"{len(datasets)} dataset(s)", summary)


@eval_dataset_app.command("validate")
def eval_dataset_validate(
    dataset_path: Path = typer.Option(..., "--path", help="Path to dataset YAML/JSON"),
) -> None:
    """Validate an eval dataset file."""
    from ananke.plexus.evals.datasets.loader import load_dataset
    from ananke.plexus.evals.datasets.validator import validate_dataset

    ds = load_dataset(dataset_path)
    issues = validate_dataset(ds)
    if issues:
        typer.echo("Dataset validation failed:\n" + "\n".join(f"  - {i}" for i in issues))
        raise typer.Exit(code=1)
    render_result(f"dataset '{ds.id}' valid", {"cases": len(ds.cases)})


# ─── Eval Baseline sub-commands ──────────────────────────────────────────────


@eval_baseline_app.command("create")
def eval_baseline_create(
    run_id: str = typer.Option(..., "--run", help="Run ID to baseline"),
    baseline_id: str = typer.Option(..., "--id", help="Baseline identifier"),
    project: Path = typer.Option(Path("."), "--project", help="Project root"),
) -> None:
    """Create a baseline from an existing eval run's scores."""
    import json as _json
    from datetime import datetime

    scores_file = project / ".ananke" / "evidence" / run_id / "eval" / "scores.json"
    if not scores_file.exists():
        typer.echo(f"No eval scores for run '{run_id}'")
        raise typer.Exit(code=2)

    from ananke.plexus.evals.models.score import EvalScore

    scores = [EvalScore(**s) for s in _json.loads(scores_file.read_text(encoding="utf-8"))]
    agg = {s.dimension: s.normalized_score for s in scores if s.normalized_score is not None}

    from ananke.plexus.evals.models.baseline import Baseline

    baseline = Baseline(
        baseline_id=baseline_id,
        suite_id="unknown",
        version=run_id,
        scores=agg,
        created_at=datetime.now(tz=UTC),
    )
    baselines_dir = project / ".ananke" / "evals" / "baselines"
    baselines_dir.mkdir(parents=True, exist_ok=True)
    out = baselines_dir / f"{baseline_id}.json"
    out.write_text(baseline.model_dump_json(indent=2), encoding="utf-8")
    render_result(f"baseline '{baseline_id}' created", {"scores": agg, "path": str(out)})


# ─── Eval Trace sub-commands ─────────────────────────────────────────────────


@eval_trace_app.command("show")
def eval_trace_show(
    trace_path: Path = typer.Option(..., "--path", help="Path to trace JSON"),
) -> None:
    """Show a canonical trace summary."""
    from ananke.plexus.evals.traces.importers import load_trace_from_file

    trace = load_trace_from_file(trace_path)
    render_result(
        f"trace {trace.trace_id}",
        {
            "runtime": trace.runtime,
            "spans": len(trace.spans),
            "tool_calls": trace.usage.tool_calls,
            "tokens": trace.usage.total_tokens,
        },
    )


@eval_trace_app.command("import")
def eval_trace_import(
    trace_path: Path = typer.Option(..., "--path", help="Path to raw trace JSON"),
    run_id: str = typer.Option("", "--run-id", help="Override run ID"),
    project: Path = typer.Option(Path("."), "--project", help="Project root"),
) -> None:
    """Import and normalize a raw trace into the Ananke evidence store."""
    from ananke.plexus.evals.traces.exporters import export_trace_to_json
    from ananke.plexus.evals.traces.importers import load_trace_from_file

    trace = load_trace_from_file(trace_path)
    eff_run_id = run_id or trace.run_id
    out_path = project / ".ananke" / "traces" / f"{eff_run_id}.json"
    export_trace_to_json(trace, out_path)
    render_result(f"trace imported as '{eff_run_id}'", {"path": str(out_path)})


# ─── Eval Adapter sub-commands ───────────────────────────────────────────────


@eval_adapter_app.command("list")
def eval_adapter_list(
    as_json: bool = typer.Option(False, "--json", help="JSON output"),
) -> None:
    """List all available evaluation adapters and their status."""
    from ananke.plexus.evals.api import adapter_doctor

    results = adapter_doctor()
    if as_json:
        typer.echo(json.dumps(results, indent=2, default=str))
        return
    render_result(
        f"{len(results)} adapter(s)", {k: str(v.get("status", "?")) for k, v in results.items()}
    )


@eval_adapter_app.command("doctor")
def eval_adapter_doctor(
    adapter: str = typer.Argument("", help="Specific adapter ID (empty = all)"),
    as_json: bool = typer.Option(False, "--json", help="JSON output"),
) -> None:
    """Check health of an evaluation adapter."""
    from ananke.plexus.evals.api import adapter_doctor

    results = adapter_doctor()
    if adapter:
        filtered = {k: v for k, v in results.items() if adapter in k}
        if as_json:
            typer.echo(json.dumps(filtered, indent=2, default=str))
            return
        render_result(
            f"adapter doctor: {adapter}",
            {k: str(v.get("status", "?")) for k, v in filtered.items()},
        )
    else:
        if as_json:
            typer.echo(json.dumps(results, indent=2, default=str))
            return
        render_result(
            "adapter doctor: all", {k: str(v.get("status", "?")) for k, v in results.items()}
        )


# ─── Eval Judge sub-commands ─────────────────────────────────────────────────


@eval_judge_app.command("list")
def eval_judge_list() -> None:
    """List configured judge providers."""
    from ananke.plexus.evals.judges.gateway import EnterpriseJudgeGateway

    gw = EnterpriseJudgeGateway()
    render_result("available judge providers", {"providers": gw.list_providers()})


@eval_judge_app.command("test")
def eval_judge_test(
    provider: str = typer.Option("local", "--provider", help="Provider to test"),
) -> None:
    """Test the judge gateway with a trivial prompt."""
    from ananke.plexus.evals.judges.base import JudgeInputEnvelope
    from ananke.plexus.evals.judges.gateway import EnterpriseJudgeGateway
    from ananke.plexus.evals.models.rubric import Rubric

    gw = EnterpriseJudgeGateway(allowed_providers=list({provider, "local"}))
    rubric = Rubric(rubric_id="test-rubric", title="Test", pass_threshold=0.5)
    envelope = JudgeInputEnvelope(rubric=rubric, case_input="hello", agent_output="world")
    try:
        result = gw.score(envelope=envelope, provider=provider)
        render_result(
            f"judge test: {provider}", {"score": result.normalized_score, "passed": result.passed}
        )
    except Exception as exc:
        render_result(f"judge test failed: {exc}", {})
        raise typer.Exit(code=1) from None


# ─── Test CLI ──────────────────────────────────────────────────────────────

test_app = typer.Typer(help="Unified quality test harness")
test_profile_app = typer.Typer(help="Test profile operations")
test_mutation_app = typer.Typer(help="Mutation testing")
test_fuzz_app = typer.Typer(help="Fuzz testing")
test_formal_app = typer.Typer(help="Formal verification")
test_adapter_test_app = typer.Typer(help="Test adapter management")

app.add_typer(test_app, name="test")
test_app.add_typer(test_profile_app, name="profile")
test_app.add_typer(test_mutation_app, name="mutation")
test_app.add_typer(test_fuzz_app, name="fuzz")
test_app.add_typer(test_formal_app, name="formal")
test_app.add_typer(test_adapter_test_app, name="adapter")


@test_app.command("run")
def test_run(
    profile: str = typer.Option(
        "standard", "--profile", help="Quality profile: fast|standard|strict|verification|release"
    ),
    kind: list[str] = typer.Option([], "--kind", help="Filter by test kind (repeatable)"),
    select: str = typer.Option(
        "full", "--select", help="Selection mode: full|changed|impact|requirement"
    ),
    project: Path = typer.Option(Path("."), "--project", help="Project root"),
    as_json: bool = typer.Option(False, "--json", help="JSON output"),
) -> None:
    """Run the quality test suite."""
    from ananke.plexus.testing.api import run_quality_suite
    from ananke.plexus.testing.policy.thresholds import QualityConfigError, load_quality_config
    from ananke.plexus.testing.reports.console import generate_console_report

    try:
        gate_config = load_quality_config(project)
    except QualityConfigError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    run = run_quality_suite(
        project_root=project,
        profile=profile,
        kinds=list(kind) or None,
        selection=select,
        save_evidence=True,
    )
    from ananke.plexus.testing.policy.gates import apply_quality_gate

    decision = apply_quality_gate(run, gate_config)
    if as_json:
        typer.echo(run.model_dump_json(indent=2))
        if decision.blocks:
            raise typer.Exit(code=1)
        return
    typer.echo(generate_console_report(run))
    for reason in decision.reasons:
        typer.echo(f"quality gate: {reason}")
    if decision.blocks or any(r.status in ("fail", "error") for r in run.results):
        raise typer.Exit(code=1)


@test_app.command("discover")
def test_discover(
    profile: str = typer.Option("standard", "--profile", help="Quality profile"),
    project: Path = typer.Option(Path("."), "--project", help="Project root"),
    as_json: bool = typer.Option(False, "--json", help="JSON output"),
) -> None:
    """Discover available tests without running them."""
    from ananke.plexus.testing.discovery import discover_tests

    tests = discover_tests(project, profile=profile, adapters=None)
    if as_json:
        typer.echo(json.dumps({t.id: t.kind for t in tests}, indent=2))
        return
    render_result(f"{len(tests)} test(s) discovered", {t.id: t.kind for t in tests})


@test_app.command("list")
def test_list(
    project: Path = typer.Option(Path("."), "--project", help="Project root"),
    as_json: bool = typer.Option(False, "--json", help="JSON output"),
) -> None:
    """List all discovered tests."""
    from ananke.plexus.testing.discovery import discover_tests

    tests = discover_tests(project, profile="full", adapters=None)
    details = {t.id: f"{t.kind} [{t.engine}]" for t in tests}
    if as_json:
        typer.echo(json.dumps(details, indent=2))
        return
    render_result(f"{len(tests)} test(s)", details)


@test_app.command("report")
def test_report(
    run_id: str = typer.Option(..., "--run", help="Run ID to report on"),
    fmt: str = typer.Option("console", "--format", help="console|markdown|json|junit|sarif"),
    project: Path = typer.Option(Path("."), "--project", help="Project root"),
) -> None:
    """Generate a quality report for a completed run."""
    import json as _json

    results_file = project / ".ananke" / "evidence" / run_id / "quality" / "results.json"
    if not results_file.exists():
        typer.echo(f"No quality results for run '{run_id}'")
        raise typer.Exit(code=2)
    from ananke.plexus.testing.models.result import TestRun

    run = TestRun(**_json.loads(results_file.read_text(encoding="utf-8")))
    if fmt == "markdown":
        from ananke.plexus.testing.reports.markdown import generate_markdown_report

        typer.echo(generate_markdown_report(run))
    elif fmt == "json":
        typer.echo(run.model_dump_json(indent=2))
    elif fmt == "junit":
        from ananke.plexus.testing.reports.junit import generate_junit_xml

        typer.echo(generate_junit_xml(run))
    elif fmt == "sarif":
        from ananke.plexus.testing.reports.sarif import generate_sarif_report

        typer.echo(generate_sarif_report(run))
    else:
        from ananke.plexus.testing.reports.console import generate_console_report

        typer.echo(generate_console_report(run))


@test_profile_app.command("list")
def test_profile_list(as_json: bool = typer.Option(False, "--json", help="JSON output")) -> None:
    """List built-in quality profiles."""
    from ananke.plexus.testing.api import list_profiles

    profiles = list_profiles()
    if as_json:
        typer.echo(json.dumps(profiles, indent=2))
        return
    render_result("available profiles", {k: str(v) for k, v in profiles.items()})


@test_profile_app.command("show")
def test_profile_show(
    name: str = typer.Argument(help="Profile name"),
    as_json: bool = typer.Option(False, "--json", help="JSON output"),
) -> None:
    """Show a quality profile configuration."""
    from ananke.plexus.testing.models.profile import BUILTIN_PROFILES

    if name not in BUILTIN_PROFILES:
        typer.echo(f"Profile '{name}' not found")
        raise typer.Exit(code=2)
    profile_data = BUILTIN_PROFILES[name]
    if as_json:
        typer.echo(json.dumps(profile_data, indent=2))
        return
    render_result(f"profile: {name}", {k: str(v) for k, v in profile_data.items()})


@test_adapter_test_app.command("list")
def test_adapter_list(as_json: bool = typer.Option(False, "--json", help="JSON output")) -> None:
    """List all test adapters and their status."""
    from ananke.plexus.testing.api import adapter_doctor

    results = adapter_doctor()
    if as_json:
        typer.echo(json.dumps(results, indent=2))
        return
    render_result("test adapters", {k: str(v.get("available", False)) for k, v in results.items()})


@test_adapter_test_app.command("doctor")
def test_adapter_doctor(
    adapter: str = typer.Argument("", help="Adapter ID (empty = all)"),
    as_json: bool = typer.Option(False, "--json", help="JSON output"),
) -> None:
    """Check health of a test adapter."""
    from ananke.plexus.testing.api import adapter_doctor

    results = adapter_doctor()
    if adapter:
        filtered = {k: v for k, v in results.items() if adapter in k}
        if as_json:
            typer.echo(json.dumps(filtered, indent=2))
            return
        render_result(f"adapter doctor: {adapter}", {k: str(v) for k, v in filtered.items()})
    else:
        if as_json:
            typer.echo(json.dumps(results, indent=2))
            return
        render_result(
            "adapter doctor: all", {k: str(v.get("available", False)) for k, v in results.items()}
        )


@test_mutation_app.command("run")
def test_mutation_run(
    package: str = typer.Option("", "--package", help="Package to mutate"),
    project: Path = typer.Option(Path("."), "--project", help="Project root"),
) -> None:
    """Run mutation testing (requires mutmut)."""
    from ananke.plexus.testing.adapters.mutmut import MutmutAdapter

    adapter = MutmutAdapter()
    if not adapter.available():
        typer.echo("mutmut is not installed — pip install ananke-plexus[test-mutation]")
        raise typer.Exit(code=2)
    render_result("mutation run", {k: str(v) for k, v in adapter.doctor().items()})


@test_fuzz_app.command("run")
def test_fuzz_run(
    target: str = typer.Option(..., "--target", help="Fuzz target name"),
    seconds: int = typer.Option(30, "--seconds", help="Fuzz duration"),
    project: Path = typer.Option(Path("."), "--project", help="Project root"),
) -> None:
    """Run fuzz testing (requires cargo-fuzz)."""
    from ananke.plexus.testing.adapters.cargo_fuzz import CargoFuzzAdapter

    adapter = CargoFuzzAdapter()
    if not adapter.available():
        typer.echo("cargo-fuzz is not available")
        raise typer.Exit(code=2)
    render_result("fuzz run", {"target": target, "seconds": seconds})


@test_formal_app.command("run")
def test_formal_run(
    proof: str = typer.Option("", "--proof", help="Proof harness ID"),
    project: Path = typer.Option(Path("."), "--project", help="Project root"),
) -> None:
    """Run formal verification (requires Kani)."""
    from ananke.plexus.testing.adapters.kani import KaniAdapter

    adapter = KaniAdapter()
    if not adapter.available():
        typer.echo("Kani is not available")
        raise typer.Exit(code=2)
    render_result("formal run", {"proof": proof})
