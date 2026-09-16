"""Native internal spec provider."""

from pathlib import Path

from ananke.plexus.specs.models import Requirement

from .base import SpecProvider


def _slugify(value: str) -> str:
    return "-".join(value.lower().strip().split())


class NativeSpecProvider(SpecProvider):
    provider_id = "native"

    def available(self) -> tuple[bool, str]:
        return True, "native provider available"

    def create(self, repository_root: Path, requirement: Requirement) -> Path:
        feature_name = f"{requirement.requirement_id}-{_slugify(requirement.title)}"
        feature_dir = repository_root / ".ananke" / "specs" / feature_name
        feature_dir.mkdir(parents=True, exist_ok=True)

        requirement_md = feature_dir / "requirement.md"
        requirement_md.write_text(
            "\n".join(
                [
                    f"# Requirement {requirement.requirement_id}",
                    "",
                    f"## Title\n{requirement.title}",
                    "",
                    f"## Body\n{requirement.body}",
                    "",
                    "## Acceptance Criteria",
                    *[f"- {item}" for item in requirement.acceptance_criteria],
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        spec_md = feature_dir / "spec.md"
        spec_md.write_text(
            "\n".join(
                [
                    f"# Spec for {requirement.requirement_id}",
                    "",
                    "## Scope",
                    "Implement the requirement with explicit, testable behavior.",
                    "",
                    "## Behavioral Contract",
                    *[f"- AC: {item}" for item in requirement.acceptance_criteria],
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        return feature_dir

    def plan(self, feature_dir: Path) -> Path:
        plan_md = feature_dir / "plan.md"
        if not plan_md.exists():
            plan_md.write_text(
                "\n".join(
                    [
                        "# Implementation Plan",
                        "",
                        "## Steps",
                        "1. Validate requirement and acceptance criteria.",
                        "2. Compile Ananke BMAD contracts.",
                        "3. Implement code changes with tests.",
                        "4. Run verify and finalize evidence.",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
        return plan_md

    def tasks(self, feature_dir: Path) -> Path:
        tasks_md = feature_dir / "tasks.md"
        if not tasks_md.exists():
            tasks_md.write_text(
                "\n".join(
                    [
                        "# Task Checklist",
                        "",
                        "- [ ] Create/adjust tests",
                        "- [ ] Implement requirement behavior",
                        "- [ ] Validate architecture impact",
                        "- [ ] Run verification gates",
                        "- [ ] Attach evidence bundle to review",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
        return tasks_md
