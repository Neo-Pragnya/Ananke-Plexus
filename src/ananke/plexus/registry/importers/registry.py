"""Importer selection and plugin discovery (spec §35, §172)."""

from __future__ import annotations

import importlib.metadata as md
from pathlib import Path
from typing import Any

from ananke.plexus.registry.errors import ImporterError
from ananke.plexus.registry.importers.base import (
    InspectionContext,
    RegistryImporter,
    Source,
    parse_source,
)
from ananke.plexus.registry.importers.common import (
    LEGACY_APM_MANIFEST,
    MANIFEST_NAMES,
    find_candidate_dirs,
)
from ananke.plexus.registry.importers.filesystem import FilesystemImporter
from ananke.plexus.registry.importers.framework import DynamicImporter, FrameworkImporter
from ananke.plexus.registry.importers.manifest import ManifestImporter
from ananke.plexus.registry.importers.mcp import McpImporter
from ananke.plexus.registry.importers.python_pkg import PythonImporter
from ananke.plexus.registry.importers.rust_crate import RustCrateImporter
from ananke.plexus.registry.importers.vcs_archive import ArchiveImporter, GitImporter

_BY_SCHEME = {
    "python": "python-metadata",
    "rust": "rust-crate",
    "mcp": "mcp",
    "mcp-stdio": "mcp",
    "mcp-http": "mcp",
    "git": "git",
    "archive": "archive",
    "framework": "framework-static",
    "dynamic": "dynamic-introspection",
}


def builtin_importers() -> list[RegistryImporter]:
    return [
        ManifestImporter(),
        FilesystemImporter(),
        PythonImporter(),
        RustCrateImporter(),
        McpImporter(),
        GitImporter(),
        ArchiveImporter(),
        FrameworkImporter(),
        DynamicImporter(),
    ]


def plugin_importers() -> tuple[list[RegistryImporter], list[tuple[str, str]]]:
    """Importers contributed via entry-point group ``ananke.registry.importers``."""
    loaded: list[RegistryImporter] = []
    failed: list[tuple[str, str]] = []
    for ep in md.entry_points().select(group="ananke.registry.importers"):
        try:
            obj: Any = ep.load()
            inst = obj() if isinstance(obj, type) else obj
            if not isinstance(inst, RegistryImporter):
                raise TypeError("does not implement the RegistryImporter protocol")
            loaded.append(inst)
        except Exception as exc:
            failed.append((ep.name, str(exc)))
    return loaded, failed


class ImporterRegistry:
    def __init__(
        self, importers: list[RegistryImporter] | None = None, *, load_plugins: bool = True
    ) -> None:
        self.failed: list[tuple[str, str]] = []
        self._importers = list(importers) if importers is not None else builtin_importers()
        if load_plugins and importers is None:
            plugins, self.failed = plugin_importers()
            self._importers.extend(plugins)

    def all(self) -> list[RegistryImporter]:
        return list(self._importers)

    def get(self, importer_id: str) -> RegistryImporter:
        for imp in self._importers:
            if imp.id == importer_id:
                return imp
        raise ImporterError(f"unknown importer {importer_id!r}")

    def availability(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = [
            {
                "id": i.id,
                "version": i.version,
                "static": i.static,
                "executes_code": i.permissions.executes_code,
                "network": i.permissions.network,
                "available": True,
            }
            for i in self._importers
        ]
        rows += [{"id": n, "available": False, "error": e} for n, e in self.failed]
        return rows

    def _best(self, source: Source, ctx: InspectionContext) -> RegistryImporter:
        scored = []
        for imp in self._importers:
            probe = imp.probe(source, ctx)
            if probe.ok:
                scored.append((probe.confidence, imp))
        if not scored:
            raise ImporterError(f"no importer recognises source {source.raw!r}")
        scored.sort(key=lambda t: -t[0])
        return scored[0][1]

    def plan(
        self, source: Source | str, ctx: InspectionContext
    ) -> list[tuple[RegistryImporter, Source]]:
        src = parse_source(source) if isinstance(source, str) else source
        if src.scheme in _BY_SCHEME:
            return [(self.get(_BY_SCHEME[src.scheme]), src)]
        path = src.path
        if not path.exists():
            raise ImporterError(f"source not found: {src.raw}")
        if path.is_file():
            return [(self._best(src, ctx), src)]
        return self.plan_path(path, ctx)

    def plan_path(
        self, path: Path, ctx: InspectionContext
    ) -> list[tuple[RegistryImporter, Source]]:
        dirs = find_candidate_dirs(path) or [path]
        plan: list[tuple[RegistryImporter, Source]] = []
        for d in dirs:
            src = Source(str(d), "path", str(d))
            plan.append((self._best(src, ctx), src))
        return plan


_default: ImporterRegistry | None = None


def default_registry() -> ImporterRegistry:
    global _default
    if _default is None:
        _default = ImporterRegistry()
    return _default


def plan_path(path: Path, ctx: InspectionContext) -> list[tuple[RegistryImporter, Source]]:
    return default_registry().plan_path(path, ctx)


__all__ = [
    "LEGACY_APM_MANIFEST",
    "MANIFEST_NAMES",
    "ImporterRegistry",
    "builtin_importers",
    "default_registry",
    "plan_path",
    "plugin_importers",
]
