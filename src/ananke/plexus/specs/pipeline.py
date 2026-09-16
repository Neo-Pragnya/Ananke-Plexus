"""End-to-end spec lifecycle orchestration."""

from pathlib import Path

from ananke.plexus.contracts.bmad import compile_bmad
from ananke.plexus.specs.lock import detect_drift, write_spec_lock
from ananke.plexus.specs.models import Requirement
from ananke.plexus.specs.providers.base import SpecProvider


def run_full_spec_pipeline(
    repository_root: Path,
    requirement: Requirement,
    provider: SpecProvider,
) -> dict[str, str]:
    feature_dir = provider.create(repository_root, requirement)
    plan_path = provider.plan(feature_dir)
    tasks_path = provider.tasks(feature_dir)
    bmad_path = compile_bmad(feature_dir, requirement)
    lock_path = write_spec_lock(feature_dir)
    drift = detect_drift(feature_dir)

    return {
        "provider": provider.provider_id,
        "feature_dir": str(feature_dir),
        "plan": str(plan_path),
        "tasks": str(tasks_path),
        "bmad": str(bmad_path),
        "lock": str(lock_path),
        "drift": "none" if not drift else ",".join(drift),
    }
