"""Import GitHub Copilot-style Agent Skills into APM format."""

import re
import shutil
from pathlib import Path

from ananke.plexus.apm.installer import install_local_skill


def _infer_name(skill_md: Path) -> str:
    heading = skill_md.read_text(encoding="utf-8").splitlines()[0].strip()
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", heading.lstrip("# ").strip().lower())
    return cleaned.strip("-") or "copilot-skill"


def import_copilot_skills(repository_root: Path, source_dir: Path) -> list[str]:
    installed: list[str] = []
    for skill_md in source_dir.rglob("SKILL.md"):
        name = _infer_name(skill_md)
        temp = repository_root / ".ananke" / "tmp" / f"import-{name}"
        if temp.exists():
            shutil.rmtree(temp)
        temp.mkdir(parents=True, exist_ok=True)

        shutil.copy2(skill_md, temp / "SKILL.md")
        (temp / "README.md").write_text(
            "Imported from Copilot skill format.\n",
            encoding="utf-8",
        )
        (temp / "ananke-skill.toml").write_text(
            "\n".join(
                [
                    "[skill]",
                    f'name = "{name}"',
                    'version = "0.1.0"',
                    'description = "Imported Copilot skill"',
                    'license = "UNKNOWN"',
                    "",
                    "[compatibility]",
                    'ananke = ">=0.1,<1"',
                    'skill_api = "1"',
                    "",
                    "[permissions]",
                    'filesystem_read = ["src/**", ".ananke/**"]',
                    'filesystem_write = [".ananke/evidence/**"]',
                    "network = []",
                    "shell = []",
                    "",
                    "[entrypoints]",
                    'instructions = "SKILL.md"',
                    "",
                    "[provenance]",
                    'source = "copilot"',
                    f'repository = "{source_dir}"',
                    'revision = "local"',
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        installed_name, _ = install_local_skill(repository_root, temp)
        installed.append(installed_name)

    return installed
