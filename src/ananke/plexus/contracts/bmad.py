"""Ananke BMAD contract compiler — coordinates behavior, model, and architecture compilation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from ananke.plexus.specs.models import Requirement


def compile_bmad(
    feature_dir: Path,
    requirement: Requirement,
    *,
    api_contract: dict[str, Any] | None = None,
    calm: dict[str, Any] | None = None,
) -> Path:
    """Compile all BMAD contracts and write bmad.yaml index.

    With an API contract and a CALM model the model and architecture contracts name real operations, schemas and
    components; without them they're skeletons to fill in. Returns the path to the written bmad.yaml.
    """
    from ananke.plexus.contracts.architecture import compile_architecture
    from ananke.plexus.contracts.behavior import compile_behavior, compile_gherkin
    from ananke.plexus.contracts.model import compile_model

    behavior_path = compile_behavior(feature_dir, requirement)
    gherkin_path = compile_gherkin(feature_dir, requirement)
    model_path = compile_model(feature_dir, requirement, api_contract=api_contract)
    arch_path = compile_architecture(feature_dir, requirement, calm=calm)
    model = yaml.safe_load(model_path.read_text(encoding="utf-8")) or {}
    arch = yaml.safe_load(arch_path.read_text(encoding="utf-8")) or {}
    schema_names = [str(s.get("name")) for s in model.get("schemas") or [] if isinstance(s, dict)]
    components = [
        str(c.get("unique_id")) for c in arch.get("components") or [] if isinstance(c, dict)
    ]

    scenarios: list[str] = []
    for idx, criterion in enumerate(requirement.acceptance_criteria, start=1):
        scenarios.extend(
            [
                f"    - id: AC-{idx}",
                f"      acceptance_id: AC-{requirement.requirement_id}-{idx}",
                f"      description: {json.dumps(criterion, ensure_ascii=False)}",
            ]
        )

    lines = [
        f"# Ananke BMAD Contract — {requirement.requirement_id}",
        "",
        "behavior:",
        "  scenarios:",
        *scenarios,
        f"  test_skeleton: {behavior_path.relative_to(feature_dir)}",
        f"  gherkin: {gherkin_path.relative_to(feature_dir)}",
        "  evidence_required:",
        "    - pytest_results",
        "    - coverage_report",
        "",
        "model:",
        f"  contract: {model_path.relative_to(feature_dir)}",
        *_schema_lines(schema_names if api_contract is not None else ["requirement_boundary_v1"]),
        "  compatibility_policy: backward_compatible",
        "",
        "architecture:",
        f"  contract: {arch_path.relative_to(feature_dir)}",
        "  calm_ref: .ananke/architecture/system.calm.json",
        "  invariant_ids: []",
        f"  components: {json.dumps(components)}",
        "  denied_dependencies: []",
    ]

    bmad_path = feature_dir / "bmad.yaml"
    bmad_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return bmad_path


def _schema_lines(names: list[str]) -> list[str]:
    if not names:
        return ["  schemas: []"]
    return ["  schemas:", *(f"    - {json.dumps(n)}" for n in names)]
