"""Behavior contract compiler (D5).

Converts acceptance criteria into executable pytest skeletons and Gherkin scenarios.
Every generated test records its traceability:
  Requirement -> Acceptance Criterion -> Scenario -> Test ID
"""

from __future__ import annotations

import re
from pathlib import Path

from ananke.plexus.specs.models import Requirement


def _safe_id(text: str) -> str:
    return re.sub(r"[^a-z0-9_]", "_", text.lower().strip())[:60]


def compile_behavior(feature_dir: Path, requirement: Requirement) -> Path:
    """Generate a pytest skeleton file for each acceptance criterion."""
    tests_dir = feature_dir / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)

    req_id = requirement.requirement_id
    lines = [
        '"""Behavior contract tests generated from requirement.',
        "",
        f"Requirement: {req_id} — {requirement.title}",
        '"""',
        "",
        "import pytest",
        "",
        "",
    ]

    for idx, criterion in enumerate(requirement.acceptance_criteria, start=1):
        test_id = f"test_ac_{idx}_{_safe_id(criterion)}"
        lines += [
            f"# Traceability: {req_id} -> AC-{idx} -> {test_id}",
            f"@pytest.mark.requirement('{req_id}')",
            f"@pytest.mark.ac('{idx}')",
            f"def {test_id}() -> None:",
            f'    """AC-{idx}: {criterion}"""',
            "    # TODO: implement acceptance criterion assertion",
            "    pytest.fail('not implemented')",
            "",
            "",
        ]

    out_path = tests_dir / f"test_{_safe_id(req_id)}_behavior.py"
    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path


def compile_gherkin(feature_dir: Path, requirement: Requirement) -> Path:
    """Generate a Gherkin feature file from acceptance criteria."""
    req_id = requirement.requirement_id
    lines = [
        f"Feature: {requirement.title}",
        f"  # Requirement: {req_id}",
        "",
    ]

    for idx, criterion in enumerate(requirement.acceptance_criteria, start=1):
        lines += [
            f"  Scenario: AC-{idx} — {criterion}",
            "    Given the system is in a known state",
            f"    When the condition for AC-{idx} is met",
            "    Then the system satisfies the acceptance criterion",
            "",
        ]

    out_path = feature_dir / f"{_safe_id(req_id)}.feature"
    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path
