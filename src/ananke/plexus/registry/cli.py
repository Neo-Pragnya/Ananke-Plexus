"""``ananke registry`` / ``ananke skill`` / ``ananke agent`` / ``ananke sync`` (spec §68, §70, §71, §175)."""

from __future__ import annotations

import contextlib
import functools
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

import typer
from rich.console import Console
from rich.table import Table

from ananke.plexus.registry.diff import render_diff_text
from ananke.plexus.registry.errors import (
    IntegrityError,
    InvalidIdentityError,
    InvalidRequirementError,
    NotFoundError,
    NotInitializedError,
    PolicyViolationError,
    QuarantinedError,
    RegistryError,
    RemoteSourceError,
    SecretDetectedError,
)
from ananke.plexus.registry.models import ArtifactKind, ResolutionMode, TrustStatus
from ananke.plexus.registry.policy import (
    PRESETS,
    TrustedKey,
    dump_policy_toml,
    load_policy,
    policy_path,
)
from ananke.plexus.registry.present import record_to_dict
from ananke.plexus.registry.registry import Registry
from ananke.plexus.registry.resolver import (
    Requirement,
    ResolutionEnvironment,
    ResolutionOptions,
    Resolver,
)

F = TypeVar("F", bound=Callable[..., Any])

registry_app = typer.Typer(
    help="Skill & agent registry: learn, version, resolve, document and serve capabilities"
)
docs_app = typer.Typer(help="Registry documentation site")
alias_app = typer.Typer(help="Registry aliases")
policy_app = typer.Typer(help="Registry policy")
analytics_app = typer.Typer(help="Optional analytics projection")
key_app = typer.Typer(help="Signing keys: generate, trust, revoke")
remote_app = typer.Typer(help="Remote registries (pull-only federation)")
skill_app = typer.Typer(help="Skill shortcuts over the registry")
agent_app = typer.Typer(help="Agent shortcuts over the registry")
registry_app.add_typer(docs_app, name="docs")
registry_app.add_typer(alias_app, name="alias")
registry_app.add_typer(policy_app, name="policy")
registry_app.add_typer(analytics_app, name="analytics")
registry_app.add_typer(key_app, name="key")
registry_app.add_typer(remote_app, name="remote")

_PROJECT = typer.Option(Path("."), "--project", help="Project root")
_USER = typer.Option(False, "--user", help="Use the user-level registry (~/.ananke/registry)")
_JSON = typer.Option(False, "--json", help="Machine-readable JSON output")

_EXIT: list[tuple[type[Exception], int]] = [
    (PolicyViolationError, 3),
    (QuarantinedError, 3),
    (SecretDetectedError, 3),
    (IntegrityError, 4),
    (RemoteSourceError, 6),
    (NotInitializedError, 2),
    (InvalidIdentityError, 2),
    (InvalidRequirementError, 2),
    (NotFoundError, 2),
]


def guarded(func: F) -> F:
    """Turn registry errors into ``error: [CODE] message`` + a meaningful exit code."""

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return func(*args, **kwargs)
        except RegistryError as exc:
            typer.echo(f"error: {exc}", err=True)
            code = next((c for cls, c in _EXIT if isinstance(exc, cls)), 1)
            raise typer.Exit(code=code) from None

    return wrapper  # type: ignore[return-value]


def open_registry(project: Path, user: bool = False, *, create: bool = False) -> Registry:
    reg = Registry.for_user(create=create) if user else Registry.for_project(project, create=create)
    if create:
        reg.init()
    return reg


def _echo_json(obj: Any) -> None:
    typer.echo(json.dumps(obj, indent=2, default=str))


def _table(title: str, columns: list[str], rows: list[list[str]]) -> None:
    t = Table(title=title, show_lines=False)
    for c in columns:
        t.add_column(c)
    for r in rows:
        t.add_row(*r)
    # wide enough for pipes/CI logs (a real terminal keeps its own width)
    Console(width=None if sys.stdout.isatty() else 200).print(t)


def _env(runtime: str | None) -> ResolutionEnvironment:
    return ResolutionEnvironment.detect(runtime=runtime)


def _options(
    mode: str | None,
    allow_prerelease: bool,
    allow_yanked: bool,
    allow_deprecated: bool,
    channel: list[str] | None,
) -> ResolutionOptions:
    return ResolutionOptions(
        mode=ResolutionMode(mode) if mode else None,
        allow_prerelease=True if allow_prerelease else None,
        allow_yanked=allow_yanked,
        allow_deprecated=True if allow_deprecated else None,
        channels=channel or None,
    )


# --------------------------------------------------------------------------- lifecycle


@registry_app.command("init")
@guarded
def init(
    project: Path = _PROJECT,
    user: bool = _USER,
    preset: str = typer.Option("default", "--policy", help="Policy preset: default | enterprise"),
) -> None:
    """Create a local registry (SQLite + content-addressed store)."""
    if preset not in PRESETS:
        raise typer.BadParameter(f"unknown preset {preset!r}; choose {', '.join(PRESETS)}")
    reg = open_registry(project, user, create=True)
    pp = policy_path(reg.root)
    if not pp.exists():
        pp.write_text(dump_policy_toml(PRESETS[preset]()), encoding="utf-8")
    typer.echo(f"Registry ready: {reg.root}")
    typer.echo(f"Policy: {pp} ({preset})")
    typer.echo(f"Registry id: {reg.registry_id}")
    reg.close()


def _print_learn(report: Any, as_json: bool) -> int:
    if as_json:
        _echo_json(report.model_dump(mode="json", exclude={"candidates": {"__all__": {"result"}}}))
        return 0 if report.ok else 1
    rows = [
        [c.action, (c.uri or c.source).replace("ananke://", ""), c.version or "", c.message[:70]]
        for c in report.candidates
    ]
    _table(f"learn {report.source}", ["action", "artifact", "version", "note"], rows)
    for c in report.candidates:
        for d in c.diagnostics:
            if d.level != "info":
                typer.echo(f"  {d.level}: {c.uri or c.source}: [{d.code}] {d.message}")
        for n in c.identity_notes:
            typer.echo(f"  identity: {n}")
        for p in c.permission_concerns:
            typer.echo(f"  permission: {p}")
        if c.missing:
            typer.echo(
                f"  missing: {', '.join(c.missing)} (supply with --namespace/--name/--version/--license or --interactive)"
            )
        if c.action == "conflict" and c.suggested_version:
            typer.echo(f"  suggestion: publish as {c.suggested_version} (or pass --version auto)")
    return 0 if report.ok else 1


@registry_app.command("learn")
@guarded
def learn_cmd(
    source: str = typer.Argument(
        ...,
        help="Path, python:PKG, rust:DIR, mcp:FILE, mcp-stdio:CMD, mcp-http:URL, git:URL, framework:NAME:DIR, dynamic:NAME:DIR",
    ),
    project: Path = _PROJECT,
    user: bool = _USER,
    kind: str | None = typer.Option(None, "--kind"),
    namespace: str | None = typer.Option(None, "--namespace"),
    name: str | None = typer.Option(None, "--name"),
    version: str | None = typer.Option(
        None, "--version", help="Explicit version or 'auto' (diff-based bump)"
    ),
    license: str | None = typer.Option(None, "--license", help="SPDX expression"),
    channel: str | None = typer.Option(None, "--channel"),
    allow_dynamic: bool = typer.Option(
        False, "--allow-dynamic", help="Permit sandboxed dynamic introspection"
    ),
    allow_network: bool = typer.Option(
        False, "--allow-network", help="Permit remote git/MCP access"
    ),
    plugin: str | None = typer.Option(
        None, "--plugin", help="Introspection plugin module:function"
    ),
    plugin_path: list[str] = typer.Option([], "--plugin-path"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    force: bool = typer.Option(False, "--force", help="Ignore the unchanged-source fingerprint"),
    interactive: bool = typer.Option(False, "--interactive", help="Prompt for missing metadata"),
    as_json: bool = _JSON,
) -> None:
    """Discover capabilities from a source and register new immutable versions."""
    from ananke.plexus.registry.learn import learn

    reg = open_registry(project, user, create=True)
    kwargs: dict[str, Any] = {
        "kind": kind,
        "namespace": namespace,
        "name": name,
        "version": version,
        "license": license,
        "channel": channel,
        "allow_dynamic": allow_dynamic,
        "allow_network": allow_network,
        "plugin": plugin,
        "plugin_paths": plugin_path,
        "dry_run": dry_run,
        "force": force,
    }
    try:
        report = learn(reg, source, **kwargs)
        if interactive and not as_json and sys.stdin.isatty():
            missing = sorted(
                {m for c in report.candidates if c.action == "incomplete" for m in c.missing}
            )
            if missing:
                typer.echo("Missing required metadata: " + ", ".join(missing))
                for field in missing:
                    kwargs[field if field != "license" else "license"] = typer.prompt(field)
                if "license" not in missing and not (license or kwargs.get("license")):
                    kwargs["license"] = (
                        typer.prompt("license (SPDX, blank to skip)", default="") or None
                    )
                report = learn(reg, source, **kwargs)
        code = _print_learn(report, as_json)
    finally:
        reg.close()
    if code:
        raise typer.Exit(code=code)


@registry_app.command("register")
@guarded
def register_cmd(
    path: Path = typer.Argument(
        ..., help="Directory containing an Ananke manifest or a recognisable skill layout"
    ),
    project: Path = _PROJECT,
    user: bool = _USER,
    version: str | None = typer.Option(None, "--version", help="Explicit version or 'auto'"),
    channel: str | None = typer.Option(None, "--channel"),
    namespace: str | None = typer.Option(None, "--namespace"),
    license: str | None = typer.Option(None, "--license"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    as_json: bool = _JSON,
) -> None:
    """Register a local artifact directory (``learn`` for a filesystem source)."""
    from ananke.plexus.registry.learn import learn

    reg = open_registry(project, user, create=True)
    try:
        report = learn(
            reg,
            str(path),
            version=version,
            channel=channel,
            namespace=namespace,
            license=license,
            dry_run=dry_run,
            force=True,
        )
        code = _print_learn(report, as_json)
    finally:
        reg.close()
    if code:
        raise typer.Exit(code=code)


@registry_app.command("unregister")
@guarded
def unregister_cmd(
    ref: str = typer.Argument(..., help="ns/name@version (or range)"),
    project: Path = _PROJECT,
    user: bool = _USER,
    purge: bool = typer.Option(
        False, "--purge", help="Delete instead of archiving (refused if referenced)"
    ),
    force: bool = typer.Option(False, "--force"),
    reason: str | None = typer.Option(None, "--reason"),
) -> None:
    """Archive a version (default) or purge it when nothing references it."""
    with open_registry(project, user) as reg:
        recs = reg.unregister(ref, purge=purge, force=force, reason=reason)
    typer.echo(f"{'Purged' if purge else 'Archived'}: " + ", ".join(r.version_uri for r in recs))


@registry_app.command("inspect")
@guarded
def inspect_cmd(
    ref: str | None = typer.Argument(None, help="Registered artifact (ns/name@version)"),
    source: str | None = typer.Option(
        None, "--source", help="Inspect an unregistered source instead"
    ),
    report_file: Path | None = typer.Option(
        None, "--report", help="Write a pre-registration HTML report"
    ),
    project: Path = _PROJECT,
    user: bool = _USER,
    allow_dynamic: bool = typer.Option(False, "--allow-dynamic"),
    allow_network: bool = typer.Option(False, "--allow-network"),
    as_json: bool = _JSON,
) -> None:
    """Show everything about a registered version, or a pre-registration report for a source."""
    if source:
        from ananke.plexus.registry.docsgen.render import inspection_report_html
        from ananke.plexus.registry.learn import inspect_source

        reg = open_registry(project, user, create=True)
        try:
            rep = inspect_source(
                reg, source, allow_dynamic=allow_dynamic, allow_network=allow_network
            )
        finally:
            reg.close()
        if report_file:
            report_file.write_text(inspection_report_html(rep), encoding="utf-8")
            typer.echo(f"Report: {report_file}")
        _print_learn(rep, as_json)
        return
    if not ref:
        raise typer.BadParameter("give a registered REF or --source")
    with open_registry(project, user) as reg:
        _show(reg, ref, as_json, full=True)


def _show(
    reg: Registry, ref: str, as_json: bool, *, full: bool = False, kind: str | None = None
) -> None:
    from ananke.plexus.registry.models import parse_ref

    r = parse_ref(ref, kind)
    if r.requirement and _is_exact(r.requirement):
        rec = reg.exact_version(ref, kind)
    else:
        versions = reg.versions(ref.split("@")[0], kind)
        rec = versions[-1]
    d = record_to_dict(rec, reg)
    if as_json:
        _echo_json(d)
        return
    typer.echo(f"{rec.version_uri}")
    typer.echo(f"  {rec.summary}")
    typer.echo(
        f"  state: {rec.lifecycle.value} · channel: {rec.channel} · trust: {rec.trust.value} · license: {rec.license.expression or 'unknown'}"
    )
    typer.echo(f"  digest: {rec.digest}")
    typer.echo(f"  capabilities: {', '.join(rec.manifest.capabilities) or '-'}")
    typer.echo(f"  runtimes: {', '.join(rec.manifest.runtime.supported) or 'any'}")
    perms = rec.manifest.permissions.flatten()
    typer.echo("  permissions: " + (", ".join(f"{c}={p}" for c, p in perms) or "none"))
    deps = rec.manifest.all_dependencies()
    typer.echo(
        "  dependencies: "
        + (", ".join(f"{d.id or d.name}@{d.version or '*'}" for d in deps) or "none")
    )
    typer.echo(
        "  versions: "
        + ", ".join(v.version for v in reg.store.versions_of(rec.kind, rec.namespace, rec.name))
    )
    if full:
        typer.echo(
            f"  provenance: {rec.provenance.source_type} {rec.provenance.source_name or ''}".rstrip()
        )
        vid = reg.store.version_id(rec.kind, rec.namespace, rec.name, rec.version)
        typer.echo(
            "  files: "
            + ", ".join(f.path for f in reg.store.files_of(vid) if f.path != "ananke.registry.json")
        )
        typer.echo(f"  security: {rec.security.status} · tests: {rec.quality.tests_status}")
        dependents = reg.dependents(rec)
        if dependents:
            typer.echo(
                "  used by: "
                + ", ".join(sorted({f"{d.namespace}/{d.name}@{d.version}" for d, _ in dependents}))
            )


def _is_exact(req: str) -> bool:
    from ananke.plexus.registry.semver import VersionReq

    try:
        return VersionReq.parse(req).is_exact
    except RegistryError:
        return False


@registry_app.command("show")
@guarded
def show_cmd(
    ref: str = typer.Argument(...),
    project: Path = _PROJECT,
    user: bool = _USER,
    as_json: bool = _JSON,
) -> None:
    """Show an artifact (latest version unless ``@version`` is given)."""
    with open_registry(project, user) as reg:
        _show(reg, ref, as_json)


@registry_app.command("list")
@guarded
def list_cmd(
    ref: str | None = typer.Argument(None, help="Artifact to list versions of"),
    project: Path = _PROJECT,
    user: bool = _USER,
    kind: str | None = typer.Option(None, "--kind"),
    namespace: str | None = typer.Option(None, "--namespace"),
    versions: bool = typer.Option(False, "--versions"),
    as_json: bool = _JSON,
) -> None:
    """List artifacts, or every version of one artifact."""
    with open_registry(project, user) as reg:
        if ref:
            recs = reg.versions(ref, kind)
            if as_json:
                _echo_json([record_to_dict(r, None, detail=False) for r in recs])
                return
            _table(
                ref,
                ["version", "lifecycle", "channel", "trust", "digest"],
                [
                    [r.version, r.lifecycle.value, r.channel, r.trust.value, r.digest[:19]]
                    for r in recs
                ],
            )
            return
        if versions:
            recs = reg.list_versions(kind, namespace)
            if as_json:
                _echo_json([record_to_dict(r, None, detail=False) for r in recs])
                return
            _table(
                "versions",
                ["artifact", "version", "lifecycle", "channel", "trust"],
                [
                    [
                        f"{r.kind.value}/{r.namespace}/{r.name}",
                        r.version,
                        r.lifecycle.value,
                        r.channel,
                        r.trust.value,
                    ]
                    for r in recs
                ],
            )
            return
        arts = reg.list_artifacts(kind, namespace)
        if as_json:
            _echo_json(
                [
                    {"uri": a.uri, "versions": a.version_count, "latest": a.latest_version}
                    for a in arts
                ]
            )
            return
        _table(
            "artifacts",
            ["kind", "artifact", "versions", "latest"],
            [
                [
                    a.kind.value,
                    f"{a.namespace}/{a.name}",
                    str(a.version_count),
                    a.latest_version or "",
                ]
                for a in arts
            ],
        )


@registry_app.command("search")
@guarded
def search_cmd(
    query: str = typer.Argument(
        "", help='Free text and filters, e.g. "kind:skill capability:graph.query graph review"'
    ),
    project: Path = _PROJECT,
    user: bool = _USER,
    kind: str | None = typer.Option(None, "--kind"),
    runtime: str | None = typer.Option(None, "--runtime"),
    trust: str | None = typer.Option(None, "--trust"),
    channel: str | None = typer.Option(None, "--channel"),
    capability: list[str] = typer.Option([], "--capability"),
    tag: str | None = typer.Option(None, "--tag"),
    license: str | None = typer.Option(None, "--license"),
    limit: int = typer.Option(20, "--limit"),
    all_versions: bool = typer.Option(False, "--all-versions"),
    semantic: bool = typer.Option(
        False, "--semantic", help="Similarity search (needs [semantic] enabled in policy)"
    ),
    as_json: bool = _JSON,
) -> None:
    """Lexical search with capability/runtime/trust/channel filters."""
    with open_registry(project, user) as reg:
        hits = reg.search(
            query,
            kind=kind,
            runtime=runtime,
            trust=trust,
            channel=channel,
            capability=capability or None,
            tag=tag,
            license=license,
            limit=limit,
            all_versions=all_versions,
            semantic=semantic,
        )
    if as_json:
        _echo_json([h.model_dump(mode="json") for h in hits])
        return
    if not hits:
        typer.echo("No matches.")
        return
    _table(
        "search",
        ["artifact", "version", "kind", "trust", "channel", "summary"],
        [
            [
                f"{h.namespace}/{h.name}",
                h.version,
                h.kind.value,
                h.trust.value,
                h.channel,
                h.summary[:50],
            ]
            for h in hits
        ],
    )


@registry_app.command("diff")
@guarded
def diff_cmd(
    a: str = typer.Argument(..., help="ns/name@version"),
    b: str = typer.Argument(..., help="ns/name@version"),
    project: Path = _PROJECT,
    user: bool = _USER,
    as_json: bool = _JSON,
) -> None:
    """Compare two versions: capabilities, permissions, schemas, dependencies, suggested semver."""
    with open_registry(project, user) as reg:
        d = reg.diff(a, b, with_text=False)
    _echo_json(d.model_dump(mode="json")) if as_json else typer.echo(render_diff_text(d))


@registry_app.command("resolve")
@guarded
def resolve_cmd(
    ref: str = typer.Argument(..., help="ns/name[@range], e.g. core/graph-review@^2"),
    project: Path = _PROJECT,
    user: bool = _USER,
    kind: str | None = typer.Option(None, "--kind"),
    runtime: str | None = typer.Option(None, "--runtime"),
    mode: str | None = typer.Option(
        None,
        "--mode",
        help="highest-compatible|highest-approved|lowest-compatible|stable-only|exact|locked",
    ),
    explain: bool = typer.Option(False, "--explain"),
    allow_prerelease: bool = typer.Option(False, "--allow-prerelease"),
    allow_yanked: bool = typer.Option(False, "--allow-yanked"),
    allow_deprecated: bool = typer.Option(False, "--allow-deprecated"),
    channel: list[str] = typer.Option([], "--channel"),
    as_json: bool = _JSON,
) -> None:
    """Resolve a requirement to an exact version under policy (and explain why)."""
    with open_registry(project, user) as reg:
        result = Resolver(
            reg,
            env=_env(runtime),
            options=_options(mode, allow_prerelease, allow_yanked, allow_deprecated, channel),
        ).resolve(Requirement.parse(ref, kind), kind=kind)
    if as_json:
        _echo_json(
            {
                "ok": result.ok,
                "selected": result.selected.model_dump() if result.selected else None,
                "explanation": result.explain(),
                **result.model_dump(mode="json"),
            }
        )
    elif explain or not result.ok:
        typer.echo(result.explain())
    else:
        for node in result.nodes:
            typer.echo(f"{node.ref.ref}  {node.ref.digest[:19]}{'  (root)' if node.root else ''}")
    if not result.ok:
        raise typer.Exit(code=1)


# --------------------------------------------------------------------------- state changes


@registry_app.command("promote")
@guarded
def promote_cmd(
    ref: str = typer.Argument(..., help="ns/name@version"),
    project: Path = _PROJECT,
    user: bool = _USER,
    channel: str | None = typer.Option(None, "--channel"),
    trust: str | None = typer.Option(None, "--trust", help="verified | approved | restricted"),
    reviewed_by: str | None = typer.Option(None, "--reviewed-by"),
    reason: str | None = typer.Option(None, "--reason"),
    run_tests: bool = typer.Option(
        False, "--run-tests", help="Execute the artifact's own tests (runs untrusted code)"
    ),
    skip_gate: bool = typer.Option(False, "--skip-gate", help="Skip the quality gate (audited)"),
) -> None:
    """Promote channel/trust; the version never changes (spec §154)."""
    with open_registry(project, user) as reg:
        rec = reg.promote(
            ref,
            channel=channel,
            trust=TrustStatus(trust) if trust else None,
            reason=reason,
            reviewed_by=reviewed_by,
            run_gate=not skip_gate,
            run_tests=run_tests or None,
        )
    typer.echo(
        f"{rec.version_uri}: trust={rec.trust.value} channel={rec.channel} revision={rec.revision}"
    )


def _state_command(name: str, help_text: str, action: str) -> None:
    @registry_app.command(name, help=help_text)
    @guarded
    def _cmd(
        ref: str = typer.Argument(..., help="ns/name@version or range"),
        project: Path = _PROJECT,
        user: bool = _USER,
        reason: str | None = typer.Option(None, "--reason"),
        replacement: str | None = typer.Option(
            None, "--replacement", help="(deprecate) replacement artifact"
        ),
    ) -> None:
        with open_registry(project, user) as reg:
            if action == "yank":
                recs = reg.yank(ref, reason)
            elif action == "unyank":
                recs = reg.unyank(ref)
            elif action == "deprecate":
                recs = reg.deprecate(ref, reason, replacement)
            elif action == "quarantine":
                recs = reg.quarantine(ref, reason)
            else:
                recs = reg.release_quarantine(ref, reason or "released")
        for r in recs:
            typer.echo(
                f"{r.version_uri}: {r.lifecycle.value} (trust {r.trust.value}, channel {r.channel})"
            )

    _cmd.__name__ = f"{action}_cmd"


_state_command("yank", "Yank a version (kept for locks; avoided by new resolution)", "yank")
_state_command("unyank", "Reverse a yank", "unyank")
_state_command("deprecate", "Deprecate an artifact or version", "deprecate")
_state_command(
    "quarantine", "Quarantine a version (never selected; materialization refused)", "quarantine"
)
_state_command("release-quarantine", "Leave quarantine (audited; returns as restricted)", "release")


@alias_app.command("set")
@guarded
def alias_set(
    alias: str = typer.Argument(...),
    ref: str = typer.Argument(...),
    project: Path = _PROJECT,
    user: bool = _USER,
) -> None:
    with open_registry(project, user) as reg:
        reg.alias_set(alias, ref)
    typer.echo(f"{alias} -> {ref}")


@alias_app.command("list")
@guarded
def alias_list(project: Path = _PROJECT, user: bool = _USER, as_json: bool = _JSON) -> None:
    with open_registry(project, user) as reg:
        rows = reg.aliases()
    _echo_json(rows) if as_json else _table(
        "aliases",
        ["alias", "artifact"],
        [[r["alias"], f"{r['kind']}/{r['namespace']}/{r['name']}"] for r in rows],
    )


@alias_app.command("remove")
@guarded
def alias_remove(
    alias: str = typer.Argument(...), project: Path = _PROJECT, user: bool = _USER
) -> None:
    with open_registry(project, user) as reg:
        typer.echo("removed" if reg.alias_remove(alias) else "no such alias")


# --------------------------------------------------------------------------- activation


@registry_app.command("activate")
@guarded
def activate_cmd(
    ref: str = typer.Argument(..., help="Exact version, e.g. core/graph-review@2.1.3"),
    project: Path = _PROJECT,
    user: bool = _USER,
    mode: str | None = typer.Option(None, "--mode", help="copy | link"),
) -> None:
    """Materialize and activate a version for the project (registered != activated)."""
    from ananke.plexus.registry.activation import activate

    with open_registry(project, user) as reg:
        res = activate(reg, project, ref, mode=mode)
    typer.echo(f"Activated {res.uri}@{res.version} -> {res.path} ({res.mode})")


@registry_app.command("deactivate")
@guarded
def deactivate_cmd(
    ref: str = typer.Argument(...), project: Path = _PROJECT, user: bool = _USER
) -> None:
    from ananke.plexus.registry.activation import deactivate

    with open_registry(project, user) as reg:
        typer.echo("deactivated" if deactivate(reg, project, ref) else "not active")


@registry_app.command("translate")
@guarded
def translate_cmd(
    ref: str = typer.Argument(...),
    runtime: str = typer.Option(..., "--runtime"),
    project: Path = _PROJECT,
    user: bool = _USER,
) -> None:
    """Show the runtime-native descriptor for a version (identity never changes)."""
    from ananke.plexus.registry.translate import translate

    with open_registry(project, user) as reg:
        _echo_json(translate(reg.exact_version(ref), runtime))


# --------------------------------------------------------------------------- integrity & ops


def _print_checks(rep: Any, as_json: bool) -> int:
    if as_json:
        _echo_json({"ok": rep.ok, "checks": [c.model_dump() for c in rep.checks]})
    else:
        for c in rep.checks:
            mark = "✓" if c.ok else ("✗" if c.severity == "error" else "!")
            typer.echo(f"{mark} {c.name}: {c.detail}")
    return 0 if rep.ok else 4


@registry_app.command("verify")
@guarded
def verify_cmd(
    project: Path = _PROJECT,
    user: bool = _USER,
    fast: bool = typer.Option(False, "--fast", help="Skip payload hash re-verification"),
    as_json: bool = _JSON,
) -> None:
    """Integrity checks: SQLite, blobs, graph, aliases, index, lockfiles."""
    with open_registry(project, user) as reg:
        code = _print_checks(reg.verify(deep=not fast), as_json)
    if code:
        raise typer.Exit(code=code)


@registry_app.command("doctor")
@guarded
def doctor_cmd(project: Path = _PROJECT, user: bool = _USER, as_json: bool = _JSON) -> None:
    """Operational health: verify + WAL, cache, resolver, templates, importers, accelerators."""
    with open_registry(project, user) as reg:
        code = _print_checks(reg.doctor(), as_json)
    if code:
        raise typer.Exit(code=code)


@registry_app.command("export")
@guarded
def export_cmd(
    output: Path | None = typer.Option(None, "--output", "-o"),
    project: Path = _PROJECT,
    user: bool = _USER,
    compression: str = typer.Option("auto", "--compression", help="auto | gzip | zstd"),
) -> None:
    """Export a portable, database-independent archive."""
    with open_registry(project, user) as reg:
        res = reg.export_archive(output, compression=compression)
    typer.echo(f"Exported {res.versions} version(s), {res.blobs} blob(s) -> {res.path}")


@registry_app.command("import")
@guarded
def import_cmd(
    archive: Path = typer.Argument(...),
    project: Path = _PROJECT,
    user: bool = _USER,
    dry_run: bool = typer.Option(False, "--dry-run"),
    skip_policy: bool = typer.Option(False, "--skip-policy"),
    reset_trust: bool = typer.Option(
        False, "--reset-trust", help="Do not carry over trust/channel"
    ),
) -> None:
    """Import an export archive (validates checksums, schema, immutability, policy)."""
    reg = open_registry(project, user, create=True)
    try:
        res = reg.import_archive(
            archive, dry_run=dry_run, skip_policy=skip_policy, preserve_trust=not reset_trust
        )
    finally:
        reg.close()
    typer.echo(
        f"{'Would import' if dry_run else 'Imported'} {res.versions_added} version(s) ({res.versions_existing} already present)"
    )
    for w in res.warnings:
        typer.echo(f"warning: {w}")


@registry_app.command("backup")
@guarded
def backup_cmd(
    output_dir: Path | None = typer.Option(None, "--output-dir"),
    project: Path = _PROJECT,
    user: bool = _USER,
) -> None:
    from ananke.plexus.registry.portable import backup

    with open_registry(project, user) as reg:
        typer.echo(f"Backup: {backup(reg, output_dir)}")


@registry_app.command("restore")
@guarded
def restore_cmd(
    archive: Path = typer.Argument(...),
    target: Path = typer.Option(..., "--into", help="Empty registry directory to restore into"),
    force: bool = typer.Option(False, "--force"),
) -> None:
    from ananke.plexus.registry.portable import restore

    typer.echo(f"Restored into {restore(archive, target, force=force)}")


@registry_app.command("gc")
@guarded
def gc_cmd(
    project: Path = _PROJECT,
    user: bool = _USER,
    apply: bool = typer.Option(False, "--apply", help="Actually delete (default is a dry run)"),
    grace_days: float = typer.Option(7.0, "--grace-days"),
) -> None:
    """Delete unreachable blobs older than the grace period."""
    with open_registry(project, user) as reg:
        rep = reg.gc(dry_run=not apply, grace_seconds=grace_days * 86400)
    typer.echo(
        f"{'Deleted' if apply else 'Would delete'}: {len(rep.deleted) if apply else len(rep.unreachable) - rep.within_grace} blob(s), "
        f"{rep.within_grace} within grace, {rep.reachable} reachable, {rep.bytes_freed} bytes"
    )
    if rep.materialized_removed:
        typer.echo(
            f"materialized dirs {'removed' if apply else 'removable'}: {len(rep.materialized_removed)}"
        )


@registry_app.command("rebuild-index")
@guarded
def rebuild_index_cmd(project: Path = _PROJECT, user: bool = _USER) -> None:
    with open_registry(project, user) as reg:
        typer.echo(f"Rebuilt search index: {reg.rebuild_index()} row(s)")


@registry_app.command("snapshot")
@guarded
def snapshot_cmd(
    project: Path = _PROJECT, user: bool = _USER, note: str | None = typer.Option(None, "--note")
) -> None:
    with open_registry(project, user) as reg:
        typer.echo(reg.create_snapshot(note))


@registry_app.command("events")
@guarded
def events_cmd(
    project: Path = _PROJECT,
    user: bool = _USER,
    limit: int = typer.Option(50, "--limit"),
    type_prefix: str | None = typer.Option(None, "--type"),
) -> None:
    """Show the immutable registry audit log."""
    with open_registry(project, user) as reg:
        rows = reg.store.list_events(limit=limit, type_prefix=type_prefix)
    _table(
        "events",
        ["seq", "type", "artifact", "actor", "at"],
        [
            [
                str(e["seq"]),
                e["event_type"],
                e["artifact_uri"] or "",
                e["actor"] or "",
                e["created_at"],
            ]
            for e in rows
        ],
    )


@registry_app.command("report")
@guarded
def report_cmd(
    name: str = typer.Argument(
        ..., help="inventory|licenses|trust|stale|deprecated|unused|security|compatibility"
    ),
    project: Path = _PROJECT,
    user: bool = _USER,
    fmt: str = typer.Option("table", "--format", help="table | json | csv"),
    days: int = typer.Option(90, "--days", help="(stale) days since last verification"),
) -> None:
    with open_registry(project, user) as reg:
        table = reg.report(name, **({"stale_days": days} if name == "stale" else {}))
    if fmt == "json":
        typer.echo(table.to_json())
    elif fmt == "csv":
        typer.echo(table.to_csv(), nl=False)
    else:
        _table(table.title, table.columns, table.rows)
        if table.note:
            typer.echo(table.note)


@analytics_app.command("build")
@guarded
def analytics_build(
    project: Path = _PROJECT,
    user: bool = _USER,
    output_dir: Path | None = typer.Option(None, "--output-dir"),
) -> None:
    """Write JSONL projections (and registry.duckdb when DuckDB is installed)."""
    from ananke.plexus.registry.reports import build_analytics

    with open_registry(project, user) as reg:
        res = build_analytics(reg, output_dir)
    typer.echo(f"Analytics: {res.directory} ({', '.join(res.files)})")
    if res.note:
        typer.echo(res.note)


@analytics_app.command("query")
@guarded
def analytics_query(
    question: str | None = typer.Argument(
        None, help="approved-skills-per-runtime | agents-on-deprecated | network-skills | ..."
    ),
    project: Path = _PROJECT,
    user: bool = _USER,
    days: int = typer.Option(90, "--days", help="Staleness window for not-verified-recently"),
    engine: str = typer.Option("auto", "--engine", help="auto | duckdb | sqlite"),
    as_json: bool = _JSON,
) -> None:
    """Answer a built-in analytics question (spec §146); omit the name to list them."""
    from ananke.plexus.registry.reports import QUESTIONS, answer_question

    if question is None:
        for q in QUESTIONS.values():
            typer.echo(f"{q.name:30} {q.title}")
        return
    with open_registry(project, user) as reg:
        result = answer_question(reg, question, days=days, engine=engine)
    if as_json:
        _echo_json(result.model_dump(mode="json"))
        return
    if not result.rows:
        typer.echo(f"{result.title}: no rows ({result.engine})")
        return
    _table(
        f"{result.title} ({result.engine})",
        result.columns,
        [["" if v is None else str(v) for v in row] for row in result.rows],
    )


@registry_app.command("duplicates")
@guarded
def duplicates_cmd(project: Path = _PROJECT, user: bool = _USER) -> None:
    """Surface potential duplicates (same source, digest, tool schema or capability set)."""
    from ananke.plexus.registry.duplicates import find_duplicates

    with open_registry(project, user) as reg:
        groups = find_duplicates(reg)
    if not groups:
        typer.echo("No potential duplicates.")
    for g in groups:
        typer.echo(f"{g.reason}: {', '.join(g.artifacts)}")


@registry_app.command("recommend")
@guarded
def recommend_cmd(
    capability: str = typer.Argument(...),
    project: Path = _PROJECT,
    user: bool = _USER,
    limit: int = typer.Option(5, "--limit"),
) -> None:
    """Policy-eligible skills providing a capability (deterministic order)."""
    from ananke.plexus.registry.search import recommend_skills

    with open_registry(project, user) as reg:
        hits = recommend_skills(reg, capability, limit)
    for i, h in enumerate(hits, 1):
        typer.echo(f"{i}. {h.namespace}/{h.name}@{h.version}  ({h.trust.value})")
    if not hits:
        typer.echo("No eligible skills provide that capability.")


@registry_app.command("watch")
@guarded
def watch_cmd(
    project: Path = _PROJECT,
    user: bool = _USER,
    once: bool = typer.Option(False, "--once"),
    interval: float = typer.Option(2.0, "--interval", help="Polling interval (poll backend)"),
    backend: str = typer.Option(
        "auto", "--backend", help="auto | native (needs registry-watch) | poll"
    ),
) -> None:
    """Detect changes under .ananke/skills and .ananke/agents (publish stays manual)."""
    if backend not in {"auto", "native", "poll"}:
        raise typer.BadParameter("backend must be auto, native or poll")
    from ananke.plexus.registry.watcher import scan_once, watch

    with open_registry(project, user, create=True) as reg:

        def show(rep: Any) -> None:
            for c in rep.changes:
                typer.echo(
                    f"pending: {c.uri or c.path} [{c.action}] {c.suggested_version or ''} {c.message}".rstrip()
                )

        if once:
            show(scan_once(reg))
            return
        typer.echo("Watching (Ctrl+C to stop)…")
        with contextlib.suppress(KeyboardInterrupt):  # pragma: no cover
            watch(reg, interval=interval, on_change=show, backend=backend)  # type: ignore[arg-type]


@registry_app.command("benchmark")
@guarded
def benchmark_cmd(
    size: int = typer.Option(500, "--size", min=1, help="Artifacts to seed (spec: 10000)"),
    iterations: int = typer.Option(20, "--iterations", min=1),
    only: list[str] = typer.Option([], "--only", help="Run just these benchmarks (repeatable)"),
    save_baseline: Path | None = typer.Option(None, "--save-baseline"),
    baseline: Path | None = typer.Option(None, "--baseline", help="Compare against a saved run"),
    tolerance: float = typer.Option(1.5, "--tolerance", min=1.0, help="Allowed slowdown factor"),
    as_json: bool = _JSON,
) -> None:
    """Time the spec's registry operations; optionally fail on regressions vs a baseline."""
    from ananke.plexus.registry.bench import compare, load_report, run_benchmarks, save_report

    report = run_benchmarks(size, iterations, only=set(only) or None)
    if save_baseline:
        save_report(report, save_baseline)
    regressions = compare(report, load_report(baseline), tolerance) if baseline else []
    if as_json:
        _echo_json(
            {
                "report": report.model_dump(mode="json"),
                "regressions": [r.model_dump(mode="json") for r in regressions],
            }
        )
    else:
        _table(
            f"registry benchmark ({report.size} artifacts, sqlite {report.sqlite}, fts={report.fts})",
            ["operation", "median ms", "p95 ms", "goal ms", "goal"],
            [
                [
                    r.name,
                    f"{r.median_ms:.2f}",
                    f"{r.p95_ms:.2f}",
                    "" if r.target_ms is None else f"{r.target_ms:g}",
                    "" if r.within_target is None else ("met" if r.within_target else "missed"),
                ]
                for r in report.results
            ],
        )
        for reg_ in regressions:
            typer.echo(
                f"regression: {reg_.name} {reg_.baseline_ms:.2f}ms -> {reg_.current_ms:.2f}ms (x{reg_.ratio})"
            )
    if regressions:
        raise typer.Exit(code=1)


@registry_app.command("schema")
@guarded
def schema_cmd(
    name: str | None = typer.Argument(
        None, help="skill-manifest | agent-manifest | bundle-manifest | registry-export | lockfile"
    ),
    output: Path | None = typer.Option(
        None, "--output", help="Write all schemas into this directory"
    ),
) -> None:
    """Publish JSON Schemas for IDE validation."""
    from ananke.plexus.registry.schemas import registry_json_schemas, write_schemas

    if output:
        for p in write_schemas(output):
            typer.echo(str(p))
        return
    schemas = registry_json_schemas()
    if name is None:
        typer.echo("\n".join(sorted(schemas)))
        return
    if name not in schemas:
        raise typer.BadParameter(f"unknown schema {name!r}")
    _echo_json(schemas[name])


@registry_app.command("serve")
@guarded
def serve_cmd(
    project: Path = _PROJECT,
    user: bool = _USER,
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8765, "--port"),
    token_env: str | None = typer.Option(
        None,
        "--token-env",
        help="Name of an environment variable holding a bearer token clients must send "
        "(required to listen on anything but loopback)",
    ),
) -> None:
    """Serve generated docs and a read-only JSON API (optional; static HTML is primary)."""
    import os

    from ananke.plexus.registry.server import serve

    token = None
    if token_env:
        token = os.environ.get(token_env)
        if not token:
            raise typer.BadParameter(f"environment variable {token_env} is not set")
    reg = open_registry(project, user)
    root, proj = reg.root, reg.project_root
    reg.close()
    auth = "bearer token required" if token else "no auth, loopback only"
    typer.echo(f"Serving {root} on http://{host}:{port} (read-only, {auth}, Ctrl+C to stop)")
    serve(root, host, port, project_root=proj, token=token)


# --------------------------------------------------------------------------- docs


@docs_app.command("build")
@guarded
def docs_build(
    project: Path = _PROJECT,
    user: bool = _USER,
    output: Path | None = typer.Option(None, "--output"),
    single_file: bool = typer.Option(False, "--single-file", help="Also/only write registry.html"),
    full: bool = typer.Option(False, "--full", help="Ignore the incremental cache"),
) -> None:
    """Generate the static multi-page registry site."""
    with open_registry(project, user) as reg:
        if single_file:
            out = reg.dump_docs(output / "registry.html" if output and output.is_dir() else output)
            typer.echo(f"Single-file registry: {out}")
            return
        res = reg.build_docs(output, incremental=not full)
    typer.echo(
        f"Docs: {res.out_dir} — {res.total_pages} page(s), {res.pages_written} written, {res.pages_skipped} unchanged, {res.pages_removed} removed"
    )


@docs_app.command("dump")
@guarded
def docs_dump(
    project: Path = _PROJECT,
    user: bool = _USER,
    output: Path | None = typer.Option(None, "--output", "-o", help="e.g. ananke-registry.html"),
) -> None:
    """One self-contained offline HTML file with every artifact, version and diagram."""
    with open_registry(project, user) as reg:
        typer.echo(f"Registry dump: {reg.dump_docs(output)}")


# --------------------------------------------------------------------------- policy / lock


@policy_app.command("show")
@guarded
def policy_show(project: Path = _PROJECT, user: bool = _USER, as_json: bool = _JSON) -> None:
    with open_registry(project, user) as reg:
        typer.echo(
            reg.policy.model_dump_json(indent=2) if as_json else dump_policy_toml(reg.policy)
        )


@policy_app.command("init")
@guarded
def policy_init(
    preset: str = typer.Option("enterprise", "--preset", help="default | enterprise"),
    project: Path = _PROJECT,
    user: bool = _USER,
    force: bool = typer.Option(False, "--force"),
) -> None:
    """Write a policy preset (enterprise = HighestApproved, approved-only, no unknown licenses)."""
    if preset not in PRESETS:
        raise typer.BadParameter(f"unknown preset {preset!r}")
    with open_registry(project, user, create=True) as reg:
        pp = policy_path(reg.root)
        if pp.exists() and not force:
            raise typer.BadParameter(f"{pp} exists (use --force)")
        pp.write_text(dump_policy_toml(PRESETS[preset]()), encoding="utf-8")
        typer.echo(f"Wrote {pp}")


# --------------------------------------------------------------------------- federation

_SOURCE = typer.Option("enterprise", "--source", help="Configured remote source name")
_ALLOW_NET = typer.Option(
    False, "--allow-network", help="Override [remote_sources] policy for this call (if permitted)"
)


@remote_app.command("list")
@guarded
def remote_list(project: Path = _PROJECT, as_json: bool = _JSON) -> None:
    """Show every registry source, its priority and whether it can be reached."""
    from ananke.plexus.registry.sources import source_statuses

    statuses = source_statuses(project)
    if as_json:
        _echo_json([s.model_dump(mode="json") for s in statuses])
        return
    _table(
        "registry sources (priority order)",
        ["source", "type", "enabled", "available", "location", "note"],
        [
            [
                s.name,
                s.type,
                "yes" if s.enabled else "no",
                "yes" if s.available else "no",
                s.location,
                s.note,
            ]
            for s in statuses
        ],
    )


@remote_app.command("search")
@guarded
def remote_search(
    query: str = typer.Argument(""),
    source: str = _SOURCE,
    kind: str | None = typer.Option(None, "--kind"),
    limit: int = typer.Option(20, "--limit"),
    allow_network: bool = _ALLOW_NET,
    project: Path = _PROJECT,
    user: bool = _USER,
    as_json: bool = _JSON,
) -> None:
    """Search a remote registry (read-only network call, policy-gated)."""
    from ananke.plexus.registry.remote import open_remote

    with open_registry(project, user) as reg:
        remote = open_remote(reg.policy, source, cli_override=allow_network)
    hits = remote.search(query, kind=kind, limit=limit)
    if as_json:
        _echo_json(hits)
        return
    _table(
        f"{source}: {query or '*'}",
        ["artifact", "version", "kind", "trust", "summary"],
        [
            [
                f"{h.get('namespace')}/{h.get('name')}",
                str(h.get("version")),
                str(h.get("kind")),
                str(h.get("trust")),
                str(h.get("summary", ""))[:50],
            ]
            for h in hits
        ],
    )


@remote_app.command("pull")
@guarded
def remote_pull(
    ref: str = typer.Argument(..., help="ns/name[@range] resolved by the remote's policy"),
    source: str = _SOURCE,
    kind: str | None = typer.Option(None, "--kind"),
    runtime: str | None = typer.Option(None, "--runtime"),
    no_deps: bool = typer.Option(False, "--no-deps", help="Pull only the requested version"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    allow_network: bool = _ALLOW_NET,
    project: Path = _PROJECT,
    user: bool = _USER,
    as_json: bool = _JSON,
) -> None:
    """Fetch a verified version (and its dependencies) into the local registry.

    Pulled versions arrive as `discovered` in the `candidate` channel; promote them locally.
    """
    from ananke.plexus.registry.remote import open_remote, pull

    with open_registry(project, user) as reg:
        remote = open_remote(reg.policy, source, cli_override=allow_network)
        result = pull(
            reg,
            remote,
            ref,
            kind=kind,
            runtime=runtime,
            with_dependencies=not no_deps,
            dry_run=dry_run,
        )
    if as_json:
        _echo_json(result.model_dump(mode="json"))
        return
    _table(
        f"pull {ref} from {source}",
        ["action", "artifact", "version"],
        [[i.action, i.uri.replace("ananke://", ""), i.version] for i in result.items],
    )
    for w in result.warnings:
        typer.echo(f"  warning: {w}")
    if not dry_run:
        typer.echo("Pulled versions are `discovered`; run `ananke registry promote` after review.")


# --------------------------------------------------------------------------- signing


def _print_signatures(statuses: list[Any]) -> None:
    _table(
        "signatures",
        ["key", "signer", "verified", "detail"],
        [[s.key_id, s.signer or "", "yes" if s.verified else "no", s.reason] for s in statuses],
    )


@key_app.command("generate")
@guarded
def key_generate(
    output: Path = typer.Option(
        Path(".ananke/secrets/registry-signing.key"),
        "--output",
        "-o",
        help="Private key file (created 0600; keep it out of git)",
    ),
    trust: bool = typer.Option(False, "--trust", help="Also trust the public key in this registry"),
    signer: str | None = typer.Option(None, "--signer", help="Name recorded for the key"),
    force: bool = typer.Option(False, "--force", help="Replace an existing key file"),
    project: Path = _PROJECT,
    user: bool = _USER,
) -> None:
    """Generate an Ed25519 signing key. Only the *public* key is ever written to policy."""
    from ananke.plexus.registry.signing import generate_keypair

    key_id, public = generate_keypair(output, overwrite=force)
    typer.echo(f"Private key: {output} (mode 0600 — never commit it)")
    typer.echo(f"Key id:      {key_id}")
    typer.echo(f"Public key:  {public}")
    if trust:
        _trust_key(project, user, key_id, public, signer)
    else:
        typer.echo(
            f"Trust it with: ananke registry key trust {key_id} --public-key {public}", err=False
        )


def _trust_key(project: Path, user: bool, key_id: str, public: str, signer: str | None) -> None:
    from ananke.plexus.registry.signing import decode_public_key, validate_key_id

    validate_key_id(key_id)
    decode_public_key(public)
    with open_registry(project, user) as reg:
        policy = load_policy(reg.root)
        policy.signing.trusted_keys[key_id] = TrustedKey(public_key=public, signer=signer)
        policy_path(reg.root).write_text(dump_policy_toml(policy), encoding="utf-8")
    typer.echo(f"Trusted key {key_id} (policy rewritten; comments are not preserved)")


@key_app.command("trust")
@guarded
def key_trust(
    key_id: str = typer.Argument(..., help="Key id, e.g. ed25519-1a2b3c4d5e6f7a8b"),
    public_key: str = typer.Option(..., "--public-key", help="Base64 raw Ed25519 public key"),
    signer: str | None = typer.Option(None, "--signer"),
    project: Path = _PROJECT,
    user: bool = _USER,
) -> None:
    """Trust a public key: signatures by it can then satisfy `require_signature`."""
    _trust_key(project, user, key_id, public_key, signer)


@key_app.command("revoke")
@guarded
def key_revoke(
    key_id: str = typer.Argument(...), project: Path = _PROJECT, user: bool = _USER
) -> None:
    """Revoke a key: everything it signed stops counting as verified (immediately)."""
    with open_registry(project, user) as reg:
        policy = load_policy(reg.root)
        if key_id not in policy.signing.trusted_keys:
            raise NotFoundError(f"key {key_id!r} is not in the policy")
        policy.signing.trusted_keys[key_id].revoked = True
        policy_path(reg.root).write_text(dump_policy_toml(policy), encoding="utf-8")
    typer.echo(f"Revoked {key_id}")


@key_app.command("list")
@guarded
def key_list(project: Path = _PROJECT, user: bool = _USER, as_json: bool = _JSON) -> None:
    with open_registry(project, user) as reg:
        keys = reg.policy.signing.trusted_keys
        if as_json:
            _echo_json({k: v.model_dump(mode="json") for k, v in keys.items()})
            return
        _table(
            "trusted keys",
            ["key id", "signer", "revoked"],
            [[k, v.signer or "", "yes" if v.revoked else "no"] for k, v in sorted(keys.items())],
        )


@registry_app.command("sign")
@guarded
def sign_cmd(
    ref: str = typer.Argument(..., help="Exact version, e.g. core/graph-review@1.0.0"),
    key: Path = typer.Option(..., "--key", help="Ed25519 private key file (PEM, mode 0600)"),
    key_id: str | None = typer.Option(None, "--key-id"),
    signer: str | None = typer.Option(None, "--signer"),
    project: Path = _PROJECT,
    user: bool = _USER,
) -> None:
    """Sign a registered version (URI + payload digest). Needs `registry-signing`."""
    with open_registry(project, user) as reg:
        status = reg.sign(ref, key, key_id=key_id, signer=signer)
    _print_signatures([status])


@registry_app.command("signatures")
@guarded
def signatures_cmd(
    ref: str = typer.Argument(...),
    project: Path = _PROJECT,
    user: bool = _USER,
    as_json: bool = _JSON,
) -> None:
    """Show stored signatures and whether each verifies against the policy's trusted keys."""
    with open_registry(project, user) as reg:
        statuses = reg.signatures(ref)
    if as_json:
        _echo_json([s.model_dump(mode="json") for s in statuses])
    else:
        _print_signatures(statuses)
    if not any(s.verified for s in statuses):
        raise typer.Exit(code=1)


@registry_app.command("lock")
@guarded
def lock_cmd(
    refs: list[str] = typer.Argument(
        None, help="Requirements to lock (default: project requirements)"
    ),
    project: Path = _PROJECT,
    user: bool = _USER,
    output: Path | None = typer.Option(None, "--output"),
    update: bool = typer.Option(False, "--update", help="Ignore existing pins"),
    runtime: str | None = typer.Option(None, "--runtime"),
    mode: str | None = typer.Option(None, "--mode"),
) -> None:
    """Resolve requirements and write a deterministic ``ananke.lock``."""
    _do_sync(
        project,
        user,
        output,
        update,
        runtime,
        mode,
        refs or [],
        activate=False,
        release=False,
        dry_run=False,
    )


@registry_app.command("verify-lock")
@guarded
def lock_verify(
    path: Path | None = typer.Argument(None, help="Lockfile (default: ./ananke.lock)"),
    project: Path = _PROJECT,
    user: bool = _USER,
) -> None:
    """Check every locked artifact exists, matches its digest and is not quarantined."""
    from ananke.plexus.registry.lockfile import LOCK_NAME, load_lock, verify_lock

    lock_file = path or (project / LOCK_NAME)
    with open_registry(project, user) as reg:
        checks = verify_lock(reg, load_lock(lock_file))
    for c in checks:
        typer.echo(f"{'✓' if c.ok else '✗'} {c.id}: {c.detail}")
    if not all(c.ok for c in checks):
        raise typer.Exit(code=4)


def _do_sync(
    project: Path,
    user: bool,
    output: Path | None,
    update: bool,
    runtime: str | None,
    mode: str | None,
    refs: list[str],
    *,
    activate: bool,
    release: bool,
    dry_run: bool,
) -> None:
    from ananke.plexus.registry.sync import sync

    try:
        reg = open_registry(project, user)
    except NotInitializedError:
        reg = open_registry(project, user, create=True)
    try:
        extra = [Requirement.parse(r) for r in refs]
        res = sync(
            reg,
            project,
            lock_path=output,
            update=update,
            env=_env(runtime),
            options=ResolutionOptions(mode=ResolutionMode(mode)) if mode else None,
            extra=extra,
            activate_all=activate,
            release=release,
            dry_run=dry_run,
        )
    except RegistryError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=next((c for cls, c in _EXIT if isinstance(exc, cls)), 1)) from None
    finally:
        reg.close()
    typer.echo(
        f"{'Would write' if dry_run else ('Wrote' if res.written else 'Up to date:')} {res.lock_path}"
    )
    for change in res.changes:
        typer.echo(f"  {change}")
    for w in res.warnings:
        typer.echo(f"  warning: {w}")
    for a in res.activated:
        typer.echo(f"  activated {a}")


def sync_command(
    project: Path = _PROJECT,
    user: bool = _USER,
    output: Path | None = typer.Option(
        None, "--lock", help="Lockfile path (default ./ananke.lock)"
    ),
    update: bool = typer.Option(False, "--update", help="Re-resolve ignoring pins"),
    runtime: str | None = typer.Option(None, "--runtime"),
    mode: str | None = typer.Option(None, "--mode"),
    activate: bool = typer.Option(
        False, "--activate", help="Materialize and activate everything locked"
    ),
    release: bool = typer.Option(
        False, "--release", help="Release profile: forbid path overrides/dev links"
    ),
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    """Resolve [tool.ananke.agent]/[tool.ananke.skills] from pyproject.toml and write ananke.lock."""
    _do_sync(
        project,
        user,
        output,
        update,
        runtime,
        mode,
        [],
        activate=activate,
        release=release,
        dry_run=dry_run,
    )


@registry_app.command("publish")
@guarded
def publish_cmd(
    path: Path = typer.Argument(...),
    project: Path = _PROJECT,
    user: bool = _USER,
    version: str | None = typer.Option(None, "--version"),
    channel: str | None = typer.Option(None, "--channel"),
    namespace: str | None = typer.Option(None, "--namespace"),
    license: str | None = typer.Option(None, "--license"),
    run_tests: bool = typer.Option(False, "--run-tests"),
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    """validate → test → hash → resolve deps → conflict check → policy → register → docs."""
    from ananke.plexus.registry.sync import publish

    reg = open_registry(project, user, create=True)
    try:
        rep = publish(
            reg,
            path,
            version=version,
            channel=channel,
            namespace=namespace,
            license=license,
            run_tests=run_tests,
            dry_run=dry_run,
        )
    finally:
        reg.close()
    for s in rep.steps:
        typer.echo(f"{'✓' if s.ok else '✗'} {s.name}: {s.detail}")
    if not dry_run and not rep.ok:
        raise typer.Exit(code=1)
    if not dry_run and rep.result:
        typer.echo(f"Published {rep.result.version_uri}")


@registry_app.command("link")
@guarded
def link_cmd(
    path: Path = typer.Argument(...), project: Path = _PROJECT, user: bool = _USER
) -> None:
    """Dev mode: use a working copy without publishing an immutable version."""
    from ananke.plexus.registry.sync import link

    with open_registry(project, user, create=True) as reg:
        res = link(reg, project, path)
    typer.echo(f"Linked {res.uri}@{res.version} -> {res.path}")


@registry_app.command("unlink")
@guarded
def unlink_cmd(
    ref: str = typer.Argument(...), project: Path = _PROJECT, user: bool = _USER
) -> None:
    from ananke.plexus.registry.sync import unlink

    with open_registry(project, user, create=True) as reg:
        typer.echo("unlinked" if unlink(reg, project, ref) else "not linked")


@registry_app.command("evidence")
@guarded
def evidence_cmd(
    project: Path = _PROJECT,
    run_dir: Path | None = typer.Option(
        None, "--run-dir", help="Write registry-capabilities.json into this evidence directory"
    ),
) -> None:
    """Capability versions in play (from ananke.lock) as evidence."""
    from ananke.plexus.registry.evidence import project_capability_evidence, write_run_evidence

    if run_dir:
        out = write_run_evidence(project, run_dir)
        typer.echo(f"Wrote {out}" if out else "No ananke.lock found")
        return
    payload = project_capability_evidence(project)
    _echo_json(payload) if payload else typer.echo("No ananke.lock found")


# --------------------------------------------------------------------------- skill / agent shortcuts


def _kind_shortcuts(app: typer.Typer, kind: ArtifactKind) -> None:
    k = kind.value

    @app.command("list", help=f"List {kind.plural}")
    @guarded
    def _list(
        project: Path = _PROJECT,
        user: bool = _USER,
        versions: bool = typer.Option(False, "--versions"),
        as_json: bool = _JSON,
    ) -> None:
        with open_registry(project, user) as reg:
            if versions:
                recs = reg.list_versions(k)
                rows = [
                    [
                        f"{r.namespace}/{r.name}",
                        r.version,
                        r.lifecycle.value,
                        r.channel,
                        r.trust.value,
                    ]
                    for r in recs
                ]
                _echo_json(
                    [record_to_dict(r, None, detail=False) for r in recs]
                ) if as_json else _table(
                    kind.plural, ["artifact", "version", "lifecycle", "channel", "trust"], rows
                )
            else:
                arts = reg.list_artifacts(k)
                _echo_json(
                    [
                        {"uri": a.uri, "versions": a.version_count, "latest": a.latest_version}
                        for a in arts
                    ]
                ) if as_json else _table(
                    kind.plural,
                    ["artifact", "versions", "latest"],
                    [
                        [f"{a.namespace}/{a.name}", str(a.version_count), a.latest_version or ""]
                        for a in arts
                    ],
                )

    @app.command("show", help=f"Show a {k}")
    @guarded
    def _show_cmd(
        ref: str = typer.Argument(...),
        project: Path = _PROJECT,
        user: bool = _USER,
        as_json: bool = _JSON,
    ) -> None:
        with open_registry(project, user) as reg:
            _show(reg, ref, as_json, kind=k)

    @app.command("search", help=f"Search {kind.plural}")
    @guarded
    def _search(
        query: str = typer.Argument(""),
        project: Path = _PROJECT,
        user: bool = _USER,
        runtime: str | None = typer.Option(None, "--runtime"),
        capability: list[str] = typer.Option([], "--capability"),
        trust: str | None = typer.Option(None, "--trust"),
        skill: str | None = typer.Option(None, "--skill", help="(agents) composes this skill"),
        as_json: bool = _JSON,
    ) -> None:
        from ananke.plexus.registry.search import search, search_agents

        with open_registry(project, user) as reg:
            if kind is ArtifactKind.AGENT and (skill or capability):
                hits = search_agents(
                    reg,
                    skill=skill,
                    runtime=runtime,
                    capability=capability[0] if capability else None,
                )
            else:
                hits = search(
                    reg, query, kind=k, runtime=runtime, capability=capability or None, trust=trust
                )
        if as_json:
            _echo_json([h.model_dump(mode="json") for h in hits])
        else:
            _table(
                f"{kind.plural}",
                ["artifact", "version", "trust", "summary"],
                [
                    [f"{h.namespace}/{h.name}", h.version, h.trust.value, h.summary[:50]]
                    for h in hits
                ],
            )

    @app.command("register", help=f"Register a {k} directory")
    @guarded
    def _register(
        path: Path = typer.Argument(...),
        project: Path = _PROJECT,
        user: bool = _USER,
        version: str | None = typer.Option(None, "--version"),
        namespace: str | None = typer.Option(None, "--namespace"),
        license: str | None = typer.Option(None, "--license"),
        dry_run: bool = typer.Option(False, "--dry-run"),
    ) -> None:
        from ananke.plexus.registry.learn import learn

        reg = open_registry(project, user, create=True)
        try:
            code = _print_learn(
                learn(
                    reg,
                    str(path),
                    kind=k,
                    version=version,
                    namespace=namespace,
                    license=license,
                    dry_run=dry_run,
                    force=True,
                ),
                False,
            )
        finally:
            reg.close()
        if code:
            raise typer.Exit(code=code)

    @app.command("resolve", help=f"Resolve a {k} requirement")
    @guarded
    def _resolve(
        ref: str = typer.Argument(...),
        project: Path = _PROJECT,
        user: bool = _USER,
        runtime: str | None = typer.Option(None, "--runtime"),
        explain: bool = typer.Option(False, "--explain"),
    ) -> None:
        with open_registry(project, user) as reg:
            result = Resolver(reg, env=_env(runtime)).resolve(Requirement.parse(ref, k), kind=k)
        typer.echo(
            result.explain()
            if explain or not result.ok
            else "\n".join(n.ref.ref for n in result.nodes)
        )
        if not result.ok:
            raise typer.Exit(code=1)

    @app.command("versions", help=f"List versions of a {k}")
    @guarded
    def _versions(
        ref: str = typer.Argument(...), project: Path = _PROJECT, user: bool = _USER
    ) -> None:
        with open_registry(project, user) as reg:
            recs = reg.versions(ref, k)
        _table(
            ref,
            ["version", "lifecycle", "channel", "trust"],
            [[r.version, r.lifecycle.value, r.channel, r.trust.value] for r in recs],
        )

    @app.command("activate", help=f"Activate a {k} version")
    @guarded
    def _activate(
        ref: str = typer.Argument(...), project: Path = _PROJECT, user: bool = _USER
    ) -> None:
        from ananke.plexus.registry.activation import activate

        with open_registry(project, user) as reg:
            res = activate(reg, project, ref, kind=k)
        typer.echo(f"Activated {res.uri}@{res.version} -> {res.path}")

    for fn in (_list, _show_cmd, _search, _register, _resolve, _versions, _activate):
        fn.__name__ = f"{k}_{fn.__name__.strip('_')}"


_kind_shortcuts(skill_app, ArtifactKind.SKILL)
_kind_shortcuts(agent_app, ArtifactKind.AGENT)
