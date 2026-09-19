"""Project sync, lockfiles, path overrides and dev links (spec §150-§152, §153).

A project declares what it consumes::

    [tool.ananke.agent]
    name = "engineering/coding-agent"
    version = "^3"

    [tool.ananke.skills]
    "core/graph-review" = "^2"

    [tool.ananke.overrides]
    "core/graph-review" = { path = "../graph-review-dev" }

``ananke sync`` resolves that (plus anything installed through ``apm install <ref>``),
prefers versions already pinned in ``ananke.lock`` unless ``--update`` is given, and
writes the lockfile. Overrides and dev links appear in the lock as ``source = "path"``
with ``dirty = true``; release profiles may forbid them.
"""

from __future__ import annotations

import json
import shutil
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from ananke.plexus.registry.errors import PolicyViolationError, RegistryError
from ananke.plexus.registry.importers.base import InspectionContext, finalize
from ananke.plexus.registry.importers.registry import default_registry
from ananke.plexus.registry.lockfile import (
    LOCK_NAME,
    Lockfile,
    build_lock,
    dump_lock,
    load_lock,
    lock_hash,
    write_lock,
)
from ananke.plexus.registry.models import ArtifactKind, VersionRecord, artifact_uri, parse_ref
from ananke.plexus.registry.registry import RegisterResult
from ananke.plexus.registry.resolver import (
    Requirement,
    ResolutionEnvironment,
    ResolutionOptions,
    ResolutionResult,
    Resolver,
)

if TYPE_CHECKING:
    from ananke.plexus.registry.registry import Registry


class ProjectRequirements(BaseModel):
    roots: list[Requirement] = Field(default_factory=list)
    overrides: dict[str, str] = Field(default_factory=dict)
    sources: list[str] = Field(default_factory=list)


def _as_list(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        return [v for v in value if isinstance(v, dict)]
    return []


def _links_path(project: Path) -> Path:
    return project / ".ananke" / "registry" / "links.json"


def read_links(project: Path) -> dict[str, dict[str, str]]:
    try:
        data = json.loads(_links_path(project).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def read_project_requirements(project: Path) -> ProjectRequirements:
    project = project.resolve()
    reqs = ProjectRequirements()
    seen: set[str] = set()

    def add(ref: str, version: str, kind: ArtifactKind | None, source: str) -> None:
        r = Requirement.parse(ref, kind, version)
        key = f"{r.kind or ''}:{r.namespace}/{r.name}"
        if key in seen:
            return
        seen.add(key)
        reqs.roots.append(r)
        if source not in reqs.sources:
            reqs.sources.append(source)

    pyproject = project / "pyproject.toml"
    if pyproject.is_file():
        try:
            tool = (
                tomllib.loads(pyproject.read_text(encoding="utf-8"))
                .get("tool", {})
                .get("ananke", {})
            )
        except tomllib.TOMLDecodeError as exc:
            raise RegistryError(f"invalid pyproject.toml: {exc}") from exc
        for agent in _as_list(tool.get("agent")):
            if agent.get("name"):
                add(
                    str(agent["name"]),
                    str(agent.get("version", "*")),
                    ArtifactKind.AGENT,
                    "pyproject.toml",
                )
        for name, version in (tool.get("skills", {}) or {}).items():
            add(str(name), str(version), ArtifactKind.SKILL, "pyproject.toml")
        for name, spec in (tool.get("overrides", {}) or {}).items():
            path = spec.get("path") if isinstance(spec, dict) else spec
            if path:
                reqs.overrides[_override_uri(str(name))] = str((project / str(path)).resolve())
    overrides_file = project / ".ananke" / "registry" / "overrides.toml"
    if overrides_file.is_file():
        for name, spec in (
            tomllib.loads(overrides_file.read_text(encoding="utf-8")).get("overrides", {}) or {}
        ).items():
            if isinstance(spec, dict) and spec.get("path"):
                reqs.overrides.setdefault(
                    _override_uri(str(name)), str((project / str(spec["path"])).resolve())
                )
    for uri, link in read_links(project).items():
        reqs.overrides.setdefault(uri, link["path"])
    from ananke.plexus.apm.lockfile import read_lock

    packages = read_lock(project).get("packages", [])
    for pkg in packages if isinstance(packages, list) else []:
        if isinstance(pkg, dict) and pkg.get("origin") == "registry" and pkg.get("registry_uri"):
            add(str(pkg["registry_uri"]), str(pkg.get("requirement", "*")), None, "apm.lock")
    return reqs


def _override_uri(name: str) -> str:
    ref = parse_ref(name)
    if ref.kind is not None and ref.namespace:
        return artifact_uri(ref.kind, ref.namespace, ref.name)
    if ref.namespace:
        return artifact_uri(ArtifactKind.SKILL, ref.namespace, ref.name)
    raise RegistryError(f"override {name!r} needs a namespace (ns/name)")


def record_from_path(registry: Registry, path: Path) -> VersionRecord:
    """Import a directory and build an in-memory record (never registered)."""
    ctx = InspectionContext(policy=registry.policy)
    plan = default_registry().plan(str(path), ctx)
    candidates = [c for imp, src in plan for c in imp.inspect(src, ctx)]
    if len(candidates) != 1:
        raise RegistryError(f"{path} must contain exactly one artifact (found {len(candidates)})")
    imported = finalize(candidates[0], ctx=ctx)
    return registry.preview_record(imported, source_path=str(path))


class SyncResult(BaseModel):
    lock_path: str
    lock_hash: str = ""
    changes: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    activated: list[str] = Field(default_factory=list)
    written: bool = False
    resolution: ResolutionResult


def _diff_locks(old: Lockfile | None, new: Lockfile) -> list[str]:
    before = {e.id: e.version for e in old.entries} if old else {}
    after = {e.id: e.version for e in new.entries}
    out = [f"+ {u}@{v}" for u, v in sorted(after.items()) if u not in before]
    out += [f"- {u}@{v}" for u, v in sorted(before.items()) if u not in after]
    out += [
        f"~ {u} {before[u]} -> {v}"
        for u, v in sorted(after.items())
        if u in before and before[u] != v
    ]
    return out


def sync(
    registry: Registry,
    project_root: Path,
    *,
    lock_path: Path | None = None,
    update: bool = False,
    env: ResolutionEnvironment | None = None,
    options: ResolutionOptions | None = None,
    extra: list[Requirement] | None = None,
    activate_all: bool = False,
    release: bool = False,
    dry_run: bool = False,
) -> SyncResult:
    project = project_root.resolve()
    path = lock_path or (project / LOCK_NAME)
    reqs = read_project_requirements(project)
    roots = [*reqs.roots, *(extra or [])]
    if not roots:
        raise RegistryError(
            "nothing to sync: declare [tool.ananke.agent]/[tool.ananke.skills] in pyproject.toml "
            "or run `apm install <ref>`"
        )
    overrides = {uri: record_from_path(registry, Path(p)) for uri, p in reqs.overrides.items()}
    if overrides and release and not registry.policy.overrides.allow_in_release:
        raise PolicyViolationError(
            "path overrides / dev links are not allowed in release profiles: "
            + ", ".join(sorted(overrides)),
            code="OVERRIDE_IN_RELEASE",
        )
    old_lock = load_lock(path) if path.is_file() else None
    opts = (options or ResolutionOptions()).model_copy()
    if old_lock is not None and not update:
        opts.pins = old_lock.pins()
        opts.pin_mode = "prefer"
    result = Resolver(registry, env=env, options=opts, overrides=overrides).resolve_many(
        list(roots)
    )
    result.raise_if_failed()
    lock = build_lock(result, [(_root_uri(registry, r, overrides), r.req) for r in roots])
    out = SyncResult(
        lock_path=str(path),
        changes=_diff_locks(old_lock, lock),
        warnings=result.warnings,
        resolution=result,
    )
    out.lock_hash = lock_hash(dump_lock(lock))
    if dry_run:
        return out
    unchanged = old_lock is not None and old_lock.model_copy(
        update={"registry_snapshot": ""}
    ) == lock.model_copy(update={"registry_snapshot": ""})
    if not unchanged or not path.is_file():  # a moved snapshot alone never rewrites the lock
        write_lock(registry, path, lock, actor=registry.actor)
        out.written = True
    if activate_all:
        from ananke.plexus.registry.activation import activate

        for node in result.nodes:
            if node.source == "path":
                continue
            activate(
                registry,
                project,
                node.ref.ref,
                requirement=next(
                    (r.req for r in roots if _root_uri(registry, r, overrides) == node.ref.uri),
                    None,
                ),
                root=node.root,
            )
            out.activated.append(node.ref.ref)
    return out


def _root_uri(registry: Registry, req: Requirement, overrides: dict[str, VersionRecord]) -> str:
    for uri in overrides:
        ref = parse_ref(uri)
        if ref.namespace == req.namespace and ref.name == req.name:
            return uri
    kind, ns, name = registry.locate(
        parse_ref(f"{req.namespace}/{req.name}" if req.namespace else req.name, req.kind), req.kind
    )
    return artifact_uri(kind, ns, name)


# --------------------------------------------------------------------------- dev links


class LinkResult(BaseModel):
    uri: str
    version: str
    path: str


def link(registry: Registry, project_root: Path, path: Path) -> LinkResult:
    """Dev mode (spec §152): expose a working copy without publishing an immutable version."""
    project = project_root.resolve()
    src = path.resolve()
    rec = record_from_path(registry, src)
    table = read_links(project)
    table[rec.uri] = {"path": str(src), "version": rec.version}
    lp = _links_path(project)
    lp.parent.mkdir(parents=True, exist_ok=True)
    lp.write_text(json.dumps(table, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    dest = project / ".ananke" / "skills" / "linked" / f"{rec.namespace}.{rec.name}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_symlink() or dest.is_file():
        dest.unlink()
    elif dest.exists():
        shutil.rmtree(dest)
    try:
        dest.symlink_to(src, target_is_directory=True)
    except OSError:
        shutil.copytree(src, dest)
    registry.emit("activation.changed", rec.uri, {"linked": str(src), "dev": True})
    return LinkResult(uri=rec.uri, version=rec.version, path=str(src))


def unlink(registry: Registry, project_root: Path, ref: str) -> bool:
    project = project_root.resolve()
    table = read_links(project)
    target = _match_link(table, ref)
    if target is None:
        return False
    info = table.pop(target)
    _links_path(project).write_text(
        json.dumps(table, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    parsed = parse_ref(target)
    dest = project / ".ananke" / "skills" / "linked" / f"{parsed.namespace}.{parsed.name}"
    if dest.is_symlink():
        dest.unlink()
    elif dest.exists():
        shutil.rmtree(dest)
    registry.emit("activation.changed", target, {"unlinked": info.get("path")})
    return True


def _match_link(table: dict[str, dict[str, str]], ref: str) -> str | None:
    if ref in table:
        return ref
    r = parse_ref(ref.split("@")[0])
    for uri in table:
        p = parse_ref(uri)
        if p.name == r.name and (r.namespace is None or p.namespace == r.namespace):
            return uri
    return None


# --------------------------------------------------------------------------- publish


class PublishStep(BaseModel):
    name: str
    ok: bool
    detail: str = ""


class PublishReport(BaseModel):
    steps: list[PublishStep] = Field(default_factory=list)
    result: RegisterResult | None = None

    @property
    def ok(self) -> bool:
        return self.result is not None and all(s.ok for s in self.steps)


def publish(
    registry: Registry,
    source: Path,
    *,
    version: str | None = None,
    channel: str | None = None,
    namespace: str | None = None,
    license: str | None = None,
    run_tests: bool = False,
    dry_run: bool = False,
) -> PublishReport:
    """validate → test → hash → resolve deps → conflict check → policy → register → docs (spec §153)."""
    from ananke.plexus.registry.errors import ManifestError, VersionContentConflictError

    report = PublishReport()

    def step(name: str, ok: bool, detail: str = "") -> bool:
        report.steps.append(PublishStep(name=name, ok=ok, detail=detail))
        return ok

    ctx = InspectionContext(policy=registry.policy, namespace=namespace)
    try:
        plan = default_registry().plan(str(source), ctx)
        cands = [c for imp, src in plan for c in imp.inspect(src, ctx)]
        if len(cands) != 1:
            step("validate", False, f"expected exactly one artifact, found {len(cands)}")
            return report
        overrides: dict[str, Any] = {}
        if license:
            overrides["license"] = {"expression": license}
        if namespace:
            overrides["namespace"] = namespace
        imported = finalize(cands[0], overrides=overrides, ctx=ctx)
        step("validate", True, imported.manifest.uri)
    except RegistryError as exc:
        step("validate", False, str(exc))
        return report

    if run_tests:
        try:
            from ananke.plexus.testing.api import run_quality_suite
            from ananke.plexus.testing.models.test import TestStatus

            run = run_quality_suite(project_root=source, profile="fast", save_evidence=False)
            bad = [r for r in run.results if r.status in {TestStatus.FAIL, TestStatus.ERROR}]
            if not step("test", not bad, f"{len(run.results)} result(s), {len(bad)} failing"):
                return report
        except Exception as exc:
            step("test", False, f"tests could not run: {exc}")
            return report
    else:
        step("test", True, "skipped (use --run-tests to execute the skill's own tests)")

    try:
        prep = registry.prepare(imported, version)
        step("hash", True, f"sha256:{prep.sha256[:16]}…")
    except RegistryError as exc:
        step("hash", False, str(exc))
        return report

    missing = []
    for dep in prep.manifest.artifact_dependencies():
        ref = dep.artifact_ref
        assert ref is not None and ref.kind is not None and ref.namespace is not None  # noqa: S101
        if not registry.store.versions_of(ref.kind, ref.namespace, ref.name):
            missing.append(f"{ref.namespace}/{ref.name}")
    step(
        "resolve-dependencies",
        not missing,
        "not yet registered: " + ", ".join(missing) if missing else "all dependencies registered",
    )

    try:
        registry.register(imported, version=version, channel=channel, dry_run=True)
        step("conflict-and-policy", True, "no conflicts; policy accepts")
    except VersionContentConflictError as exc:
        step("conflict-and-policy", False, str(exc))
        return report
    except (RegistryError, ManifestError) as exc:
        step("conflict-and-policy", False, str(exc))
        return report
    if dry_run:
        return report
    report.result = registry.register(imported, version=version, channel=channel)
    step("register", True, report.result.version_uri)
    try:
        from ananke.plexus.registry.learn import _refresh_docs

        _refresh_docs(registry)
        step("docs", True, "incremental rebuild if docs exist")
    except Exception as exc:
        step("docs", False, str(exc))
    return report
