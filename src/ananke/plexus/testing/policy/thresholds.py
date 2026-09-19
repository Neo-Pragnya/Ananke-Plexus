"""QualityGateConfig and threshold loading."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class QualityGateConfig(BaseModel):
    block_on_required_failure: bool = True
    block_on_error: bool = True
    warn_on_skipped: bool = False
    coverage_threshold: float | None = Field(default=None, ge=0, le=100)
    mutation_score_threshold: float | None = Field(default=None, ge=0, le=100)
    max_duration_seconds: int | None = Field(default=None, gt=0)
    extra: dict[str, Any] = Field(default_factory=dict)


class QualityConfigError(ValueError):
    """``.ananke/quality.yaml`` exists but cannot be honoured (fail closed, never ignore)."""


def load_quality_config(project_root: Path | None = None) -> QualityGateConfig:
    """Load quality gate configuration from .ananke/quality.yaml if it exists.

    A missing file yields the defaults. A file that is present but malformed or out of range
    raises :class:`QualityConfigError`, because silently dropping a configured threshold would
    let a failing build pass.
    """
    if project_root is None:
        return QualityGateConfig()
    config_path = project_root / ".ananke" / "quality.yaml"
    if not config_path.exists():
        return QualityGateConfig()
    import yaml

    try:
        data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            raise QualityConfigError(f"{config_path}: expected a mapping at the top level")
        gate_data = data.get("quality_gate", data)
        if not isinstance(gate_data, dict):
            raise QualityConfigError(f"{config_path}: `quality_gate` must be a mapping")
        return QualityGateConfig(
            **{k: v for k, v in gate_data.items() if k in QualityGateConfig.model_fields}
        )
    except QualityConfigError:
        raise
    except (yaml.YAMLError, ValueError) as exc:
        raise QualityConfigError(f"{config_path}: {exc}") from exc
