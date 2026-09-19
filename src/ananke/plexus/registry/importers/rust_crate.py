"""Rust crate importer — static ``Cargo.toml`` + ``ananke.registry.json`` (spec §38)."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Any, ClassVar

from ananke.plexus.registry.errors import ImporterError
from ananke.plexus.registry.importers.base import (
    Candidate,
    ImporterPermissions,
    InspectionContext,
    ProbeResult,
    Source,
    slugify,
)
from ananke.plexus.registry.importers.common import (
    normalise_draft_types,
    now_iso,
    resolve_contracts,
    tree_fingerprint,
)
from ananke.plexus.registry.models import ImporterInfo, Provenance
from ananke.plexus.registry.payload import collect_directory
from ananke.plexus.registry.semver import normalize_version

REGISTRY_JSON = "ananke.registry.json"


class RustCrateImporter:
    id: ClassVar[str] = "rust-crate"
    version: ClassVar[str] = "1"
    static: ClassVar[bool] = True
    permissions: ClassVar[ImporterPermissions] = ImporterPermissions()

    def probe(self, source: Source, ctx: InspectionContext) -> ProbeResult:
        path = source.path
        if source.scheme not in {"rust", "path"} or not path.is_dir():
            return ProbeResult(ok=False, reason="not a directory")
        cargo = path / "Cargo.toml"
        if not cargo.is_file():
            return ProbeResult(ok=False, reason="no Cargo.toml")
        if source.scheme == "rust" or (path / REGISTRY_JSON).is_file():
            return ProbeResult(ok=True, confidence=0.85, reason="crate with registry metadata")
        return ProbeResult(
            ok=True, confidence=0.4, reason="bare Cargo crate (kind must be supplied)"
        )

    def inspect(self, source: Source, ctx: InspectionContext) -> list[Candidate]:
        path = source.path
        cargo_path = path / "Cargo.toml"
        if not cargo_path.is_file():
            raise ImporterError(f"no Cargo.toml in {path}")
        try:
            cargo = tomllib.loads(cargo_path.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as exc:
            raise ImporterError(f"invalid Cargo.toml: {exc}") from exc
        pkg = cargo.get("package")
        if not isinstance(pkg, dict):
            raise ImporterError("Cargo.toml has no [package] (workspace roots are not supported)")
        report = collect_directory(path)
        files = {k: v for k, v in report.files.items() if not k.startswith("target/")}
        cand = Candidate(draft={}, files=files, source=str(path))
        draft: dict[str, Any] = {}
        if REGISTRY_JSON in files:
            try:
                loaded = json.loads(files[REGISTRY_JSON].decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ImporterError(f"{REGISTRY_JSON} is not valid JSON: {exc}") from exc
            if not isinstance(loaded, dict):
                raise ImporterError(f"{REGISTRY_JSON} must contain an object")
            draft = loaded
            cand.note("info", "registry-json", f"read {REGISTRY_JSON}")
        meta = (pkg.get("metadata", {}) or {}).get("ananke", {})
        if isinstance(meta, dict):
            for key, value in meta.items():
                draft.setdefault(key, value)
        draft.setdefault("name", slugify(str(pkg.get("name", path.name))))
        version = draft.get("version") or pkg.get("version")
        if isinstance(version, str) and version:
            try:
                draft["version"] = str(normalize_version(version))
            except Exception:
                draft["version"] = version
        if pkg.get("description") and not draft.get("summary"):
            draft["summary"] = str(pkg["description"])
        if pkg.get("license") and not draft.get("license"):
            draft["license"] = {"expression": str(pkg["license"]), "source": "Cargo.toml"}
        deps = draft.setdefault("dependencies", [])
        for crate, spec in (cargo.get("dependencies", {}) or {}).items():
            req = (
                spec
                if isinstance(spec, str)
                else (spec.get("version") if isinstance(spec, dict) else None)
            )
            deps.append({"type": "rust-crate", "name": str(crate), "version": req})
        compat = draft.setdefault("compatibility", {})
        if pkg.get("rust-version"):
            compat.setdefault("rust", f">={pkg['rust-version']}")
        if not draft.get("kind"):
            cand.note(
                "warning",
                "unknown-kind",
                "crate does not declare an artifact kind; pass --kind",
                "kind",
            )
            if ctx.kind:
                draft["kind"] = ctx.kind.value
        normalise_draft_types(draft, cand)
        resolve_contracts(draft, files, cand)
        cand.draft = draft
        cand.provenance = Provenance(
            source_type="rust-crate",
            source_name=str(pkg.get("name", path.name)),
            source_version=str(pkg.get("version", "")) or None,
            source_url=str(pkg["repository"]) if pkg.get("repository") else None,
            importer=ImporterInfo(id=self.id, version=self.version),
            discovered_at=now_iso(),
            fingerprint=tree_fingerprint(files),
        )
        cand.note(
            "warning",
            "no-permissions",
            "crates cannot declare permissions in Cargo.toml; add them via registry metadata",
            "permissions",
        )
        return [cand]


__all__ = ["REGISTRY_JSON", "Path", "RustCrateImporter"]
