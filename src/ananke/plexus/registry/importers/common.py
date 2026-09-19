"""Shared importer helpers: manifest parsing, schema resolution, fingerprints, dir discovery."""

from __future__ import annotations

import json
import os
import re
import tomllib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from ananke.plexus.registry.errors import ManifestError, PayloadError
from ananke.plexus.registry.hashing import sha256_hex
from ananke.plexus.registry.importers.base import Candidate
from ananke.plexus.registry.payload import safe_relpath

MANIFEST_NAMES = ("ananke.toml", "ananke.yaml", "ananke.yml", "ananke.registry.json")
LEGACY_APM_MANIFEST = "ananke-skill.toml"
GENERIC_MARKERS = (
    "SKILL.md",
    "skill.yaml",
    "skill.yml",
    "manifest.yaml",
    "manifest.yml",
    "agent.yaml",
    "agent.yml",
    "AGENT.md",
)
_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".tox", "dist", "build"}

_LICENSE_HINTS: list[tuple[str, str]] = [
    (r"apache license[\s,]*version 2\.0", "Apache-2.0"),
    (r"mit license|permission is hereby granted, free of charge", "MIT"),
    (r"redistribution and use in source and binary forms.*neither the name", "BSD-3-Clause"),
    (r"redistribution and use in source and binary forms", "BSD-2-Clause"),
    (r"mozilla public license[\s,]*(?:version|v\.?)\s*2\.0", "MPL-2.0"),
    (r"gnu affero general public license", "AGPL-3.0-only"),
    (r"gnu lesser general public license", "LGPL-3.0-only"),
    (r"gnu general public license", "GPL-3.0-only"),
    (r"this is free and unencumbered software released into the public domain", "Unlicense"),
]


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def tree_fingerprint(files: dict[str, bytes]) -> str:
    lines = [f"{p}\0{sha256_hex(files[p])}" for p in sorted(files)]
    return "sha256:" + sha256_hex("\n".join(lines).encode())


def detect_license_text(text: str) -> str | None:
    head = " ".join(text[:4000].lower().split())
    for pattern, spdx in _LICENSE_HINTS:
        if re.search(pattern, head, re.DOTALL):
            return spdx
    return None


def find_manifest(directory: Path) -> Path | None:
    for name in (*MANIFEST_NAMES, LEGACY_APM_MANIFEST):
        candidate = directory / name
        if candidate.is_file():
            return candidate
    return None


def has_generic_marker(directory: Path) -> bool:
    return any((directory / m).is_file() for m in GENERIC_MARKERS)


def find_candidate_dirs(root: Path, max_depth: int = 3) -> list[Path]:
    """Directories under ``root`` (or ``root`` itself) that look like an artifact."""
    root = root.resolve()
    if find_manifest(root) is not None or has_generic_marker(root):
        return [root]
    found: list[Path] = []

    def walk(directory: Path, depth: int) -> None:
        if depth > max_depth:
            return
        try:
            children = sorted(p for p in directory.iterdir() if p.is_dir() and not p.is_symlink())
        except OSError:
            return
        for child in children:
            if child.name in _SKIP_DIRS or child.name.startswith("."):
                continue
            if find_manifest(child) is not None or has_generic_marker(child):
                found.append(child)
            else:
                walk(child, depth + 1)

    walk(root, 1)
    return found


def parse_structured(name: str, data: bytes) -> dict[str, Any]:
    try:
        text = data.decode("utf-8")
        if name.endswith(".toml"):
            parsed: Any = tomllib.loads(text)
        elif name.endswith((".yaml", ".yml")):
            parsed = yaml.safe_load(text)
        else:
            parsed = json.loads(text)
    except (
        UnicodeDecodeError,
        tomllib.TOMLDecodeError,
        yaml.YAMLError,
        json.JSONDecodeError,
    ) as exc:
        raise ManifestError(f"cannot parse {name}: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ManifestError(f"{name} must contain a mapping at the top level")
    return parsed


def legacy_apm_to_draft(data: dict[str, Any]) -> dict[str, Any]:
    """Map a legacy APM ``ananke-skill.toml`` onto the canonical manifest fields."""
    skill = data.get("skill", {}) or {}
    perms = data.get("permissions", {}) or {}
    compat = data.get("compatibility", {}) or {}
    prov = data.get("provenance", {}) or {}
    draft: dict[str, Any] = {
        "kind": "skill",
        "name": skill.get("name"),
        "version": str(skill.get("version", "")) or None,
        "summary": skill.get("description", ""),
        "license": {"expression": skill.get("license") or None, "source": "manifest"},
        "permissions": {
            "filesystem": {
                "read": list(perms.get("filesystem_read", [])),
                "write": list(perms.get("filesystem_write", [])),
            },
            "shell": {"allow": list(perms.get("shell", []))},
            "network": list(perms.get("network", [])),
        },
    }
    if compat.get("ananke"):
        draft["compatibility"] = {"ananke": compat["ananke"]}
    entry = (data.get("entrypoints", {}) or {}).get("instructions")
    if entry:
        draft["instructions"] = {"ref": entry}
    if prov.get("repository"):
        draft["metadata"] = {"repository": prov["repository"]}
    return draft


def _load_schema(
    files: dict[str, bytes], ref: str, cand: Candidate, field: str
) -> dict[str, Any] | None:
    try:
        rel = safe_relpath(ref)
    except PayloadError as exc:
        cand.note("error", "unsafe-path", f"{field}: {exc}", field)
        return None
    raw = files.get(rel)
    if raw is None:
        cand.note("error", "missing-schema-file", f"{field}: schema file {rel!r} not found", field)
        return None
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        cand.note(
            "error", "invalid-schema-file", f"{field}: {rel} is not valid JSON ({exc})", field
        )
        return None
    if not isinstance(parsed, dict):
        cand.note("error", "invalid-schema-file", f"{field}: {rel} must be a JSON object", field)
        return None
    return parsed


def resolve_contracts(draft: dict[str, Any], files: dict[str, bytes], cand: Candidate) -> None:
    """Inline referenced JSON Schema files (spec §26: importers convert to JSON Schema)."""
    for role in ("inputs", "outputs"):
        contract = draft.get(role)
        if isinstance(contract, dict):
            ref = contract.get("schema") or contract.get("schema_path")
            if isinstance(ref, str) and not contract.get("json_schema"):
                loaded = _load_schema(files, ref, cand, role)
                if loaded is not None:
                    contract["json_schema"] = loaded
    tools = draft.get("tools")
    if isinstance(tools, list):
        for i, tool in enumerate(tools):
            if not isinstance(tool, dict):
                continue
            for key in ("input_schema", "output_schema"):
                value = tool.get(key)
                if isinstance(value, str):
                    loaded = _load_schema(files, value, cand, f"tools[{i}].{key}")
                    if loaded is not None:
                        tool[key] = loaded
                    else:
                        tool.pop(key, None)


def check_references(draft: dict[str, Any], files: dict[str, bytes], cand: Candidate) -> None:
    instr = draft.get("instructions")
    if isinstance(instr, dict) and isinstance(instr.get("ref"), str):
        try:
            rel = safe_relpath(instr["ref"])
        except PayloadError as exc:
            cand.note("error", "unsafe-path", f"instructions.ref: {exc}", "instructions")
            return
        if rel not in files:
            cand.note(
                "warning",
                "missing-instructions",
                f"instructions file {rel!r} not found",
                "instructions",
            )


def normalise_draft_types(draft: dict[str, Any], cand: Candidate) -> None:
    """YAML/TOML authors often write ``version: 2.1`` (a float)."""
    version = draft.get("version")
    if version is not None and not isinstance(version, str):
        draft["version"] = str(version)
        cand.note("info", "coerced-version", f"version {version!r} coerced to string", "version")


def first_paragraph(markdown: str) -> str:
    body = re.sub(r"^---\n.*?\n---\n", "", markdown, count=1, flags=re.DOTALL)
    for block in re.split(r"\n\s*\n", body):
        text = " ".join(
            line.strip() for line in block.splitlines() if not line.startswith("#")
        ).strip()
        if text:
            return text[:400]
    return ""


def read_frontmatter(markdown: str) -> dict[str, Any]:
    match = re.match(r"^---\n(.*?)\n---\n", markdown, re.DOTALL)
    if not match:
        return {}
    try:
        data = yaml.safe_load(match.group(1))
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


def dir_display_name(path: Path) -> str:
    return path.resolve().name or os.fspath(path)
