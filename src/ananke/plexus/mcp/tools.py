"""MCP tool handlers mapped to Ananke API — read-only and mutation surfaces."""

from __future__ import annotations

from pathlib import Path

from ananke.plexus.api import Ananke


def call_tool(repository_root: Path, tool_name: str, args: dict[str, object]) -> dict[str, object]:
    app = Ananke.open(repository_root)

    # ------------------------------------------------------------------ #
    # Read-only tools                                                      #
    # ------------------------------------------------------------------ #

    if tool_name == "ananke.project.status":
        return app.doctor().model_dump()

    if tool_name == "ananke.spec.get":
        feature_dir = str(args.get("feature_dir", ""))
        if not feature_dir:
            return {"ok": False, "summary": "feature_dir required", "details": {}}
        path = Path(feature_dir)
        if not path.exists():
            return {"ok": False, "summary": "feature_dir not found", "details": {}}
        files = sorted(item.name for item in path.iterdir() if item.is_file())
        return {"ok": True, "summary": "spec fetched", "details": {"files": ",".join(files)}}

    if tool_name == "ananke.spec.validate":
        feature_dir = str(args.get("feature_dir", ""))
        if not feature_dir:
            return {"ok": False, "summary": "feature_dir required", "details": {}}
        return app.validate(Path(feature_dir)).model_dump()

    if tool_name == "ananke.graph.query":
        text = str(args.get("text", ""))
        return app.graph_query(text).model_dump()

    if tool_name == "ananke.graph.impact":
        changed = args.get("changed_files", [])
        if isinstance(changed, str):
            changed_files = [changed]
        elif isinstance(changed, list):
            changed_files = [str(item) for item in changed]
        else:
            changed_files = []
        return app.graph_impact(changed_files).model_dump()

    if tool_name == "ananke.graph.update":
        return app.graph_build().model_dump()

    if tool_name == "ananke.arch.get":
        calm_path = repository_root / ".ananke" / "architecture" / "system.calm.json"
        if not calm_path.exists():
            return {"ok": False, "summary": "no CALM document found", "details": {}}
        return {
            "ok": True,
            "summary": "architecture loaded",
            "details": {"calm": calm_path.read_text(encoding="utf-8")},
        }

    if tool_name == "ananke.arch.validate":
        return app.arch_validate().model_dump()

    if tool_name == "ananke.arch.render":
        return app.arch_render().model_dump()

    if tool_name == "ananke.policy.explain":
        stage = str(args.get("stage", "verify"))
        return app.policy_explain(stage=stage).model_dump()

    if tool_name == "ananke.verify.run":
        return app.verify().model_dump()

    if tool_name == "ananke.run.status":
        run_id = str(args.get("run_id", ""))
        return app.run_status(run_id).model_dump()

    if tool_name == "ananke.evidence.get":
        run_id = str(args.get("run_id", ""))
        path = repository_root / ".ananke" / "evidence" / run_id / "manifest.json"
        if not path.exists():
            return {"ok": False, "summary": "evidence not found", "details": {"run_id": run_id}}
        manifest = path.read_text(encoding="utf-8")
        return {"ok": True, "summary": "evidence found", "details": {"manifest": manifest}}

    if tool_name == "ananke.spec.create":
        from ananke.plexus.specs.models import Requirement

        req_id = str(args.get("requirement_id", ""))
        title = str(args.get("title", ""))
        body = str(args.get("body", title))
        if not req_id or not title:
            return {"ok": False, "summary": "requirement_id and title required", "details": {}}
        acceptance = args.get("acceptance_criteria", [])
        criteria = [str(c) for c in acceptance] if isinstance(acceptance, list) else []
        req = Requirement(
            requirement_id=req_id, title=title, body=body, acceptance_criteria=criteria
        )
        return app.create_spec(req).model_dump()

    # ------------------------------------------------------------------ #
    # Mutation tools (H4)                                                  #
    # ------------------------------------------------------------------ #

    if tool_name == "ananke.run.execute":
        spec_id = str(args.get("spec_id", ""))
        if not spec_id:
            return {"ok": False, "summary": "spec_id required", "details": {}}
        return app.run_start(spec_id).model_dump()

    if tool_name == "ananke.git.create_branch":
        change_type = str(args.get("change_type", "feature"))
        ticket = str(args.get("ticket", ""))
        slug = str(args.get("slug", ""))
        if not ticket or not slug:
            return {"ok": False, "summary": "ticket and slug required", "details": {}}
        from ananke.plexus.lifecycle.git import make_branch_name

        branch = make_branch_name(change_type, ticket, slug)
        return {"ok": True, "summary": f"branch name: {branch}", "details": {"branch": branch}}

    if tool_name == "ananke.git.commit":
        message = str(args.get("message", ""))
        if not message:
            return {"ok": False, "summary": "message required", "details": {}}
        import subprocess

        result = subprocess.run(  # noqa: S603
            ["git", "commit", "--allow-empty", "-m", message],
            cwd=str(repository_root),
            capture_output=True,
            text=True,
            check=False,
        )
        ok = result.returncode == 0
        return {
            "ok": ok,
            "summary": "commit created" if ok else "commit failed",
            "details": {
                "exit_code": result.returncode,
                "output": (result.stdout + result.stderr)[:500],
            },
        }

    if tool_name == "ananke.lifecycle.transition_issue":
        ticket = str(args.get("ticket", ""))
        transition = str(args.get("transition", ""))
        if not ticket or not transition:
            return {"ok": False, "summary": "ticket and transition required", "details": {}}
        return app.lifecycle_issue_transition(ticket=ticket, transition=transition).model_dump()

    if tool_name == "ananke.lifecycle.create_pr":
        title = str(args.get("title", ""))
        branch = str(args.get("branch", ""))
        if not title or not branch:
            return {"ok": False, "summary": "title and branch required", "details": {}}
        return app.lifecycle_pr_create(title=title, branch=branch).model_dump()

    return {"ok": False, "summary": "unsupported tool", "details": {"tool": tool_name}}
