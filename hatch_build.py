"""Hatchling build hook: bundle the MkDocs source into the wheel.

``ananke docs build`` / ``ananke docs serve`` need the Markdown, images and ``mkdocs.yml``
inside the *installed* package so the full documentation site can be rendered fully offline —
no PyPI or GitHub access needed beyond the initial ``pip install``. Plain ``force-include``
copies a whole directory verbatim, but ``[tool.hatch.build] exclude`` patterns are not applied
to force-included paths, so we build the file list ourselves (also lets us skip editor/OS
artifacts like ``.DS_Store`` that would otherwise tag along).

Images under ``docs/assets/`` are pre-compressed JPEGs (~300-400 KB each, resized and
re-encoded from the ~2 MB PNG originals — see the image generation note in CHANGELOG.md for
0.3.3) specifically so they're small enough to always bundle; nothing here treats them
specially anymore.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

_DEST_PREFIX = "ananke/plexus/_docsite"


class DocsSiteBuildHook(BuildHookInterface):  # type: ignore[misc]
    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        root = Path(self.root)
        docs_src = root / "docs"
        mkdocs_yml = root / "mkdocs.yml"
        if not docs_src.is_dir() or not mkdocs_yml.is_file():
            return  # sdist built from an archive without the docs tree: skip quietly

        force_include: dict[str, str] = build_data.setdefault("force_include", {})
        force_include[str(mkdocs_yml)] = f"{_DEST_PREFIX}/mkdocs.yml"
        for path in sorted(docs_src.rglob("*")):
            if path.is_dir():
                continue
            rel = path.relative_to(root)
            if any(part.startswith(".") for part in rel.parts):
                continue  # editor/OS artifacts (.DS_Store, ...), never part of the site
            force_include[str(path)] = f"{_DEST_PREFIX}/{rel.as_posix()}"
