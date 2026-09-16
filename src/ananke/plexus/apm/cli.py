"""APM CLI baseline."""

import json
from pathlib import Path

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


@app.command("install")
def install(
    source: Path = typer.Option(
        ...,
        "--source",
        help="Local directory containing ananke-skill.toml",
    ),
    project: Path = typer.Option(Path("."), "--project", help="Repository root"),
) -> None:
    installed_name, target = install_local_skill(project, source)
    typer.echo(f"Installed: {installed_name}")
    typer.echo(f"Location: {target}")


@app.command("activate")
def activate(
    installed_name: str = typer.Option(..., "--name", help="Installed skill directory name"),
    project: Path = typer.Option(Path("."), "--project", help="Repository root"),
) -> None:
    marker = activate_skill(project, installed_name)
    typer.echo(f"Activated: {installed_name}")
    typer.echo(f"Marker: {marker}")


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
    project: Path = typer.Option(Path("."), "--project", help="Repository root"),
) -> None:
    lock = read_lock(project)
    typer.echo(json.dumps(lock, indent=2))


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
