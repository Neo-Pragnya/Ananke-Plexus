"""Importer for explicit Ananke manifests (spec §33): the highest-fidelity source."""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from ananke.plexus.registry.errors import ImporterError
from ananke.plexus.registry.importers.base import (
    Candidate,
    ImporterPermissions,
    InspectionContext,
    ProbeResult,
    Source,
)
from ananke.plexus.registry.importers.common import (
    LEGACY_APM_MANIFEST,
    check_references,
    dir_display_name,
    find_candidate_dirs,
    find_manifest,
    legacy_apm_to_draft,
    normalise_draft_types,
    now_iso,
    parse_structured,
    resolve_contracts,
    tree_fingerprint,
)
from ananke.plexus.registry.models import ImporterInfo, Provenance
from ananke.plexus.registry.payload import collect_directory


class ManifestImporter:
    id: ClassVar[str] = "ananke-manifest"
    version: ClassVar[str] = "1"
    static: ClassVar[bool] = True
    permissions: ClassVar[ImporterPermissions] = ImporterPermissions()

    def probe(self, source: Source, ctx: InspectionContext) -> ProbeResult:
        if source.scheme != "path":
            return ProbeResult(ok=False, reason="not a filesystem source")
        path = source.path
        if path.is_file() and path.name in {
            "ananke.toml",
            "ananke.yaml",
            "ananke.yml",
            "ananke.registry.json",
            LEGACY_APM_MANIFEST,
        }:
            return ProbeResult(ok=True, confidence=1.0, reason="explicit manifest file")
        if path.is_dir():
            manifest = find_manifest(path)
            if manifest is not None:
                conf = 0.9 if manifest.name == LEGACY_APM_MANIFEST else 1.0
                return ProbeResult(ok=True, confidence=conf, reason=f"found {manifest.name}")
            if any(find_manifest(d) for d in find_candidate_dirs(path)):
                return ProbeResult(
                    ok=True, confidence=0.95, reason="child directories with manifests"
                )
        return ProbeResult(ok=False, reason="no Ananke manifest found")

    def inspect(self, source: Source, ctx: InspectionContext) -> list[Candidate]:
        path = source.path
        roots = [path.parent] if path.is_file() else find_candidate_dirs(path)
        out = [self._inspect_dir(d) for d in roots if find_manifest(d) is not None]
        if not out:
            raise ImporterError(f"no Ananke manifest found under {path}")
        return out

    def _inspect_dir(self, directory: Path) -> Candidate:
        manifest_path = find_manifest(directory)
        assert manifest_path is not None  # noqa: S101 - guarded by caller
        report = collect_directory(directory)
        files = report.files
        data = parse_structured(manifest_path.name, files[manifest_path.name])
        cand = Candidate(draft={}, files=files, source=str(directory))
        if manifest_path.name == LEGACY_APM_MANIFEST:
            draft = legacy_apm_to_draft(data)
            cand.note("info", "legacy-manifest", "converted legacy APM ananke-skill.toml")
        else:
            draft = data
        normalise_draft_types(draft, cand)
        resolve_contracts(draft, files, cand)
        check_references(draft, files, cand)
        for link in report.skipped_symlinks:
            cand.note("warning", "skipped-symlink", f"symlink not followed: {link}")
        cand.draft = draft
        cand.provenance = Provenance(
            source_type="manifest",
            source_name=dir_display_name(directory),
            native_id=str(draft.get("ananke_id") or "") or None,
            importer=ImporterInfo(id=self.id, version=self.version),
            discovered_at=now_iso(),
            fingerprint=tree_fingerprint(files),
        )
        return cand
