"""QualityGateConfig and threshold loading."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class QualityGateConfig(BaseModel):
    block_on_required_failure: bool = True
    block_on_error: bool = True
    warn_on_skipped: bool = False
    coverage_threshold: float | None = None
    mutation_score_threshold: float | None = None
    max_duration_seconds: int | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


def load_quality_config(project_root: Path | None = None) -> QualityGateConfig:
    """Load quality gate configuration from .ananke/quality.yaml if it exists."""
    if project_root is None:
        return QualityGateConfig()
    config_path = project_root / ".ananke" / "quality.yaml"
    if not config_path.exists():
        return QualityGateConfig()
    try:
        import yaml

        data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        gate_data = data.get("quality_gate", data)
        return QualityGateConfig(
            **{k: v for k, v in gate_data.items() if k in QualityGateConfig.model_fields}
        )
    except Exception:
        return QualityGateConfig()
