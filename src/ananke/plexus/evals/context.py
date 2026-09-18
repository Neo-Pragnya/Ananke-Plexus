"""EvaluationContext — carries project references into evaluators."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class EvaluationContext:
    project_root: Path
    policy_config: dict[str, Any] = field(default_factory=dict)
    calm_path: Path | None = None
    graph_snapshot: dict[str, Any] | None = None
    spec_cache: dict[str, Any] = field(default_factory=dict)
    adapter_config: dict[str, Any] = field(default_factory=dict)
    online_mode: bool = False
    allowed_network: bool = False

    @classmethod
    def from_project(cls, root: Path) -> EvaluationContext:
        calm_candidate = root / ".ananke" / "architecture" / "system.calm.json"
        return cls(
            project_root=root,
            calm_path=calm_candidate if calm_candidate.exists() else None,
        )
