"""Ananke BMAD contract compiler — coordinates behavior, model, and architecture compilation."""

from __future__ import annotations

from pathlib import Path

from ananke.plexus.specs.models import Requirement


def compile_bmad(feature_dir: Path, requirement: Requirement) -> Path:
    """Compile all BMAD contracts and write bmad.yaml index.

    Returns the path to the written bmad.yaml.
    """
    from ananke.plexus.contracts.architecture import compile_architecture
    from ananke.plexus.contracts.behavior import compile_behavior, compile_gherkin
    from ananke.plexus.contracts.model import compile_model

    behavior_path = compile_behavior(feature_dir, requirement)
    gherkin_path = compile_gherkin(feature_dir, requirement)
    model_path = compile_model(feature_dir, requirement)
    arch_path = compile_architecture(feature_dir, requirement)

    scenarios: list[str] = []
    for idx, criterion in enumerate(requirement.acceptance_criteria, start=1):
        scenarios.extend(
            [
                f"    - id: AC-{idx}",
                f'      description: "{criterion}"',
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
        "  schemas:",
        "    - requirement_boundary_v1",
        "  compatibility_policy: backward_compatible",
        "",
        "architecture:",
        f"  contract: {arch_path.relative_to(feature_dir)}",
        "  calm_ref: .ananke/architecture/system.calm.json",
        "  invariant_ids: []",
        "  denied_dependencies: []",
    ]

    bmad_path = feature_dir / "bmad.yaml"
    bmad_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return bmad_path
