"""Read/write workflows as ``.ananke/workflows/<name>.toml`` (deterministic, git-diffable)."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from ananke.plexus.registry.tomlw import dumps as toml_dumps
from ananke.plexus.workflows.models import Workflow


class WorkflowNotFoundError(FileNotFoundError):
    def __init__(self, name: str, directory: Path) -> None:
        super().__init__(f"no workflow named {name!r} in {directory}")
        self.name = name


def workflows_dir(project: Path) -> Path:
    return Path(project).resolve() / ".ananke" / "workflows"


def _path_for(project: Path, name: str) -> Path:
    return workflows_dir(project) / f"{name}.toml"


def list_workflows(project: Path) -> list[str]:
    directory = workflows_dir(project)
    if not directory.is_dir():
        return []
    return sorted(p.stem for p in directory.glob("*.toml"))


def load_workflow(project: Path, name: str) -> Workflow:
    path = _path_for(project, name)
    if not path.is_file():
        raise WorkflowNotFoundError(name, workflows_dir(project))
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    data.setdefault("name", name)
    return Workflow.model_validate(data)


def save_workflow(project: Path, workflow: Workflow, *, force: bool = False) -> Path:
    path = _path_for(project, workflow.name)
    if path.exists() and not force:
        raise FileExistsError(f"{path} already exists (pass force=True / --force to overwrite)")
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {"name": workflow.name, "description": workflow.description}
    payload["steps"] = [
        {
            "description": s.description,
            "command": s.command,
            "continue_on_error": s.continue_on_error,
        }
        for s in workflow.steps
    ]
    path.write_text(
        toml_dumps(payload, header=f"# Ananke workflow: {workflow.name}\n"), encoding="utf-8"
    )
    return path


def delete_workflow(project: Path, name: str) -> bool:
    path = _path_for(project, name)
    if not path.is_file():
        return False
    path.unlink()
    return True
