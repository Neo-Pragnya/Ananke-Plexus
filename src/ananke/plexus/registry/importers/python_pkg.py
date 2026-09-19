"""Python package importer — static metadata only (spec §36, §37).

Reads ``*.dist-info/METADATA``, ``entry_points.txt``, ``pyproject.toml`` and shipped Ananke
manifests. **It never imports the package**; callable-level metadata needs the sandboxed
dynamic introspection path instead.
"""

from __future__ import annotations

import importlib.metadata as md
import re
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
    MANIFEST_NAMES,
    now_iso,
    tree_fingerprint,
)
from ananke.plexus.registry.importers.manifest import ManifestImporter
from ananke.plexus.registry.models import (
    ArtifactKind,
    ImporterInfo,
    Provenance,
    Publisher,
)
from ananke.plexus.registry.semver import normalize_version

_GROUPS = {
    "ananke.skills": ArtifactKind.SKILL,
    "ananke.agents": ArtifactKind.AGENT,
    "ananke.tools": ArtifactKind.TOOL,
}
_CLASSIFIER_LICENSES = {
    "License :: OSI Approved :: Apache Software License": "Apache-2.0",
    "License :: OSI Approved :: MIT License": "MIT",
    "License :: OSI Approved :: BSD License": "BSD-3-Clause",
    "License :: OSI Approved :: ISC License (ISCL)": "ISC",
    "License :: OSI Approved :: Mozilla Public License 2.0 (MPL 2.0)": "MPL-2.0",
    "License :: OSI Approved :: GNU General Public License v3 (GPLv3)": "GPL-3.0-only",
    "License :: OSI Approved :: GNU Affero General Public License v3": "AGPL-3.0-only",
}
_REQ_RE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[^\]]*\])?\s*(\([^)]*\)|[<>=!~][^;]*)?")


def _license_from(meta: Any) -> str | None:
    expr = meta.get("License-Expression")
    if expr:
        return str(expr)
    for classifier in meta.get_all("Classifier") or []:
        if classifier in _CLASSIFIER_LICENSES:
            return _CLASSIFIER_LICENSES[classifier]
    lic = meta.get("License")
    if lic and len(str(lic)) < 60 and "\n" not in str(lic):
        return str(lic)
    return None


def _requires(dist_requires: list[str] | None) -> list[dict[str, Any]]:
    deps: list[dict[str, Any]] = []
    for raw in dist_requires or []:
        if "extra ==" in raw:
            continue  # optional extras are not hard dependencies
        m = _REQ_RE.match(raw)
        if not m:
            continue
        spec = (m.group(2) or "").strip("() ").strip()
        deps.append({"type": "python-package", "name": m.group(1).lower(), "version": spec or None})
    return deps


class PythonImporter:
    id: ClassVar[str] = "python-metadata"
    version: ClassVar[str] = "1"
    static: ClassVar[bool] = True
    permissions: ClassVar[ImporterPermissions] = ImporterPermissions()

    def probe(self, source: Source, ctx: InspectionContext) -> ProbeResult:
        if source.scheme == "python":
            return ProbeResult(ok=True, confidence=1.0, reason="python: source")
        if source.scheme == "path" and source.path.is_dir():
            pyproject = source.path / "pyproject.toml"
            if pyproject.is_file():
                try:
                    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
                except (tomllib.TOMLDecodeError, OSError):
                    return ProbeResult(ok=False, reason="unreadable pyproject.toml")
                eps = data.get("project", {}).get("entry-points", {})
                if "ananke" in data.get("tool", {}) or any(g in eps for g in _GROUPS):
                    return ProbeResult(
                        ok=True, confidence=0.8, reason="pyproject declares Ananke metadata"
                    )
            if list(source.path.glob("*.dist-info")):
                return ProbeResult(ok=True, confidence=0.8, reason="dist-info directory")
        return ProbeResult(ok=False, reason="not a python package source")

    # ------------------------------------------------------------------ inspect
    def inspect(self, source: Source, ctx: InspectionContext) -> list[Candidate]:
        target = source.target
        path = Path(target).expanduser()
        if source.scheme == "python" and not path.exists():
            return self._from_installed(target, ctx)
        if path.is_dir():
            if list(path.glob("*.dist-info")):
                return self._from_dist_info(next(iter(path.glob("*.dist-info"))), ctx)
            return self._from_project(path, ctx)
        raise ImporterError(f"cannot read python source {target!r}")

    def _from_installed(self, name: str, ctx: InspectionContext) -> list[Candidate]:
        try:
            dist = md.distribution(name)
        except md.PackageNotFoundError as exc:
            raise ImporterError(f"python distribution {name!r} is not installed") from exc
        meta = dist.metadata
        info = {
            "name": str(meta["Name"]),
            "version": str(meta["Version"]),
            "summary": str(meta.get("Summary", "")),
            "license": _license_from(meta),
            "requires": _requires(dist.requires),
            "home": next(
                (
                    v.split(",", 1)[-1].strip()
                    for v in meta.get_all("Project-URL") or []
                    if "source" in v.lower() or "repo" in v.lower()
                ),
                None,
            ),
            "author": meta.get("Author") or meta.get("Author-email"),
        }
        manifest_dirs: list[Path] = []
        for f in dist.files or []:
            parts = Path(str(f)).parts
            if len(parts) <= 4 and parts[-1] in MANIFEST_NAMES and ".dist-info" not in str(f):
                manifest_dirs.append(Path(str(dist.locate_file(f))).parent)
        entries = [(ep.group, ep.name, ep.value) for ep in dist.entry_points if ep.group in _GROUPS]
        return self._build(info, sorted(set(manifest_dirs)), entries, ctx, source_url=None)

    def _from_dist_info(self, dist_info: Path, ctx: InspectionContext) -> list[Candidate]:
        dist = md.PathDistribution(dist_info)
        meta = dist.metadata
        info = {
            "name": str(meta["Name"]),
            "version": str(meta["Version"]),
            "summary": str(meta.get("Summary", "")),
            "license": _license_from(meta),
            "requires": _requires(dist.requires),
            "home": None,
            "author": meta.get("Author"),
        }
        entries = [(ep.group, ep.name, ep.value) for ep in dist.entry_points if ep.group in _GROUPS]
        root = dist_info.parent
        manifest_dirs = sorted(
            {
                p.parent
                for name in MANIFEST_NAMES
                for pat in ("*/{}", "*/*/{}")
                for p in root.glob(pat.format(name))
            }
        )
        return self._build(info, manifest_dirs, entries, ctx, source_url=None)

    def _from_project(self, path: Path, ctx: InspectionContext) -> list[Candidate]:
        pyproject = path / "pyproject.toml"
        if not pyproject.is_file():
            raise ImporterError(f"no pyproject.toml in {path}")
        try:
            data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as exc:
            raise ImporterError(f"invalid pyproject.toml: {exc}") from exc
        project = data.get("project", {})
        lic = project.get("license")
        info = {
            "name": str(project.get("name", path.name)),
            "version": str(project.get("version", "")),
            "summary": str(project.get("description", "")),
            "license": lic.get("text") if isinstance(lic, dict) else (str(lic) if lic else None),
            "requires": _requires([str(d) for d in project.get("dependencies", [])]),
            "home": (project.get("urls", {}) or {}).get("Repository"),
            "author": None,
        }
        eps = project.get("entry-points", {})
        entries = [(g, n, str(v)) for g in _GROUPS for n, v in (eps.get(g, {}) or {}).items()]
        manifest_dirs = sorted(
            {p.parent for name in MANIFEST_NAMES for p in path.glob(f"*/{name}")}
            | {p.parent for name in MANIFEST_NAMES for p in path.glob(f"src/*/{name}")}
            | {p.parent for name in MANIFEST_NAMES for p in path.glob(f"src/*/*/{name}")}
        )
        overrides = (data.get("tool", {}) or {}).get("ananke", {}).get("registry", {})
        return self._build(info, manifest_dirs, entries, ctx, source_url=None, overrides=overrides)

    def _build(
        self,
        info: dict[str, Any],
        manifest_dirs: list[Path],
        entries: list[tuple[str, str, str]],
        ctx: InspectionContext,
        *,
        source_url: str | None,
        overrides: dict[str, Any] | None = None,
    ) -> list[Candidate]:
        pkg, pkg_version = info["name"], info["version"]
        candidates: list[Candidate] = []
        provenance_base = {
            "source_type": "python-package",
            "source_name": pkg,
            "source_version": pkg_version,
            "source_url": info.get("home") or source_url,
            "importer": ImporterInfo(id=self.id, version=self.version),
            "publisher": Publisher(name=str(info["author"])) if info.get("author") else None,
            "discovered_at": now_iso(),
        }
        pkg_dep = {"type": "python-package", "name": pkg.lower(), "version": f"=={pkg_version}"}
        for directory in manifest_dirs:
            for cand in ManifestImporter().inspect(
                Source(str(directory), "path", str(directory)), ctx
            ):
                cand.draft.setdefault(
                    "license", {"expression": info["license"], "source": "package-metadata"}
                )
                deps = cand.draft.setdefault("dependencies", [])
                if not any(isinstance(d, dict) and d.get("name") == pkg.lower() for d in deps):
                    deps.append(pkg_dep)
                cand.provenance = Provenance(
                    **provenance_base,
                    native_id=cand.provenance.native_id,
                    fingerprint=cand.provenance.fingerprint,
                )
                cand.note(
                    "info", "packaged-manifest", f"manifest shipped inside {pkg} {pkg_version}"
                )
                candidates.append(cand)
        if candidates:
            return candidates
        for group, ep_name, value in entries:
            kind = _GROUPS[group]
            try:
                norm = str(normalize_version(pkg_version))
            except Exception:
                norm = ""
            draft: dict[str, Any] = {
                "kind": kind.value,
                "name": slugify(ep_name),
                "version": norm,
                "summary": info["summary"],
                "description": f"Declared by {pkg} {pkg_version} via entry point {group}: {ep_name} = {value}",
                "license": {"expression": info["license"], "source": "package-metadata"},
                "dependencies": [
                    pkg_dep,
                    *[d for d in info["requires"] if d["name"] != pkg.lower()],
                ],
                "runtime": {"supported": ["generic-python"]},
            }
            if overrides:
                draft.update(
                    {
                        k: v
                        for k, v in overrides.items()
                        if k in {"namespace", "capabilities", "permissions", "runtime"}
                    }
                )
            cand = Candidate(
                draft=draft,
                files={
                    "entry-point.json": (
                        f'{{"group": "{group}", "name": "{ep_name}", "value": "{value}", '
                        f'"package": "{pkg}", "package_version": "{pkg_version}"}}\n'
                    ).encode()
                },
                source=f"python:{pkg}",
            )
            cand.provenance = Provenance(**provenance_base, native_id=f"{group}:{ep_name}")
            cand.provenance.fingerprint = tree_fingerprint(cand.files) + f"|{pkg}=={pkg_version}"
            cand.note(
                "info",
                "static-entry-point",
                "entry point discovered from package metadata; callable schemas/tools need "
                "dynamic introspection (disabled unless explicitly enabled)",
            )
            cand.note(
                "warning",
                "no-permissions",
                "package metadata cannot declare permissions; add them explicitly",
                "permissions",
            )
            candidates.append(cand)
        if not candidates:
            raise ImporterError(
                f"{pkg} declares no Ananke manifest or entry points "
                f"({', '.join(_GROUPS)}); nothing to learn statically. Use dynamic "
                "introspection with a framework plugin (--allow-dynamic) if appropriate."
            )
        return candidates
