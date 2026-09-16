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
    return layout
