"""Public API for orchestrating Ananke operations."""

import json
from pathlib import Path

from ananke.plexus.apm.importers.copilot import import_copilot_skills
from ananke.plexus.architecture.calm import (
    diff_calm,
    init_calm,
    reconcile_calm,
    render_calm,
    validate_calm,
)
from ananke.plexus.config.credentials import (
    auth_export_snippet,
    credential_validation_report,
    ensure_credentials_store,
    ensure_local_adapter_config,
    read_credentials_store,
    update_credentials_store,
)
from ananke.plexus.config.loader import load_config, write_default_config
from ananke.plexus.config.migration import migrate_config_file
from ananke.plexus.contracts.bmad import compile_bmad
from ananke.plexus.core.paths import ensure_project_layout
from ananke.plexus.core.result import CommandResult
from ananke.plexus.evidence.bundle import create_evidence_bundle
from ananke.plexus.evidence.retention import prune_evidence
from ananke.plexus.execution.state import (
    read_run_state,
    replay_run,
    start_run,
    update_run_state,
)
from ananke.plexus.gates.runner import GateRunner
from ananke.plexus.graph.providers.registry import resolve_graph_provider
from ananke.plexus.graph.service import export_graph, persist_graph, query_graph
from ananke.plexus.lifecycle.confluence import upsert_page
from ananke.plexus.lifecycle.git import make_branch_name
from ananke.plexus.lifecycle.issues import transition_issue
from ananke.plexus.lifecycle.scm import create_pr
from ananke.plexus.lifecycle.worktree import create_isolated_worktree, list_worktrees
from ananke.plexus.plugins.discovery import discover_plugins
from ananke.plexus.policy.engine import PolicyEngine
from ananke.plexus.specs.lock import write_spec_lock
from ananke.plexus.specs.models import Requirement
from ananke.plexus.specs.pipeline import run_full_spec_pipeline
from ananke.plexus.specs.providers.registry import resolve_spec_provider
from ananke.plexus.specs.service import (
    create_spec_bundle,
    create_tasks,
    diff_spec,
    lock_spec,
    plan_spec,
    validate_spec,
)


def _safe_int(value: object, default: int = 0) -> int:
    if not isinstance(value, (int, float, str)):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


class Ananke:
    def __init__(self, repository_root: Path) -> None:
        self.repository_root = repository_root.resolve()

    @classmethod
    def open(cls, repository_root: str | Path = ".") -> "Ananke":
        return cls(Path(repository_root))

    def init_project(self) -> CommandResult:
        layout = ensure_project_layout(self.repository_root)
        config_path = write_default_config(self.repository_root)
        local_config = ensure_local_adapter_config(self.repository_root)
        credentials_path = ensure_credentials_store(self.repository_root)

        local_example = layout["base"] / "config.local.toml.example"
        if not local_example.exists():
            local_example.write_text(
                """# Local-only overrides. Copy to config.local.toml (ignored by git).
# Use `ananke configure auth` to set credential values in `.ananke/secrets/adapters.env`.
[jira]
base_url = { env = "ANANKE_JIRA_BASE_URL" }
email = { env = "ANANKE_JIRA_EMAIL" }
token = { env = "ANANKE_JIRA_TOKEN" }
bearer_token = { env = "ANANKE_JIRA_BEARER_TOKEN" }

[confluence]
base_url = { env = "ANANKE_CONFLUENCE_BASE_URL" }
email = { env = "ANANKE_CONFLUENCE_EMAIL" }
token = { env = "ANANKE_CONFLUENCE_TOKEN" }
bearer_token = { env = "ANANKE_CONFLUENCE_BEARER_TOKEN" }

[bitbucket]
base_url = { env = "ANANKE_BITBUCKET_BASE_URL" }
workspace = { env = "ANANKE_BITBUCKET_WORKSPACE" }
repo_slug = { env = "ANANKE_BITBUCKET_REPO_SLUG" }
destination_branch = { env = "ANANKE_BITBUCKET_DEST_BRANCH" }
username = { env = "ANANKE_BITBUCKET_USERNAME" }
app_password = { env = "ANANKE_BITBUCKET_APP_PASSWORD" }
bearer_token = { env = "ANANKE_BITBUCKET_BEARER_TOKEN" }

[lifecycle]
remote_live_enabled = { env = "ANANKE_REMOTE_LIVE_ENABLED" }
remote_live_services = { env = "ANANKE_REMOTE_LIVE_SERVICES" }

[mcp]
http_bearer_token = { env = "ANANKE_MCP_HTTP_TOKEN" }
""",
                encoding="utf-8",
            )

        policy_default = layout["policy"] / "default.toml"
        if not policy_default.exists():
            policy_default.write_text(
                """[[rule]]
id = "policy.core.fail-closed"
stage = "verify"
severity = "high"
gate = "core"
assert = "config.ananke.fail_closed == true"
on_failure = "block"
""",
                encoding="utf-8",
            )

        calm_path = layout["architecture"] / "system.calm.json"
        if not calm_path.exists():
            calm_path.write_text(
                json.dumps(
                    {
                        "schema_version": "0.1",
                        "system": {"name": "ananke-system"},
                        "components": [],
                        "relationships": [],
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

        return CommandResult(
            ok=True,
            summary="Ananke project initialized.",
            details={
                "config": str(config_path),
                "local_config": str(local_config),
                "credentials": str(credentials_path),
                "policy": str(policy_default),
                "architecture": str(calm_path),
            },
        )

    def configure_auth(
        self,
        jira_base_url: str | None = None,
        jira_email: str | None = None,
        jira_token: str | None = None,
        jira_bearer_token: str | None = None,
        jira_bearer_token_cmd: str | None = None,
        jira_token_cmd: str | None = None,
        confluence_base_url: str | None = None,
        confluence_email: str | None = None,
        confluence_token: str | None = None,
        confluence_bearer_token: str | None = None,
        confluence_bearer_token_cmd: str | None = None,
        confluence_token_cmd: str | None = None,
        bitbucket_base_url: str | None = None,
        bitbucket_workspace: str | None = None,
        bitbucket_repo_slug: str | None = None,
        bitbucket_dest_branch: str | None = None,
        bitbucket_username: str | None = None,
        bitbucket_app_password: str | None = None,
        bitbucket_bearer_token: str | None = None,
        bitbucket_bearer_token_cmd: str | None = None,
        bitbucket_app_password_cmd: str | None = None,
        mcp_http_token: str | None = None,
        mcp_http_token_cmd: str | None = None,
        remote_live_enabled: str | None = None,
        remote_live_services: str | None = None,
    ) -> CommandResult:
        ensure_project_layout(self.repository_root)
        local_config = ensure_local_adapter_config(self.repository_root)

        updates: dict[str, str] = {}
        if jira_base_url is not None:
            updates["ANANKE_JIRA_BASE_URL"] = jira_base_url
        if jira_email is not None:
            updates["ANANKE_JIRA_EMAIL"] = jira_email
        if jira_token is not None:
            updates["ANANKE_JIRA_TOKEN"] = jira_token
        if jira_bearer_token is not None:
            updates["ANANKE_JIRA_BEARER_TOKEN"] = jira_bearer_token
        if jira_bearer_token_cmd is not None:
            updates["ANANKE_JIRA_BEARER_TOKEN_CMD"] = jira_bearer_token_cmd
        if jira_token_cmd is not None:
            updates["ANANKE_JIRA_TOKEN_CMD"] = jira_token_cmd
        if confluence_base_url is not None:
            updates["ANANKE_CONFLUENCE_BASE_URL"] = confluence_base_url
        if confluence_email is not None:
            updates["ANANKE_CONFLUENCE_EMAIL"] = confluence_email
        if confluence_token is not None:
            updates["ANANKE_CONFLUENCE_TOKEN"] = confluence_token
        if confluence_bearer_token is not None:
            updates["ANANKE_CONFLUENCE_BEARER_TOKEN"] = confluence_bearer_token
        if confluence_bearer_token_cmd is not None:
            updates["ANANKE_CONFLUENCE_BEARER_TOKEN_CMD"] = confluence_bearer_token_cmd
        if confluence_token_cmd is not None:
            updates["ANANKE_CONFLUENCE_TOKEN_CMD"] = confluence_token_cmd
        if bitbucket_base_url is not None:
            updates["ANANKE_BITBUCKET_BASE_URL"] = bitbucket_base_url
        if bitbucket_workspace is not None:
            updates["ANANKE_BITBUCKET_WORKSPACE"] = bitbucket_workspace
        if bitbucket_repo_slug is not None:
            updates["ANANKE_BITBUCKET_REPO_SLUG"] = bitbucket_repo_slug
        if bitbucket_dest_branch is not None:
            updates["ANANKE_BITBUCKET_DEST_BRANCH"] = bitbucket_dest_branch
        if bitbucket_username is not None:
            updates["ANANKE_BITBUCKET_USERNAME"] = bitbucket_username
        if bitbucket_app_password is not None:
            updates["ANANKE_BITBUCKET_APP_PASSWORD"] = bitbucket_app_password
        if bitbucket_bearer_token is not None:
            updates["ANANKE_BITBUCKET_BEARER_TOKEN"] = bitbucket_bearer_token
        if bitbucket_bearer_token_cmd is not None:
            updates["ANANKE_BITBUCKET_BEARER_TOKEN_CMD"] = bitbucket_bearer_token_cmd
        if bitbucket_app_password_cmd is not None:
            updates["ANANKE_BITBUCKET_APP_PASSWORD_CMD"] = bitbucket_app_password_cmd
        if mcp_http_token is not None:
            updates["ANANKE_MCP_HTTP_TOKEN"] = mcp_http_token
        if mcp_http_token_cmd is not None:
            updates["ANANKE_MCP_HTTP_TOKEN_CMD"] = mcp_http_token_cmd
        if remote_live_enabled is not None:
            updates["ANANKE_REMOTE_LIVE_ENABLED"] = remote_live_enabled
        if remote_live_services is not None:
            updates["ANANKE_REMOTE_LIVE_SERVICES"] = remote_live_services

        path, changed, applied = update_credentials_store(self.repository_root, updates)
        details: dict[str, str | int | float | bool] = {
            "credentials": str(path),
            "local_config": str(local_config),
            "updated_keys": changed,
        }
        if applied:
            details["applied"] = ",".join(sorted(applied.keys()))
        return CommandResult(ok=True, summary="Adapter auth configured.", details=details)

    def configure_auth_values(self) -> CommandResult:
        values = read_credentials_store(self.repository_root)
        details: dict[str, str | int | float | bool] = {}
        for key, value in values.items():
            if key.endswith("TOKEN") or key.endswith("PASSWORD"):
                details[key] = "***"
            elif key.endswith("_CMD"):
                details[key] = "<configured>" if value else ""
            else:
                details[key] = value
        return CommandResult(ok=True, summary="Adapter auth values loaded.", details=details)

    def configure_auth_export(self, shell: str = "zsh") -> CommandResult:
        path, snippet = auth_export_snippet(self.repository_root, shell)
        return CommandResult(
            ok=True,
            summary="Auth export snippet generated.",
            details={"credentials": str(path), "shell": shell, "snippet": snippet},
        )

    def configure_auth_validate(self) -> CommandResult:
        report = credential_validation_report(self.repository_root)
        services = report.get("services", {})
        details: dict[str, str | int | float | bool] = {
            "ready": bool(report.get("ok", False)),
        }
        if isinstance(services, dict):
            for service, item in services.items():
                if not isinstance(item, dict):
                    continue
                service_ready = bool(item.get("ready", False))
                missing = item.get("missing", [])
                missing_text = "none"
                if isinstance(missing, list):
                    missing_text = "none" if not missing else ",".join(str(x) for x in missing)
                details[f"{service}_ready"] = service_ready
                details[f"{service}_missing"] = missing_text
        return CommandResult(
            ok=bool(report.get("ok", False)),
            summary="Adapter auth validation completed.",
            details=details,
        )

    def config_migrate(self, apply: bool = False) -> CommandResult:
        report = migrate_config_file(self.repository_root, apply=apply)
        details: dict[str, str | int | float | bool] = {
            "config_path": str(report.get("config_path", "")),
            "mode": "apply" if apply else "analyze",
            "changed": bool(report.get("changed", False)),
            "created": bool(report.get("created", False)),
            "missing_count": _safe_int(report.get("missing_count", 0)),
            "unknown_count": _safe_int(report.get("unknown_count", 0)),
        }
        unknown_sections = report.get("unknown_sections", [])
        if isinstance(unknown_sections, list) and unknown_sections:
            details["unknown_sections"] = ",".join(str(x) for x in unknown_sections)
        unknown_keys = report.get("unknown_keys", [])
        if isinstance(unknown_keys, list) and unknown_keys:
            details["unknown_keys"] = ",".join(str(x) for x in unknown_keys)
        missing_keys = report.get("missing_keys", [])
        if isinstance(missing_keys, list) and missing_keys:
            details["missing_keys"] = ",".join(str(x) for x in missing_keys)

        return CommandResult(
            ok=bool(report.get("ok", False)),
            summary=str(report.get("summary", "Config migration completed.")),
            details=details,
        )

    def evidence_prune(self, older_than: str = "30d", apply: bool = False) -> CommandResult:
        try:
            result = prune_evidence(self.repository_root, older_than=older_than, apply=apply)
        except ValueError as exc:
            return CommandResult(
                ok=False,
                summary="Evidence prune failed.",
                details={"error": str(exc), "older_than": older_than},
            )

        details: dict[str, str | int | float | bool] = {
            "mode": str(result.get("mode", "dry-run")),
            "older_than": str(result.get("older_than", older_than)),
            "candidate_count": _safe_int(result.get("candidate_count", 0)),
            "prunable_count": _safe_int(result.get("prunable_count", 0)),
            "pruned_count": _safe_int(result.get("pruned_count", 0)),
            "skipped_tracked_count": _safe_int(result.get("skipped_tracked_count", 0)),
        }
        for key, value in result.items():
            if key.startswith("target_"):
                details[key] = str(value)
        return CommandResult(ok=True, summary="Evidence prune evaluated.", details=details)

    def doctor(self) -> CommandResult:
        config = load_config(self.repository_root)
        return CommandResult(
            ok=True,
            summary="Environment diagnostics completed.",
            details={
                "project_name": config.project.name,
                "mode": config.ananke.mode,
                "offline": str(config.ananke.offline),
            },
        )

    def create_spec(
        self,
        requirement: Requirement,
        provider_name: str | None = None,
    ) -> CommandResult:
        ensure_project_layout(self.repository_root)
        config = load_config(self.repository_root)
        selected_provider = provider_name or config.spec.provider
        provider = resolve_spec_provider(selected_provider)
        available, reason = provider.available()

        feature_dir = create_spec_bundle(self.repository_root, requirement, selected_provider)
        bmad = compile_bmad(feature_dir, requirement)
        lock = write_spec_lock(feature_dir)
        return CommandResult(
            ok=True,
            summary="Requirement converted into spec bundle.",
            details={
                "feature_dir": str(feature_dir),
                "bmad": str(bmad),
                "lock": str(lock),
                "provider": selected_provider,
                "provider_available": str(available),
                "provider_reason": reason,
            },
        )

    def plan(self, feature_dir: Path, provider_name: str | None = None) -> CommandResult:
        config = load_config(self.repository_root)
        selected_provider = provider_name or config.spec.provider
        plan_path = plan_spec(feature_dir, selected_provider)
        return CommandResult(ok=True, summary="Plan generated.", details={"plan": str(plan_path)})

    def tasks(self, feature_dir: Path, provider_name: str | None = None) -> CommandResult:
        config = load_config(self.repository_root)
        selected_provider = provider_name or config.spec.provider
        tasks_path = create_tasks(feature_dir, selected_provider)
        return CommandResult(
            ok=True,
            summary="Tasks generated.",
            details={"tasks": str(tasks_path)},
        )

    def lock(self, feature_dir: Path) -> CommandResult:
        lock_path = lock_spec(feature_dir)
        return CommandResult(
            ok=True,
            summary="Spec lock updated.",
            details={"lock": str(lock_path)},
        )

    def validate(self, feature_dir: Path) -> CommandResult:
        valid, errors = validate_spec(feature_dir)
        return CommandResult(
            ok=valid,
            summary="Spec valid." if valid else "Spec validation failed.",
            details={"errors": "none" if valid else ";".join(errors)},
        )

    def diff(self, feature_dir: Path) -> CommandResult:
        drift = diff_spec(feature_dir)
        return CommandResult(
            ok=len(drift) == 0,
            summary="No spec drift." if len(drift) == 0 else "Spec drift detected.",
            details={"drift": "none" if len(drift) == 0 else ",".join(drift)},
        )

    def converge(
        self,
        requirement: Requirement,
        provider_name: str | None = None,
    ) -> CommandResult:
        ensure_project_layout(self.repository_root)
        config = load_config(self.repository_root)
        selected_provider = provider_name or config.spec.provider
        provider = resolve_spec_provider(selected_provider)
        result = run_full_spec_pipeline(self.repository_root, requirement, provider)
        details: dict[str, str | int | float | bool] = {key: value for key, value in result.items()}
        return CommandResult(ok=True, summary="Spec pipeline converged.", details=details)

    def verify(self) -> CommandResult:
        gates = GateRunner().run_local_full(self.repository_root)
        gate_map: dict[str, dict[str, object]] = {
            gate.gate_id: {
                "status": gate.status,
                "severity": gate.severity,
                "summary": gate.summary,
                "command": gate.command,
                "exit_code": gate.exit_code if gate.exit_code is not None else "",
            }
            for gate in gates
        }
        policy = PolicyEngine(self.repository_root).evaluate_verify(gate_map)
        gate_summary = {gate.gate_id: gate.status for gate in gates}
        bundle_dir = create_evidence_bundle(
            self.repository_root,
            gate_summary,
            gate_outcomes=[gate.__dict__ for gate in gates],
            policy_decisions=[item.model_dump() for item in policy],
        )
        blocked_gates = sum(1 for gate in gates if gate.status in {"BLOCKED", "ERROR"})
        blocked_policy = sum(1 for item in policy if item.status == "BLOCKED")
        return CommandResult(
            ok=blocked_gates == 0 and blocked_policy == 0,
            summary="Verification completed.",
            details={
                "evidence_bundle": str(bundle_dir),
                "policy_decisions": str(len(policy)),
                "gates": str(len(gates)),
                "blocked_gates": blocked_gates,
                "blocked_policy": blocked_policy,
            },
        )

    def policy_explain(self, stage: str = "verify") -> CommandResult:
        gates = GateRunner().run_local_full(self.repository_root) if stage == "verify" else []
        gate_map: dict[str, dict[str, object]] = {
            gate.gate_id: {
                "status": gate.status,
                "severity": gate.severity,
                "summary": gate.summary,
            }
            for gate in gates
        }
        report = PolicyEngine(self.repository_root).explain_stage(stage, gate_map)
        details: dict[str, str | int | float | bool] = {
            "stage": str(report.get("stage", stage)),
            "rule_count": _safe_int(report.get("rule_count", 0)),
            "pass_count": _safe_int(report.get("pass_count", 0)),
            "warn_count": _safe_int(report.get("warn_count", 0)),
            "blocked_count": _safe_int(report.get("blocked_count", 0)),
        }
        rules = report.get("rules", [])
        if isinstance(rules, list):
            for index, item in enumerate(rules, start=1):
                details[f"rule_{index}"] = json.dumps(item, sort_keys=True)
        return CommandResult(ok=True, summary="Policy explanation loaded.", details=details)

    def graph_build(self) -> CommandResult:
        ensure_project_layout(self.repository_root)
        provider = resolve_graph_provider("native")
        snapshot = provider.build(self.repository_root)
        path = persist_graph(self.repository_root, snapshot)
        return CommandResult(
            ok=True,
            summary="Graph built.",
            details={
                "snapshot_id": snapshot.snapshot_id,
                "nodes": len(snapshot.nodes),
                "edges": len(snapshot.edges),
                "path": str(path),
            },
        )

    def graph_query(self, needle: str) -> CommandResult:
        nodes = query_graph(self.repository_root, needle)
        details: dict[str, str | int | float | bool] = {
            "matches": len(nodes),
        }
        for index, node in enumerate(nodes[:10], start=1):
            details[f"match_{index}"] = f"{node.kind}:{node.path}:{node.name}"
        return CommandResult(ok=True, summary="Graph query completed.", details=details)

    def graph_impact(self, changed_files: list[str]) -> CommandResult:
        provider = resolve_graph_provider("native")
        report = provider.impact(self.repository_root, changed_files)
        details: dict[str, str | int | float | bool] = {
            "changed_files": len(report.changed_files),
            "impacted_symbols": len(report.impacted_symbols),
            "summary": report.summary,
        }
        for index, symbol in enumerate(report.impacted_symbols[:20], start=1):
            details[f"symbol_{index}"] = symbol
        return CommandResult(ok=True, summary="Graph impact evaluated.", details=details)

    def graph_export(self, fmt: str = "json") -> CommandResult:
        out_path = export_graph(self.repository_root, fmt)
        return CommandResult(
            ok=True,
            summary="Graph exported.",
            details={"format": fmt, "path": str(out_path)},
        )

    def arch_init(self) -> CommandResult:
        path = init_calm(self.repository_root)
        return CommandResult(
            ok=True,
            summary="Architecture initialized.",
            details={"path": str(path)},
        )

    def arch_validate(self) -> CommandResult:
        result = validate_calm(self.repository_root)
        details: dict[str, str | int | float | bool] = {
            "schema_version": str(result.get("schema_version", "")),
            "system_name": str(result.get("system_name", "")),
            "component_count": _safe_int(result.get("component_count", 0)),
            "relationship_count": _safe_int(result.get("relationship_count", 0)),
            "issue_count": _safe_int(result.get("issue_count", 0)),
        }
        issues = result.get("issues", [])
        if isinstance(issues, list):
            for index, issue in enumerate(issues[:20], start=1):
                details[f"issue_{index}"] = str(issue)
        return CommandResult(
            ok=bool(result.get("ok", False)),
            summary="Architecture validation completed.",
            details=details,
        )

    def arch_diff(self) -> CommandResult:
        result = diff_calm(self.repository_root)
        missing_from_calm = result.get("missing_from_calm", [])
        missing_from_graph = result.get("missing_from_graph", [])
        missing_from_calm_count = (
            len(missing_from_calm) if isinstance(missing_from_calm, list) else 0
        )
        missing_from_graph_count = (
            len(missing_from_graph) if isinstance(missing_from_graph, list) else 0
        )
        details: dict[str, str | int | float | bool] = {
            "missing_from_calm": missing_from_calm_count,
            "missing_from_graph": missing_from_graph_count,
        }
        for key in ["missing_from_calm", "missing_from_graph"]:
            values = result.get(key, [])
            if isinstance(values, list):
                for index, value in enumerate(values[:20], start=1):
                    details[f"{key}_{index}"] = str(value)
        return CommandResult(
            ok=bool(result.get("ok", False)),
            summary="Architecture diff completed.",
            details=details,
        )

    def arch_reconcile(self, apply: bool = False) -> CommandResult:
        result = reconcile_calm(self.repository_root, apply=apply)
        details: dict[str, str | int | float | bool] = {
            "applied": bool(result.get("applied", False)),
            "added_count": _safe_int(result.get("added_count", 0)),
        }
        added = result.get("added", [])
        if isinstance(added, list):
            for index, value in enumerate(added[:20], start=1):
                details[f"added_{index}"] = str(value)
        if "path" in result:
            details["path"] = str(result.get("path", ""))
        return CommandResult(
            ok=bool(result.get("ok", False)),
            summary="Architecture reconciliation completed.",
            details=details,
        )

    def arch_render(self) -> CommandResult:
        result = render_calm(self.repository_root)
        details: dict[str, str | int | float | bool] = {
            "path": str(result.get("path", "")),
            "component_count": _safe_int(result.get("component_count", 0)),
            "overlay_count": _safe_int(result.get("overlay_count", 0)),
        }
        return CommandResult(
            ok=bool(result.get("ok", False)),
            summary="Architecture render completed.",
            details=details,
        )

    def plugin_list(self) -> CommandResult:
        groups = {
            "ananke.graph": discover_plugins("ananke.graph"),
            "ananke.backend": discover_plugins("ananke.backend"),
        }
        total = sum(len(values) for values in groups.values())
        details: dict[str, str | int | float | bool] = {"count": total}
        for group_name, values in groups.items():
            details[group_name] = str(sorted(values.keys()))
        return CommandResult(ok=True, summary="Plugin discovery completed.", details=details)

    def run_start(self, spec_id: str) -> CommandResult:
        ensure_project_layout(self.repository_root)
        state_path = start_run(self.repository_root, spec_id)
        worktree = create_isolated_worktree(self.repository_root, state_path.parent.name)
        return CommandResult(
            ok=True,
            summary="Run started.",
            details={
                "state": str(state_path),
                "run_id": state_path.parent.name,
                "worktree": str(worktree),
            },
        )

    def run_status(self, run_id: str) -> CommandResult:
        payload = read_run_state(self.repository_root, run_id)
        if not payload:
            return CommandResult(
                ok=False,
                summary="Run not found.",
                details={"run_id": run_id},
            )
        state = payload.get("state", "unknown")
        spec_id = payload.get("spec_id", "")
        steps = payload.get("steps", [])
        step_count = len(steps) if isinstance(steps, list) else 0
        return CommandResult(
            ok=True,
            summary="Run status loaded.",
            details={
                "run_id": run_id,
                "state": str(state),
                "spec_id": str(spec_id),
                "steps": step_count,
            },
        )

    def run_cancel(self, run_id: str) -> CommandResult:
        payload = update_run_state(self.repository_root, run_id, "CANCELLED")
        if not payload:
            return CommandResult(ok=False, summary="Run not found.", details={"run_id": run_id})
        return CommandResult(
            ok=True,
            summary="Run cancelled.",
            details={"run_id": run_id, "state": "CANCELLED"},
        )

    def run_replay(self, run_id: str) -> CommandResult:
        payload = replay_run(self.repository_root, run_id)
        if not payload:
            return CommandResult(ok=False, summary="Run not found.", details={"run_id": run_id})
        return CommandResult(
            ok=True,
            summary="Run replayed.",
            details={"run_id": run_id, "state": "READY"},
        )

    def lifecycle_branch(self, change_type: str, ticket: str, slug: str) -> CommandResult:
        branch = make_branch_name(change_type, ticket, slug)
        return CommandResult(ok=True, summary="Branch name generated.", details={"branch": branch})

    def lifecycle_transition_issue(
        self,
        issue_key: str,
        target_state: str,
        idempotency_key: str,
        mode: str = "auto",
    ) -> CommandResult:
        result = transition_issue(
            self.repository_root,
            issue_key,
            target_state,
            idempotency_key,
            mode=mode,
        )
        details: dict[str, str | int | float | bool] = {key: value for key, value in result.items()}
        return CommandResult(ok=True, summary="Issue transition processed.", details=details)

    def lifecycle_create_pr(
        self,
        title: str,
        branch: str,
        idempotency_key: str,
        mode: str = "auto",
    ) -> CommandResult:
        result = create_pr(
            self.repository_root,
            title,
            branch,
            idempotency_key,
            mode=mode,
        )
        details: dict[str, str | int | float | bool] = {key: value for key, value in result.items()}
        return CommandResult(ok=True, summary="PR lifecycle processed.", details=details)

    def lifecycle_worktrees(self) -> CommandResult:
        worktrees = list_worktrees(self.repository_root)
        details: dict[str, str | int | float | bool] = {"count": len(worktrees)}
        for index, item in enumerate(worktrees[:20], start=1):
            details[f"worktree_{index}"] = item
        return CommandResult(ok=True, summary="Worktrees listed.", details=details)

    def lifecycle_confluence_upsert(
        self,
        space_key: str,
        title: str,
        content_path: str,
        idempotency_key: str,
        mode: str = "auto",
    ) -> CommandResult:
        result = upsert_page(
            self.repository_root,
            space_key,
            title,
            content_path,
            idempotency_key,
            mode=mode,
        )
        details: dict[str, str | int | float | bool] = {key: value for key, value in result.items()}
        return CommandResult(ok=True, summary="Confluence lifecycle processed.", details=details)

    def lifecycle_telemetry(self) -> CommandResult:
        from ananke.plexus.lifecycle.telemetry import telemetry_snapshot

        telemetry = telemetry_snapshot(self.repository_root)
        details: dict[str, str | int | float | bool] = {}
        services = telemetry.get("services", {}) if isinstance(telemetry, dict) else {}
        if isinstance(services, dict):
            details["service_count"] = len(services)
            for service, item in services.items():
                if not isinstance(item, dict):
                    continue
                details[f"{service}_attempts"] = int(item.get("attempts", 0))
                details[f"{service}_consecutive_failures"] = int(
                    item.get("consecutive_failures", 0)
                )
                details[f"{service}_last_status"] = str(item.get("last_status", "unknown"))
        return CommandResult(ok=True, summary="Lifecycle telemetry loaded.", details=details)

    def lifecycle_evidence(
        self,
        service: str | None = None,
        status: str | None = None,
        severity: str | None = None,
        limit: int = 20,
        since_hours: float | None = None,
        output_format: str = "compact",
        aggregate: bool = False,
        trend: str | None = None,
        csv_path: str | None = None,
    ) -> CommandResult:
        from ananke.plexus.lifecycle.evidence import (
            derive_event_severity,
            read_remote_events,
            summarize_remote_event_trends,
            summarize_remote_events,
            write_remote_events_csv,
        )

        output = output_format.strip().lower()
        if output not in {"compact", "json", "csv"}:
            output = "compact"
        trend_mode = (trend or "").strip().lower()
        if trend_mode not in {"", "hour", "day"}:
            trend_mode = ""

        events = read_remote_events(
            self.repository_root,
            service=service,
            status=status,
            severity=severity,
            limit=limit,
            since_hours=since_hours,
        )
        details: dict[str, str | int | float | bool] = {
            "event_count": len(events),
            "filter_service": service or "",
            "filter_status": status or "",
            "filter_severity": severity or "",
            "filter_since_hours": since_hours or 0,
            "format": output,
            "aggregate": aggregate,
            "trend": trend_mode,
        }

        display_rows: list[dict[str, object]]
        is_trend = trend_mode in {"hour", "day"}
        if is_trend:
            display_rows = summarize_remote_event_trends(events, trend_mode)
            details["row_count"] = len(display_rows)
        elif aggregate:
            display_rows = summarize_remote_events(events)
        else:
            display_rows = events

        if output == "csv":
            default_csv_name = (
                "lifecycle-remote-events-trend.csv"
                if is_trend
                else (
                    "lifecycle-remote-events-summary.csv"
                    if aggregate
                    else "lifecycle-remote-events.csv"
                )
            )
            resolved_path = (
                Path(csv_path)
                if csv_path
                else self.repository_root / ".ananke" / "evidence" / default_csv_name
            )
            exported = write_remote_events_csv(
                display_rows,
                resolved_path,
                aggregate=aggregate,
                trend=is_trend,
            )
            details["csv_path"] = str(exported)
            details["row_count"] = len(display_rows)
            return CommandResult(ok=True, summary="Lifecycle evidence loaded.", details=details)

        for index, event in enumerate(display_rows, start=1):
            if output == "json":
                details[f"event_{index}"] = json.dumps(event, sort_keys=True)
                continue
            if is_trend:
                details[f"event_{index}"] = (
                    f"bucket={event.get('bucket', '')} | "
                    f"service={event.get('service', '')} | "
                    f"status={event.get('status', '')} | "
                    f"severity={event.get('severity', '')} | "
                    f"count={event.get('count', 0)}"
                )
                continue
            if aggregate:
                summary_severity = derive_event_severity(str(event.get("status", "")))
                details[f"event_{index}"] = (
                    f"service={event.get('service', '')} | "
                    f"status={event.get('status', '')} | "
                    f"severity={summary_severity} | "
                    f"count={event.get('count', 0)}"
                )
                continue
            details[f"event_{index}"] = (
                f"{event.get('ts', '')} | "
                f"{event.get('service', '')}/{event.get('operation', '')} | "
                f"{event.get('status', '')} | "
                f"severity={event.get('severity', '')} | "
                f"http={event.get('http_status', '')} | "
                f"attempts={event.get('attempts', 0)} | "
                f"elapsed_ms={event.get('elapsed_ms', 0)}"
            )
        return CommandResult(ok=True, summary="Lifecycle evidence loaded.", details=details)

    def apm_import_copilot(self, source_dir: Path) -> CommandResult:
        ensure_project_layout(self.repository_root)
        installed = import_copilot_skills(self.repository_root, source_dir)
        details: dict[str, str | int | float | bool] = {"imported": len(installed)}
        for index, item in enumerate(installed[:20], start=1):
            details[f"skill_{index}"] = item
        return CommandResult(ok=True, summary="Copilot skills imported.", details=details)
