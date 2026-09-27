"""Built-in workflow presets, installable with ``ananke workflow install-pack NAME``.

Mirrors the existing ``ananke policy install-pack`` UX: a preset is copied into the project
(`.ananke/workflows/<name>.toml`) so it can be inspected, edited and versioned like any other
workflow — nothing here runs automatically.
"""

from __future__ import annotations

from ananke.plexus.workflows.models import Workflow, WorkflowStep


def _step(description: str, *command: str, continue_on_error: bool = False) -> WorkflowStep:
    return WorkflowStep(
        description=description, command=list(command), continue_on_error=continue_on_error
    )


def _setup() -> Workflow:
    return Workflow(
        name="setup",
        description=(
            "Bootstrap a new project in one shot: workspace, diagnostics, a baseline policy "
            "pack, and git hooks."
        ),
        steps=[
            _step("Initialize the .ananke/ workspace", "ananke", "init", "--project", "${project}"),
            _step(
                "Run environment diagnostics",
                "ananke",
                "doctor",
                "--project",
                "${project}",
                continue_on_error=True,
            ),
            _step(
                "Install the baseline policy pack",
                "ananke",
                "policy",
                "install-pack",
                "baseline",
                "--project",
                "${project}",
            ),
            _step(
                "Install git hooks (pre-commit + pre-push)",
                "ananke",
                "hooks",
                "install",
                "all",
                "--project",
                "${project}",
            ),
        ],
    )


def _verify_all() -> Workflow:
    return Workflow(
        name="verify-all",
        description="Everything CI checks: diagnostics, verification gates, policy, registry.",
        steps=[
            _step(
                "Environment diagnostics",
                "ananke",
                "doctor",
                "--project",
                "${project}",
                continue_on_error=True,
            ),
            _step("Run verification gates", "ananke", "verify", "--project", "${project}"),
            _step(
                "Check policy (exits 3 if any rule blocks)",
                "ananke",
                "policy",
                "check",
                "--project",
                "${project}",
            ),
            _step(
                "Registry integrity (skipped if no registry)",
                "ananke",
                "registry",
                "verify",
                "--project",
                "${project}",
                continue_on_error=True,
            ),
        ],
    )


def _registry_bootstrap() -> Workflow:
    return Workflow(
        name="registry-bootstrap",
        description=(
            "Create a registry, learn a source, and lock it. Requires "
            "--set source=PATH --set namespace=NAME --set license=SPDX."
        ),
        steps=[
            _step("Create the registry", "ananke", "registry", "init", "--project", "${project}"),
            _step(
                "Learn the source",
                "ananke",
                "registry",
                "learn",
                "${source}",
                "--namespace",
                "${namespace}",
                "--license",
                "${license}",
                "--project",
                "${project}",
            ),
            _step(
                "Write ananke.lock",
                "ananke",
                "sync",
                "--project",
                "${project}",
            ),
        ],
    )


BUILTIN_PRESETS: dict[str, Workflow] = {
    w.name: w for w in (_setup(), _verify_all(), _registry_bootstrap())
}
