"""Filesystem path helpers."""

from pathlib import Path

ANANKE_DIR = ".ananke"


def project_ananke_dir(repository_root: Path) -> Path:
    return repository_root / ANANKE_DIR


def ensure_project_layout(repository_root: Path) -> dict[str, Path]:
    base = project_ananke_dir(repository_root)
    layout = {
        "base": base,
        "secrets": base / "secrets",
        "policy": base / "policy",
        "architecture": base / "architecture",
        "specs": base / "specs",
        "graph": base / "graph",
        "skills": base / "skills",
        "skills_installed": base / "skills" / "installed",
        "skills_active": base / "skills" / "active",
        "runs": base / "runs",
        "evidence": base / "evidence",
        "state": base / "state",
        "tmp": base / "tmp",
    }
    for path in layout.values():
        path.mkdir(parents=True, exist_ok=True)
    ensure_runtime_gitignore(repository_root)
    return layout


# Machine-local and runtime state that must never be committed: adapter credentials, local overrides, run
# worktrees (nested git checkouts), scratch space and caches. Specs, policy, architecture, the graph and evidence
# stay trackable on purpose.
RUNTIME_IGNORES = ("secrets/", "config.local.toml", "runs/", "tmp/", "state/")


def ensure_runtime_gitignore(repository_root: Path) -> Path:
    """Write (or extend) ``.ananke/.gitignore`` so ``git add -A`` can never pick up credentials or run state."""
    path = project_ananke_dir(repository_root) / ".gitignore"
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    missing = [
        entry for entry in RUNTIME_IGNORES if entry not in {line.strip() for line in existing}
    ]
    if missing:
        header = (
            []
            if existing
            else ["# Written by Ananke: local and runtime state never belongs in git."]
        )
        path.write_text("\n".join([*existing, *header, *missing]) + "\n", encoding="utf-8")
    return path
