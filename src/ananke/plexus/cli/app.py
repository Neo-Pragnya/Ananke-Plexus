"""Main Typer CLI entrypoint."""

import json
from pathlib import Path

import typer

from ananke.plexus import __version__
from ananke.plexus.api import Ananke
from ananke.plexus.cli.rendering import render_result
from ananke.plexus.mcp.http import serve_http
from ananke.plexus.mcp.server import serve_stdio
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
