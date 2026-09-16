"""Spec lifecycle service built on provider adapters."""

from pathlib import Path

from ananke.plexus.specs.lock import detect_drift, write_spec_lock
from ananke.plexus.specs.models import Requirement
from ananke.plexus.specs.providers.registry import resolve_spec_provider


def create_spec_bundle(repository_root: Path, requirement: Requirement, provider_name: str) -> Path:
    provider = resolve_spec_provider(provider_name)
    return provider.create(repository_root, requirement)


def plan_spec(feature_dir: Path, provider_name: str) -> Path:
    provider = resolve_spec_provider(provider_name)
    return provider.plan(feature_dir)


def create_tasks(feature_dir: Path, provider_name: str) -> Path:
    provider = resolve_spec_provider(provider_name)
    return provider.tasks(feature_dir)


def lock_spec(feature_dir: Path) -> Path:
    return write_spec_lock(feature_dir)


def validate_spec(feature_dir: Path) -> tuple[bool, list[str]]:
    errors: list[str] = []
    required_files = (
        "requirement.md",
        "spec.md",
        "plan.md",
        "tasks.md",
        "bmad.yaml",
        "spec.lock.json",
    )
    for required in required_files:
        if not (feature_dir / required).exists():
            errors.append(f"MISSING:{required}")
    drift = detect_drift(feature_dir)
    if drift and drift != ["LOCK_MISSING"]:
        errors.extend(drift)
    return len(errors) == 0, errors


def diff_spec(feature_dir: Path) -> list[str]:
    return detect_drift(feature_dir)


def load_requirement_from_dir(feature_dir: Path) -> "Requirement | None":
    """Load a Requirement from the requirement.md in a feature dir (best-effort parse)."""
    req_path = feature_dir / "requirement.md"
    if not req_path.exists():
        return None
    text = req_path.read_text(encoding="utf-8")
    lines = text.splitlines()
    title = ""
    req_id = feature_dir.name
    body_lines: list[str] = []
    criteria: list[str] = []
    in_ac = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("# "):
            title = stripped[2:].strip()
        elif stripped.startswith("**ID:**") or stripped.startswith("ID:"):
            req_id = stripped.split(":", 1)[-1].strip(" *")
        elif "acceptance criteria" in stripped.lower() or "acceptance_criteria" in stripped.lower():
            in_ac = True
        elif in_ac and stripped.startswith("- "):
            criteria.append(stripped[2:].strip())
        elif stripped and not in_ac:
            body_lines.append(stripped)

    return Requirement(
        requirement_id=req_id,
        title=title or req_id,
        body=" ".join(body_lines) or title or req_id,
        acceptance_criteria=criteria,
    )
