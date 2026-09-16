"""Spec Kit provider adapter.

This adapter calls a user-installed Spec Kit CLI if present.
"""

import shutil
import subprocess
from pathlib import Path

from ananke.plexus.specs.models import Requirement

from .base import SpecProvider
from .native import NativeSpecProvider


class SpecKitProvider(SpecProvider):
    provider_id = "speckit"

    def __init__(self) -> None:
        self._native = NativeSpecProvider()

    def available(self) -> tuple[bool, str]:
        if shutil.which("specify"):
            return True, "found specify command"
        if shutil.which("spec-kit"):
            return True, "found spec-kit command"
        return False, "Spec Kit CLI not found (expected specify or spec-kit)"

    def create(self, repository_root: Path, requirement: Requirement) -> Path:
        feature_dir = self._native.create(repository_root, requirement)
        available, _ = self.available()
        if not available:
            return feature_dir

        self._run_optional_spec_kit(repository_root, "specify", feature_dir, requirement)
        return feature_dir

    def plan(self, feature_dir: Path) -> Path:
        plan_md = self._native.plan(feature_dir)
        self._run_optional_spec_kit(
            feature_dir.parents[2],
            "plan",
            feature_dir,
            None,
        )
        return plan_md

    def tasks(self, feature_dir: Path) -> Path:
        tasks_md = self._native.tasks(feature_dir)
        self._run_optional_spec_kit(
            feature_dir.parents[2],
            "tasks",
            feature_dir,
            None,
        )
        return tasks_md

    def _run_optional_spec_kit(
        self,
        repository_root: Path,
        action: str,
        feature_dir: Path,
        requirement: Requirement | None,
    ) -> None:
        binary = shutil.which("specify") or shutil.which("spec-kit")
        if not binary:
            return

        prompt = ""
        if requirement is not None:
            prompt = (
                f"{requirement.requirement_id}: {requirement.title}\n"
                f"{requirement.body}\n"
                + "\n".join(f"- {item}" for item in requirement.acceptance_criteria)
            )

        cmd = [binary, action]
        if prompt:
            cmd.extend(["--input", prompt])

        subprocess.run(  # noqa: S603
            cmd,
            cwd=repository_root,
            check=False,
            capture_output=True,
            text=True,
        )

        marker = feature_dir / "speckit.adapter.log"
        marker.write_text(
            f"action={action}\nbinary={binary}\n",
            encoding="utf-8",
        )
