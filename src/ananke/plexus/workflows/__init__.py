"""Named, reusable sequences of Ananke CLI commands ("workflows").

A workflow is a small TOML file listing steps to run in order — the same commands you'd
type by hand, bundled once and replayed with `ananke workflow run NAME`. See
`docs/guides/workflows.md` for the full guide.
"""

from ananke.plexus.workflows.models import Workflow, WorkflowStep
from ananke.plexus.workflows.runner import WorkflowRunResult, WorkflowStepResult, run_workflow
from ananke.plexus.workflows.storage import (
    delete_workflow,
    list_workflows,
    load_workflow,
    save_workflow,
    workflows_dir,
)

__all__ = [
    "Workflow",
    "WorkflowRunResult",
    "WorkflowStep",
    "WorkflowStepResult",
    "delete_workflow",
    "list_workflows",
    "load_workflow",
    "run_workflow",
    "save_workflow",
    "workflows_dir",
]
