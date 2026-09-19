"""APM CLI baseline."""

import json
from pathlib import Path
from typing import Any

import typer

from ananke.plexus.api import Ananke
from ananke.plexus.apm.audit import audit_manifest
from ananke.plexus.apm.bundle import export_bundle, install_bundle, list_bundle_members
from ananke.plexus.apm.installer import install_local_skill
from ananke.plexus.apm.lockfile import read_lock
from ananke.plexus.apm.manifest import load_manifest
from ananke.plexus.apm.registry import (
    activate_skill,
    deactivate_skill,
    list_active_skills,
    list_installed_skills,
)
from ananke.plexus.apm.resolver import resolve_skill_reference
from ananke.plexus.apm.sandbox import (
    permission_allows_network,
    permission_allows_shell,
    permission_allows_write,
)

app = typer.Typer(help="Ananke Agent Package Manager (APM)")
bundle_app = typer.Typer(help="APM bundle operations")
app.add_typer(bundle_app, name="bundle")


@app.command("list")
def list_installed(
    project: Path = typer.Option(Path("."), "--project", help="Repository root"),
    as_json: bool = typer.Option(False, "--json", help="Render JSON output"),
) -> None:
    installed = list_installed_skills(project)
    active = list_active_skills(project)
    if as_json:
        typer.echo(json.dumps({"installed": installed, "active": active}, indent=2))
        return
    typer.echo("Installed skills:")
    if not installed:
        typer.echo("- none")
    for item in installed:
        status = " (active)" if item in active else ""
        typer.echo(f"- {item}{status}")


def _registry(project: Path, *, create: bool = False) -> Any:
    from ananke.plexus.registry.registry import Registry

    reg = Registry.for_project(project, create=create)
    if create:
        reg.init()
    return reg


def _fail(exc: Exception) -> typer.Exit:
    typer.echo(f"error: {exc}", err=True)
    return typer.Exit(code=1)


@app.command("search")
def search_cmd(
    query: str = typer.Argument(
        "", help="Free text and filters (kind:skill capability:x runtime:y)"
    ),
    project: Path = typer.Option(Path("."), "--project", help="Repository root"),
    kind: str | None = typer.Option(None, "--kind"),
    runtime: str | None = typer.Option(None, "--runtime"),
    capability: list[str] = typer.Option([], "--capability"),
    as_json: bool = typer.Option(False, "--json", help="Render JSON output"),
) -> None:
    """Search the local capability registry."""
    from ananke.plexus.registry.errors import RegistryError

    try:
        with _registry(project) as reg:
            hits = reg.search(query, kind=kind, runtime=runtime, capability=capability or None)
    except RegistryError as exc:
        raise _fail(exc) from None
    if as_json:
        typer.echo(json.dumps([h.model_dump(mode="json") for h in hits], indent=2))
        return
    if not hits:
        typer.echo("No matches.")
    for h in hits:
        typer.echo(
            f"{h.namespace}/{h.name}@{h.version}  [{h.kind.value}, {h.trust.value}]  {h.summary}"
        )


@app.command("install")
def install(
    ref: str | None = typer.Argument(None, help="Registry reference, e.g. core/graph-review@^2"),
    source: Path | None = typer.Option(
        None,
        "--source",
        help="Local directory containing ananke-skill.toml (legacy, no registry)",
    ),
    project: Path = typer.Option(Path("."), "--project", help="Repository root"),
    activate_after: bool = typer.Option(False, "--activate", help="Also activate after installing"),
    allow_prerelease: bool = typer.Option(False, "--allow-prerelease"),
    runtime: str | None = typer.Option(None, "--runtime"),
) -> None:
    if source is not None:
        installed_name, target = install_local_skill(project, source)
        typer.echo(f"Installed: {installed_name}")
        typer.echo(f"Location: {target}")
        return
    if not ref:
        typer.echo("error: give a registry REF (core/name@^1) or --source PATH", err=True)
        raise typer.Exit(code=2)
    from ananke.plexus.registry.activation import install as registry_install
    from ananke.plexus.registry.errors import RegistryError
    from ananke.plexus.registry.resolver import (
        Requirement,
        ResolutionEnvironment,
        ResolutionOptions,
        Resolver,
    )

    try:
        with _registry(project, create=True) as reg:
            requirement = Requirement.parse(ref)
            result = Resolver(
                reg,
                env=ResolutionEnvironment.detect(runtime=runtime),
                options=ResolutionOptions(allow_prerelease=True if allow_prerelease else None),
            ).resolve(requirement)
            result.raise_if_failed()
            for node in result.nodes:
                root_req = requirement.req if node.root else None
                res = registry_install(
                    reg,
                    project,
                    node.ref.ref,
                    requirement=root_req,
                    activate_after=False,
                    root=node.root,
                )
                typer.echo(f"Installed: {node.ref.ref}")
                typer.echo(f"Location: {res.path}")
            if activate_after:
                from ananke.plexus.registry.activation import activate as registry_activate

                for node in result.nodes:
                    registry_activate(reg, project, node.ref.ref, root=node.root)
                    typer.echo(f"Activated: {node.ref.ref}")
    except RegistryError as exc:
        raise _fail(exc) from None


@app.command("activate")
def activate(
    ref: str | None = typer.Argument(None, help="Registry reference (exact version or range)"),
    installed_name: str | None = typer.Option(
        None, "--name", help="Installed skill directory name (legacy)"
    ),
    project: Path = typer.Option(Path("."), "--project", help="Repository root"),
) -> None:
    if installed_name is not None:
        marker = activate_skill(project, installed_name)
        typer.echo(f"Activated: {installed_name}")
        typer.echo(f"Marker: {marker}")
        return
    if not ref:
        typer.echo("error: give a registry REF or --name INSTALLED_NAME", err=True)
        raise typer.Exit(code=2)
    from ananke.plexus.registry.activation import activate as registry_activate
    from ananke.plexus.registry.errors import RegistryError
    from ananke.plexus.registry.resolver import Requirement, Resolver

    try:
        with _registry(project) as reg:
            requirement = Requirement.parse(ref)
            result = Resolver(reg).resolve(requirement)
            result.raise_if_failed()
            assert result.selected is not None  # noqa: S101
            res = registry_activate(reg, project, result.selected.ref, requirement=requirement.req)
    except RegistryError as exc:
        raise _fail(exc) from None
    typer.echo(f"Activated: {res.uri}@{res.version}")
    typer.echo(f"Location: {res.path}")


@app.command("lock")
def lock_cmd(
    project: Path = typer.Option(Path("."), "--project", help="Repository root"),
    update: bool = typer.Option(False, "--update", help="Ignore existing pins"),
    output: Path | None = typer.Option(None, "--output", help="Lockfile path"),
) -> None:
    """Resolve installed/declared requirements and write ananke.lock."""
    from ananke.plexus.registry.errors import RegistryError
    from ananke.plexus.registry.sync import sync

    try:
        with _registry(project, create=True) as reg:
            res = sync(reg, project, lock_path=output, update=update)
    except RegistryError as exc:
        raise _fail(exc) from None
    typer.echo(f"{'Wrote' if res.written else 'Up to date:'} {res.lock_path}")
    for change in res.changes:
        typer.echo(f"  {change}")


@app.command("upgrade")
def upgrade_cmd(
    project: Path = typer.Option(Path("."), "--project", help="Repository root"),
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    """Re-resolve requirements to newer compatible versions and refresh installs."""
    from ananke.plexus.registry.activation import activate as registry_activate
    from ananke.plexus.registry.activation import install as registry_install
    from ananke.plexus.registry.activation import list_installed
    from ananke.plexus.registry.errors import RegistryError
    from ananke.plexus.registry.sync import sync

    try:
        with _registry(project, create=True) as reg:
            before = {
                (a["kind"], a["namespace"], a["name"]): a for a in list_installed(reg, project)
            }
            res = sync(reg, project, update=True, dry_run=dry_run)
            if not res.changes:
                typer.echo("Everything is up to date.")
                return
            for change in res.changes:
                typer.echo(change)
            if dry_run:
                return
            for node in res.resolution.nodes:
                if node.source == "path":
                    continue
                ref = node.ref.uri.removeprefix("ananke://").split("/")
                previous = before.get((ref[0], ref[1], ref[2]))
                if previous is not None and previous["version"] != node.ref.version:
                    registry_install(reg, project, node.ref.ref)
                    if previous["state"] == "activated":
                        registry_activate(reg, project, node.ref.ref)
    except RegistryError as exc:
        raise _fail(exc) from None


@app.command("link")
def link_cmd(
    path: Path = typer.Argument(..., help="Working copy of a skill"),
    project: Path = typer.Option(Path("."), "--project", help="Repository root"),
) -> None:
    """Dev mode: use a working copy without publishing."""
    from ananke.plexus.registry.errors import RegistryError
    from ananke.plexus.registry.sync import link

    try:
        with _registry(project, create=True) as reg:
            res = link(reg, project, path)
    except RegistryError as exc:
        raise _fail(exc) from None
    typer.echo(f"Linked {res.uri}@{res.version} -> {res.path}")


@app.command("unlink")
def unlink_cmd(
    ref: str = typer.Argument(..., help="core/name of a linked skill"),
    project: Path = typer.Option(Path("."), "--project", help="Repository root"),
) -> None:
    from ananke.plexus.registry.errors import RegistryError
    from ananke.plexus.registry.sync import unlink

    try:
        with _registry(project, create=True) as reg:
            done = unlink(reg, project, ref)
    except RegistryError as exc:
        raise _fail(exc) from None
    typer.echo("Unlinked." if done else "Not linked.")


@app.command("publish")
def publish_cmd(
    path: Path = typer.Argument(..., help="Skill directory to publish"),
    project: Path = typer.Option(Path("."), "--project", help="Repository root"),
    version: str | None = typer.Option(None, "--version", help="Explicit version or 'auto'"),
    channel: str | None = typer.Option(None, "--channel"),
    run_tests: bool = typer.Option(False, "--run-tests"),
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    """validate → test → hash → resolve deps → conflict check → policy → register → docs."""
    from ananke.plexus.registry.errors import RegistryError
    from ananke.plexus.registry.sync import publish

    try:
        with _registry(project, create=True) as reg:
            report = publish(
                reg, path, version=version, channel=channel, run_tests=run_tests, dry_run=dry_run
            )
    except RegistryError as exc:
        raise _fail(exc) from None
    for step in report.steps:
        typer.echo(f"{'✓' if step.ok else '✗'} {step.name}: {step.detail}")
    if not dry_run and not report.ok:
        raise typer.Exit(code=1)
    if report.result is not None:
        typer.echo(f"Published {report.result.version_uri}")


@app.command("deactivate")
def deactivate(
    installed_name: str = typer.Option(..., "--name", help="Installed skill directory name"),
    project: Path = typer.Option(Path("."), "--project", help="Repository root"),
) -> None:
    marker = deactivate_skill(project, installed_name)
    typer.echo(f"Deactivated: {installed_name}")
    typer.echo(f"Marker: {marker}")


@app.command("info")
def info(
    ref: str | None = typer.Argument(
        None, help="Registry reference to describe (default: apm.lock)"
    ),
    project: Path = typer.Option(Path("."), "--project", help="Repository root"),
) -> None:
    if ref is None:
        lock = read_lock(project)
        typer.echo(json.dumps(lock, indent=2))
        return
    from ananke.plexus.registry.errors import RegistryError
    from ananke.plexus.registry.present import record_to_dict
    from ananke.plexus.registry.resolver import Requirement, Resolver

    try:
        with _registry(project) as reg:
            result = Resolver(reg).resolve(Requirement.parse(ref))
            result.raise_if_failed()
            assert result.selected is not None  # noqa: S101
            rec = reg.exact_version(result.selected.ref)
            typer.echo(json.dumps(record_to_dict(rec, reg), indent=2, default=str))
    except RegistryError as exc:
        raise _fail(exc) from None


@app.command("verify")
def verify(
    project: Path = typer.Option(Path("."), "--project", help="Repository root"),
) -> None:
    lock = read_lock(project)
    packages = lock.get("packages", [])
    if not isinstance(packages, list):
        raise typer.Exit(code=1)
    typer.echo(f"apm.lock packages: {len(packages)}")


@app.command("resolve")
def resolve(
    reference: str = typer.Option(..., "--ref", help="Reference path or installed name"),
    project: Path = typer.Option(Path("."), "--project", help="Repository root"),
) -> None:
    result = resolve_skill_reference(project, reference)
    if not result.ok:
        typer.echo(f"resolve failed: {result.reason}")
        raise typer.Exit(code=1)
    typer.echo(f"resolved[{result.kind}]: {result.value}")


@app.command("audit")
def audit(
    manifest: Path = typer.Option(..., "--manifest", help="Path to ananke-skill.toml"),
) -> None:
    findings = audit_manifest(manifest)
    for item in findings:
        typer.echo(item)


@app.command("sandbox-check")
def sandbox_check(
    manifest: Path = typer.Option(..., "--manifest", help="Path to ananke-skill.toml"),
    shell_command: str = typer.Option("", "--shell", help="Shell command to validate"),
    network_host: str = typer.Option("", "--network", help="Network host to validate"),
    write_path: str = typer.Option("", "--write", help="Write path to validate"),
) -> None:
    model = load_manifest(manifest)
    if shell_command:
        typer.echo(f"shell={permission_allows_shell(model, shell_command)}")
    if network_host:
        typer.echo(f"network={permission_allows_network(model, network_host)}")
    if write_path:
        typer.echo(f"write={permission_allows_write(model, write_path)}")


@app.command("import-copilot")
def import_copilot(
    source: Path = typer.Option(..., "--source", help="Path containing Copilot SKILL.md files"),
    project: Path = typer.Option(Path("."), "--project", help="Repository root"),
) -> None:
    result = Ananke.open(project).apm_import_copilot(source)
    typer.echo(result.summary)
    for key, value in result.details.items():
        typer.echo(f"{key}: {value}")


@bundle_app.command("list")
def bundle_list(
    bundle: Path = typer.Option(..., "--bundle", help="Path to exported bundle archive"),
) -> None:
    members = list_bundle_members(bundle)
    typer.echo(json.dumps({"members": members}, indent=2))


@bundle_app.command("export")
def bundle_export(
    name: list[str] = typer.Option(
        [], "--name", help="Installed skill directory name", show_default=False
    ),
    output: Path | None = typer.Option(None, "--output", help="Optional output archive path"),
    project: Path = typer.Option(Path("."), "--project", help="Repository root"),
) -> None:
    path = export_bundle(project, name, output)
    typer.echo(f"Bundle: {path}")


@bundle_app.command("install")
def bundle_install(
    bundle: Path = typer.Option(..., "--bundle", help="Path to exported bundle archive"),
    project: Path = typer.Option(Path("."), "--project", help="Repository root"),
) -> None:
    installed = install_bundle(project, bundle)
    typer.echo(json.dumps({"installed": installed}, indent=2))
