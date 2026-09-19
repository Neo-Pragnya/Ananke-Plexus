"""Generic filesystem-skill importer (spec §155, §156).

Recognises common non-Ananke layouts (``SKILL.md``, ``skill.yaml``, ``manifest.yaml``,
``AGENT.md``, tool schema files, prompt templates) and builds a *candidate*. Nothing is
assumed: whatever cannot be inferred is reported as a diagnostic for the human/CI to fill.
"""

from __future__ import annotations

import json
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
    GENERIC_MARKERS,
    check_references,
    detect_license_text,
    dir_display_name,
    find_candidate_dirs,
    find_manifest,
    first_paragraph,
    has_generic_marker,
    normalise_draft_types,
    now_iso,
    parse_structured,
    read_frontmatter,
    resolve_contracts,
    tree_fingerprint,
)
from ananke.plexus.registry.models import ImporterInfo, Provenance
from ananke.plexus.registry.payload import collect_directory


class FilesystemImporter:
    id: ClassVar[str] = "filesystem"
    version: ClassVar[str] = "1"
    static: ClassVar[bool] = True
    permissions: ClassVar[ImporterPermissions] = ImporterPermissions()

    def probe(self, source: Source, ctx: InspectionContext) -> ProbeResult:
        if source.scheme != "path" or not source.path.is_dir():
            return ProbeResult(ok=False, reason="not a directory")
        path = source.path
        if find_manifest(path) is not None:
            return ProbeResult(
                ok=False, reason="explicit Ananke manifest present (use manifest importer)"
            )
        if has_generic_marker(path):
            return ProbeResult(ok=True, confidence=0.6, reason="generic skill/agent markers found")
        if any(not find_manifest(d) for d in find_candidate_dirs(path)):
            return ProbeResult(
                ok=True, confidence=0.5, reason="child directories with skill markers"
            )
        return ProbeResult(
            ok=False, reason="no skill markers (SKILL.md, skill.yaml, manifest.yaml, AGENT.md)"
        )

    def inspect(self, source: Source, ctx: InspectionContext) -> list[Candidate]:
        dirs = [d for d in find_candidate_dirs(source.path) if find_manifest(d) is None]
        if not dirs:
            raise ImporterError(f"no generic skill layout found under {source.path}")
        return [self._inspect_dir(d, ctx) for d in dirs]

    def _inspect_dir(self, directory: Path, ctx: InspectionContext) -> Candidate:
        report = collect_directory(directory)
        files = report.files
        cand = Candidate(draft={}, files=files, source=str(directory))
        draft: dict[str, Any] = {}

        for marker in (
            "skill.yaml",
            "skill.yml",
            "manifest.yaml",
            "manifest.yml",
            "agent.yaml",
            "agent.yml",
        ):
            if marker in files:
                data = parse_structured(marker, files[marker])
                draft.update({k: v for k, v in data.items() if k in _ALLOWED_FIELDS})
                cand.note("info", "found-manifest-file", f"read fields from {marker}")
                break

        fm: dict[str, Any] = {}
        for md in ("SKILL.md", "AGENT.md"):
            if md in files:
                text = files[md].decode("utf-8", errors="replace")
                fm = read_frontmatter(text)
                if md == "AGENT.md" and not draft.get("kind"):
                    draft["kind"] = "agent"
                    draft.setdefault("instructions", {"ref": "AGENT.md"})
                if not draft.get("summary") and not fm.get("description"):
                    inferred = first_paragraph(text)
                    if inferred:
                        draft["summary"] = inferred
                        cand.note(
                            "info", "inferred-summary", f"summary inferred from {md}", "summary"
                        )
                break

        if not draft.get("kind"):
            draft["kind"] = (ctx.kind.value if ctx.kind else None) or "skill"
        if fm.get("name") and not draft.get("name"):
            draft["name"] = slugify(str(fm["name"]))
        if fm.get("description") and not draft.get("summary"):
            draft["summary"] = str(fm["description"]).strip()[:400]
        if fm.get("version") and not draft.get("version"):
            draft["version"] = str(fm["version"])
        if fm.get("license") and not draft.get("license"):
            draft["license"] = {"expression": str(fm["license"]), "source": "frontmatter"}
        if fm.get("allowed-tools"):
            cand.note(
                "warning",
                "unmapped-allowed-tools",
                "SKILL.md declares allowed-tools which are not mapped to registry permissions; "
                "declare permissions explicitly",
                "permissions",
            )

        if not draft.get("name"):
            draft["name"] = slugify(dir_display_name(directory))
            cand.note(
                "info", "inferred-name", f"name inferred from directory: {draft['name']}", "name"
            )
        if not draft.get("version"):
            draft["version"] = "0.1.0"
            cand.note(
                "warning", "suggested-version", "no version found; suggesting 0.1.0", "version"
            )

        license_value = draft.get("license")
        has_license = isinstance(license_value, str) or (
            isinstance(license_value, dict) and license_value.get("expression")
        )
        if isinstance(license_value, str):
            draft["license"] = {"expression": license_value, "source": "manifest"}
        if not has_license:
            for lic_file in ("LICENSE", "LICENSE.md", "LICENSE.txt", "COPYING"):
                if lic_file in files:
                    spdx = detect_license_text(files[lic_file].decode("utf-8", errors="replace"))
                    if spdx:
                        draft["license"] = {
                            "expression": spdx,
                            "source": f"file:{lic_file}",
                            "confidence": 0.7,
                        }
                        cand.note(
                            "info",
                            "inferred-license",
                            f"license {spdx} detected from {lic_file}",
                            "license",
                        )
                    break

        tools = _discover_tools(files)
        if tools and not draft.get("tools"):
            draft["tools"] = tools
        prompts = sorted(p for p in files if p.startswith("prompts/"))
        cand.note(
            "info",
            "discovered",
            f"found {len(tools)} callable tools, {len(prompts)} prompt templates",
        )
        if "README.md" in files:
            cand.note("info", "found-readme", "found README")
        if not draft.get("description") and "README.md" in files:
            draft["description"] = files["README.md"].decode("utf-8", errors="replace")[:4000]

        normalise_draft_types(draft, cand)
        resolve_contracts(draft, files, cand)
        check_references(draft, files, cand)
        for link in report.skipped_symlinks:
            cand.note("warning", "skipped-symlink", f"symlink not followed: {link}")
        cand.draft = draft
        cand.provenance = Provenance(
            source_type="filesystem",
            source_name=dir_display_name(directory),
            importer=ImporterInfo(id=self.id, version=self.version),
            discovered_at=now_iso(),
            fingerprint=tree_fingerprint(files),
        )
        return cand


_ALLOWED_FIELDS = {
    "kind",
    "namespace",
    "name",
    "version",
    "summary",
    "description",
    "capabilities",
    "tools",
    "inputs",
    "outputs",
    "runtime",
    "permissions",
    "dependencies",
    "compatibility",
    "license",
    "metadata",
    "skills",
    "policies",
    "evaluation",
    "instructions",
    "model_requirements",
    "owners",
    "maintainers",
}


def _discover_tools(files: dict[str, bytes]) -> list[dict[str, Any]]:
    tools: list[dict[str, Any]] = []
    for path in sorted(files):
        if not (path.startswith("tools/") and path.endswith(".json")):
            continue
        try:
            data = json.loads(files[path].decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict) or not isinstance(data.get("name"), str):
            continue
        tool: dict[str, Any] = {
            "name": data["name"],
            "description": str(data.get("description", "")),
        }
        schema = data.get("input_schema") or data.get("inputSchema") or data.get("parameters")
        if isinstance(schema, dict):
            tool["input_schema"] = schema
        out = data.get("output_schema") or data.get("outputSchema")
        if isinstance(out, dict):
            tool["output_schema"] = out
        tools.append(tool)
    return tools


__all__ = ["GENERIC_MARKERS", "FilesystemImporter"]
