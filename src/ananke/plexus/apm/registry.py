"""APM local registry helpers."""

from pathlib import Path


def active_skills_dir(repository_root: Path) -> Path:
    path = repository_root / ".ananke" / "skills" / "active"
    path.mkdir(parents=True, exist_ok=True)
    return path


def installed_skills_dir(repository_root: Path) -> Path:
    path = repository_root / ".ananke" / "skills" / "installed"
    path.mkdir(parents=True, exist_ok=True)
    return path


def list_installed_skills(repository_root: Path) -> list[str]:
    path = installed_skills_dir(repository_root)
    return sorted(item.name for item in path.iterdir() if item.is_dir())


def activate_skill(repository_root: Path, installed_name: str) -> Path:
    installed_path = installed_skills_dir(repository_root) / installed_name
    if not installed_path.exists():
        raise FileNotFoundError(f"Installed skill not found: {installed_name}")
    marker = active_skills_dir(repository_root) / f"{installed_name}.active"
    marker.write_text(installed_name + "\n", encoding="utf-8")
    return marker


def deactivate_skill(repository_root: Path, installed_name: str) -> Path:
    marker = active_skills_dir(repository_root) / f"{installed_name}.active"
    if marker.exists():
        marker.unlink()
    return marker


def list_active_skills(repository_root: Path) -> list[str]:
    path = active_skills_dir(repository_root)
    result: list[str] = []
    for item in sorted(path.glob("*.active")):
        result.append(item.stem)
    return result
