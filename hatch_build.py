"""Hatchling build hook: bundle the MkDocs source into the wheel (excluding images).

``ananke docs build`` / ``ananke docs serve`` need the Markdown + Mermaid content and
``mkdocs.yml`` inside the *installed* package so the full documentation site can be
rendered fully offline — no PyPI or GitHub access needed beyond the initial
``pip install``. Plain ``force-include`` copies a whole directory verbatim, and
``[tool.hatch.build] exclude`` patterns are not applied to force-included paths, so the
only way to leave out the large hero PNGs under ``docs/assets/`` (~17 MB, versus ~1 MB of
text) is to build the file list ourselves.

Docs that reference an excluded image degrade gracefully (a broken-image icon, nothing
else); ``ananke docs build --source PATH`` at a git checkout renders them too. The one
exception is the header/favicon logo used on *every* page: ``docs/assets/branding/
ananke-logo-offline.png`` is a pre-shrunk (256x256, ~110 KB) copy of the hosted logo,
checked into git for exactly this purpose, and is substituted in at the same path
``mkdocs.yml`` expects so the bundled site's chrome isn't broken everywhere.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

_EXCLUDED_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif"}
_DEST_PREFIX = "ananke/plexus/_docsite"
_LOGO_REL = "docs/assets/branding/ananke-logo.png"
_LOGO_SOURCE_REL = "docs/assets/branding/ananke-logo-offline.png"


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
            rel = path.relative_to(root)
            if path.is_dir() or path.suffix.lower() in _EXCLUDED_SUFFIXES:
                continue
            if any(part.startswith(".") for part in rel.parts):
                continue  # editor/OS artifacts (.DS_Store, ...), never part of the site
            if rel.as_posix() == _LOGO_SOURCE_REL:
                continue  # bundled under the logo's real path instead, see below
            force_include[str(path)] = f"{_DEST_PREFIX}/{rel.as_posix()}"

        offline_logo = root / _LOGO_SOURCE_REL
        if offline_logo.is_file():
            force_include[str(offline_logo)] = f"{_DEST_PREFIX}/{_LOGO_REL}"
