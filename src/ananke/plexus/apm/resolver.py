"""APM resolver for local paths and installed names."""

from pathlib import Path

from ananke.plexus.apm.registry import installed_skills_dir


class ResolveResult:
    def __init__(self, ok: bool, kind: str, value: str, reason: str = "") -> None:
        self.ok = ok
        self.kind = kind
        self.value = value
        self.reason = reason


def resolve_skill_reference(repository_root: Path, reference: str) -> ResolveResult:
    candidate = Path(reference)
    if candidate.exists() and candidate.is_dir():
        return ResolveResult(True, "path", str(candidate.resolve()))

    installed = installed_skills_dir(repository_root) / reference
    if installed.exists() and installed.is_dir():
        return ResolveResult(True, "installed", str(installed))

    return ResolveResult(False, "unknown", reference, "reference not found")
