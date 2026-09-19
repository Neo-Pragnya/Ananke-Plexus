"""Canonical version diffs, breaking-change heuristics and semver suggestions (spec §42-§45)."""

from __future__ import annotations

import difflib
from typing import Any, Literal

from pydantic import BaseModel, Field

from ananke.plexus.registry.models import ArtifactManifest, Dependency, Permissions

Bump = Literal["NONE", "PATCH", "MINOR", "MAJOR"]
_BUMP_ORDER: dict[str, int] = {"NONE": 0, "PATCH": 1, "MINOR": 2, "MAJOR": 3}
CHANGE_CLASSES = (
    "metadata-only",
    "behavioral contract",
    "input schema",
    "output schema",
    "permissions",
    "dependencies",
    "runtime",
    "prompts",
    "payload",
)
_PROMPT_HINTS = ("prompt", "instruction", "skill.md", "system")
_DOC_HINTS = ("readme", "docs/", "changelog", ".md")
MAX_TEXT_DIFF_LINES = 200


class PermissionDiff(BaseModel):
    expanded: bool = False
    added: list[str] = Field(default_factory=list)
    removed: list[str] = Field(default_factory=list)


class DependencyChange(BaseModel):
    id: str
    old: str | None = None
    new: str | None = None


class FileChange(BaseModel):
    path: str
    change: Literal["added", "removed", "modified"]
    kind: Literal["prompt", "docs", "test", "schema", "code", "other"] = "other"
    text_diff: list[str] = Field(default_factory=list)


class VersionDiff(BaseModel):
    """Structured diff; also the input to the docs comparison pages."""

    from_version: str
    to_version: str
    capabilities_added: list[str] = Field(default_factory=list)
    capabilities_removed: list[str] = Field(default_factory=list)
    tools_added: list[str] = Field(default_factory=list)
    tools_removed: list[str] = Field(default_factory=list)
    permissions: PermissionDiff = Field(default_factory=PermissionDiff)
    input_breaking: list[str] = Field(default_factory=list)
    output_breaking: list[str] = Field(default_factory=list)
    input_changed: bool = False
    output_changed: bool = False
    dependencies_added: list[DependencyChange] = Field(default_factory=list)
    dependencies_removed: list[DependencyChange] = Field(default_factory=list)
    dependencies_changed: list[DependencyChange] = Field(default_factory=list)
    runtimes_added: list[str] = Field(default_factory=list)
    runtimes_removed: list[str] = Field(default_factory=list)
    compatibility_changed: list[str] = Field(default_factory=list)
    metadata_changed: list[str] = Field(default_factory=list)
    files: list[FileChange] = Field(default_factory=list)
    change_classes: list[str] = Field(default_factory=list)
    suggested_bump: Bump = "NONE"
    reasons: list[str] = Field(default_factory=list)

    @property
    def breaking(self) -> bool:
        return self.suggested_bump == "MAJOR"

    @property
    def identical(self) -> bool:
        return self.suggested_bump == "NONE" and not self.change_classes


# --------------------------------------------------------------------------- permissions


def _covers(old_patterns: list[str], pattern: str) -> bool:
    for old in old_patterns:
        if old in {"**", "*", pattern}:
            return True
        if old.endswith("/**") and pattern.startswith(old[:-2]):
            return True
        if (
            old.endswith("/*")
            and pattern.startswith(old[:-1])
            and "/" not in pattern[len(old) - 1 :]
        ):
            return True
    return False


def diff_permissions(old: Permissions, new: Permissions) -> PermissionDiff:
    """Permission *expansion* is security-significant even when the API is unchanged (§44)."""
    old_flat: dict[str, list[str]] = {}
    new_flat: dict[str, list[str]] = {}
    for cat, pat in old.flatten():
        old_flat.setdefault(cat, []).append(pat)
    for cat, pat in new.flatten():
        new_flat.setdefault(cat, []).append(pat)
    added: list[str] = []
    removed: list[str] = []
    expanded = False
    for cat in sorted(set(old_flat) | set(new_flat)):
        before, after = old_flat.get(cat, []), new_flat.get(cat, [])
        for pat in after:
            if not _covers(before, pat):
                added.append(f"{cat}:{pat}")
                expanded = True
        for pat in before:
            if not _covers(after, pat):
                removed.append(f"{cat}:{pat}")
    return PermissionDiff(expanded=expanded, added=sorted(added), removed=sorted(removed))


# --------------------------------------------------------------------------- schemas


def _type_of(schema: dict[str, Any]) -> str:
    t = schema.get("type")
    if isinstance(t, list):
        return "|".join(sorted(str(x) for x in t))
    return str(t) if t is not None else ""


def schema_breaking(
    old: dict[str, Any] | None,
    new: dict[str, Any] | None,
    role: Literal["input", "output"],
    *,
    path: str = "$",
    depth: int = 0,
) -> list[str]:
    """Heuristic JSON-Schema compatibility check.

    Input (callers send it): new required property, removed property, changed type or
    removed enum value break callers. Output (callers read it): removed property, changed
    type, or *added* enum value break consumers.
    """
    if old is None and new is None:
        return []
    if old is None:
        return [f"{path}: input schema introduced"] if role == "input" else []
    if new is None:
        return [] if role == "input" else [f"{path}: output schema removed"]
    if depth > 8:
        return []
    reasons: list[str] = []
    if _type_of(old) != _type_of(new):
        reasons.append(f"{path}: type {_type_of(old) or 'any'} -> {_type_of(new) or 'any'}")
        return reasons
    old_props: dict[str, Any] = old.get("properties", {}) or {}
    new_props: dict[str, Any] = new.get("properties", {}) or {}
    old_req = set(old.get("required", []) or [])
    new_req = set(new.get("required", []) or [])
    for prop in sorted(old_props):
        if prop not in new_props:
            reasons.append(f"{path}.{prop}: property removed")
        elif isinstance(old_props[prop], dict) and isinstance(new_props[prop], dict):
            reasons.extend(
                schema_breaking(
                    old_props[prop], new_props[prop], role, path=f"{path}.{prop}", depth=depth + 1
                )
            )
    if role == "input":
        for prop in sorted(new_req - old_req):
            reasons.append(f"{path}.{prop}: new required property")
    else:
        for prop in sorted(old_req - new_req):
            reasons.append(f"{path}.{prop}: property no longer guaranteed")
    old_enum, new_enum = old.get("enum"), new.get("enum")
    if isinstance(old_enum, list) and isinstance(new_enum, list):
        if role == "input":
            for value in old_enum:
                if value not in new_enum:
                    reasons.append(f"{path}: enum value {value!r} removed")
        else:
            for value in new_enum:
                if value not in old_enum:
                    reasons.append(f"{path}: enum value {value!r} added")
    if isinstance(old.get("items"), dict) and isinstance(new.get("items"), dict):
        reasons.extend(
            schema_breaking(old["items"], new["items"], role, path=f"{path}[]", depth=depth + 1)
        )
    return reasons


# --------------------------------------------------------------------------- files


def _file_kind(path: str) -> Literal["prompt", "docs", "test", "schema", "code", "other"]:
    low = path.lower()
    if low.startswith("tests/") or "/tests/" in low or low.startswith("test_"):
        return "test"
    if low.startswith("schemas/") or low.endswith((".schema.json",)):
        return "schema"
    if low.startswith("prompts/") or any(h in low for h in _PROMPT_HINTS):
        return "prompt"
    if any(h in low for h in _DOC_HINTS):
        return "docs"
    if low.endswith((".py", ".rs", ".js", ".ts", ".sh")):
        return "code"
    return "other"


def _text(data: bytes | None) -> list[str] | None:
    if data is None:
        return []
    try:
        return data.decode("utf-8").splitlines()
    except UnicodeDecodeError:
        return None


def diff_files(
    old: dict[str, bytes], new: dict[str, bytes], *, with_text: bool = True
) -> list[FileChange]:
    changes: list[FileChange] = []
    for path in sorted(set(old) | set(new)):
        if path == "ananke.registry.json":
            continue
        a, b = old.get(path), new.get(path)
        if a == b:
            continue
        change: Literal["added", "removed", "modified"] = (
            "added" if a is None else "removed" if b is None else "modified"
        )
        text_diff: list[str] = []
        if with_text:
            la, lb = _text(a), _text(b)
            if la is not None and lb is not None:
                text_diff = list(
                    difflib.unified_diff(la, lb, f"a/{path}", f"b/{path}", lineterm="", n=2)
                )[:MAX_TEXT_DIFF_LINES]
        changes.append(
            FileChange(path=path, change=change, kind=_file_kind(path), text_diff=text_diff)
        )
    return changes


# --------------------------------------------------------------------------- main diff


def _dep_map(deps: list[Dependency]) -> dict[str, str | None]:
    return {(d.id or d.name or ""): d.version for d in deps}


def diff_manifests(
    old: ArtifactManifest,
    new: ArtifactManifest,
    *,
    old_files: dict[str, bytes] | None = None,
    new_files: dict[str, bytes] | None = None,
    with_text: bool = True,
) -> VersionDiff:
    d = VersionDiff(from_version=old.version, to_version=new.version)
    reasons: list[str] = []
    bump: Bump = "NONE"

    def raise_to(level: Bump, why: str) -> None:
        nonlocal bump
        reasons.append(f"{level}: {why}")
        if _BUMP_ORDER[level] > _BUMP_ORDER[bump]:
            bump = level

    classes: set[str] = set()

    oc, nc = set(old.capabilities), set(new.capabilities)
    d.capabilities_added = sorted(nc - oc)
    d.capabilities_removed = sorted(oc - nc)
    if d.capabilities_removed:
        raise_to("MAJOR", f"capability removed: {', '.join(d.capabilities_removed)}")
        classes.add("behavioral contract")
    if d.capabilities_added:
        raise_to("MINOR", f"capability added: {', '.join(d.capabilities_added)}")
        classes.add("behavioral contract")

    ot, nt = {t.name: t for t in old.tools}, {t.name: t for t in new.tools}
    d.tools_added = sorted(set(nt) - set(ot))
    d.tools_removed = sorted(set(ot) - set(nt))
    if d.tools_removed:
        raise_to("MAJOR", f"tool removed: {', '.join(d.tools_removed)}")
        classes.add("behavioral contract")
    if d.tools_added:
        raise_to("MINOR", f"tool added: {', '.join(d.tools_added)}")
        classes.add("behavioral contract")
    for name in sorted(set(ot) & set(nt)):
        for role in ("input", "output"):
            a = getattr(ot[name], f"{role}_schema")
            b = getattr(nt[name], f"{role}_schema")
            if a != b:
                breaks = schema_breaking(a, b, role)
                if breaks:
                    raise_to("MAJOR", f"tool {name} {role} schema breaking: {breaks[0]}")
                    (d.input_breaking if role == "input" else d.output_breaking).extend(
                        f"{name}: {r}" for r in breaks
                    )
                else:
                    raise_to("MINOR", f"tool {name} {role} schema extended")
                classes.add("input schema" if role == "input" else "output schema")

    for role, attr in (("input", "inputs"), ("output", "outputs")):
        a_c, b_c = getattr(old, attr), getattr(new, attr)
        a = a_c.json_schema if a_c else None
        b = b_c.json_schema if b_c else None
        if a != b:
            breaks = schema_breaking(a, b, role)  # type: ignore[arg-type]
            setattr(d, f"{role}_changed", True)
            if breaks:
                getattr(d, f"{role}_breaking").extend(breaks)
                raise_to("MAJOR", f"{role} schema breaking: {breaks[0]}")
            else:
                raise_to("MINOR", f"{role} schema changed compatibly")
            classes.add("input schema" if role == "input" else "output schema")

    d.permissions = diff_permissions(old.permissions, new.permissions)
    if d.permissions.expanded:
        raise_to("MAJOR", f"permission expansion: {', '.join(d.permissions.added)}")
        classes.add("permissions")
    elif d.permissions.removed:
        raise_to("PATCH", "permissions narrowed")
        classes.add("permissions")

    od, nd = _dep_map(old.all_dependencies()), _dep_map(new.all_dependencies())
    for dep_id in sorted(set(nd) - set(od)):
        d.dependencies_added.append(DependencyChange(id=dep_id, new=nd[dep_id]))
    for dep_id in sorted(set(od) - set(nd)):
        d.dependencies_removed.append(DependencyChange(id=dep_id, old=od[dep_id]))
    for dep_id in sorted(set(od) & set(nd)):
        if od[dep_id] != nd[dep_id]:
            d.dependencies_changed.append(
                DependencyChange(id=dep_id, old=od[dep_id], new=nd[dep_id])
            )
    if d.dependencies_added:
        raise_to("MINOR", "dependency added")
    if d.dependencies_removed or d.dependencies_changed:
        raise_to("PATCH", "dependency changed")
    if d.dependencies_added or d.dependencies_removed or d.dependencies_changed:
        classes.add("dependencies")

    ort, nrt = set(old.runtime.supported), set(new.runtime.supported)
    d.runtimes_added = sorted(nrt - ort)
    d.runtimes_removed = sorted(ort - nrt)
    if d.runtimes_removed:
        raise_to("MAJOR", f"runtime no longer supported: {', '.join(d.runtimes_removed)}")
    if d.runtimes_added:
        raise_to("MINOR", f"runtime support added: {', '.join(d.runtimes_added)}")
    if old.runtime.provider != new.runtime.provider:
        raise_to("MAJOR", f"runtime provider {old.runtime.provider} -> {new.runtime.provider}")
        classes.add("runtime")
    if d.runtimes_added or d.runtimes_removed:
        classes.add("runtime")

    if old.compatibility != new.compatibility:
        of = dict(old.compatibility.flatten())
        nf = dict(new.compatibility.flatten())
        d.compatibility_changed = sorted(k for k in set(of) | set(nf) if of.get(k) != nf.get(k))
        raise_to("PATCH", "compatibility declaration changed")
        classes.add("runtime")

    meta: list[str] = []
    if old.summary != new.summary:
        meta.append("summary")
    if old.description != new.description:
        meta.append("description")
    if old.metadata != new.metadata:
        meta.append("metadata")
    if old.license != new.license:
        meta.append("license")
    if old.owners != new.owners or old.maintainers != new.maintainers:
        meta.append("ownership")
    if old.model_requirements != new.model_requirements:
        meta.append("model_requirements")
        raise_to("MINOR", "model requirements changed")
        classes.add("behavioral contract")
    d.metadata_changed = meta
    if meta:
        raise_to("PATCH", f"metadata changed: {', '.join(meta)}")
        if not classes:
            classes.add("metadata-only")

    if old_files is not None and new_files is not None:
        d.files = diff_files(old_files, new_files, with_text=with_text)
        if any(f.kind == "prompt" for f in d.files):
            classes.add("prompts")
            raise_to("PATCH", "prompt content changed")
        if any(f.kind in {"code", "other", "test"} for f in d.files):
            classes.add("payload")
            raise_to("PATCH", "payload content changed")
        if any(f.kind in {"docs", "schema"} for f in d.files) and not classes:
            classes.add("metadata-only")
            raise_to("PATCH", "documentation/schema files changed")

    d.change_classes = [c for c in CHANGE_CLASSES if c in classes]
    d.suggested_bump = bump
    d.reasons = reasons
    return d


def apply_bump(version: str, bump: Bump) -> str:
    from ananke.plexus.registry.semver import normalize_version

    v = normalize_version(version)
    if bump == "MAJOR":
        return str(v.bump_major()) if v.major > 0 else str(v.bump_minor())
    if bump == "MINOR":
        return str(v.bump_minor())
    return str(v.bump_patch())


def render_diff_text(diff: VersionDiff) -> str:
    """CLI rendering matching the layout in spec §115."""
    lines: list[str] = [f"{diff.from_version} -> {diff.to_version}", ""]
    lines.append("Capabilities:")
    if diff.capabilities_added or diff.capabilities_removed:
        lines += [f"  + {c}" for c in diff.capabilities_added]
        lines += [f"  - {c}" for c in diff.capabilities_removed]
    else:
        lines.append("  unchanged")
    lines += ["", "Permissions:"]
    if diff.permissions.added or diff.permissions.removed:
        lines += [f"  + {p}" for p in diff.permissions.added]
        lines += [f"  - {p}" for p in diff.permissions.removed]
        if diff.permissions.expanded:
            lines.append("  !! permissions EXPANDED (security-significant)")
    else:
        lines.append("  unchanged")
    for label, changed, breaking in (
        ("Inputs", diff.input_changed, diff.input_breaking),
        ("Outputs", diff.output_changed, diff.output_breaking),
    ):
        lines += ["", f"{label}:"]
        if breaking:
            lines += [f"  BREAKING {b}" for b in breaking]
        else:
            lines.append("  backward compatible" if changed else "  unchanged")
    lines += ["", "Dependencies:"]
    if diff.dependencies_added or diff.dependencies_removed or diff.dependencies_changed:
        lines += [f"  + {c.id} {c.new or ''}".rstrip() for c in diff.dependencies_added]
        lines += [f"  - {c.id} {c.old or ''}".rstrip() for c in diff.dependencies_removed]
        lines += [f"  {c.id} {c.old} -> {c.new}" for c in diff.dependencies_changed]
    else:
        lines.append("  unchanged")
    if diff.change_classes:
        lines += ["", "Change classes: " + ", ".join(diff.change_classes)]
    lines += ["", "Suggested semver:", f"  {diff.suggested_bump}"]
    return "\n".join(lines)
