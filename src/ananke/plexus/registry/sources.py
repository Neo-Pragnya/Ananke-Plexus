"""Registry sources and federation (spec §98, §99).

Source priority is ``project → user → enterprise → public``. Local registries take part in
resolution. Remote registries are **pull-only**: ``ananke registry remote pull`` fetches verified,
policy-checked versions into the local registry (as ``discovered`` — trust is never inherited),
and resolution then runs locally, offline and reproducibly. The public source is disabled unless
policy allows it, and every network call is gated by ``[remote_sources]`` policy.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from ananke.plexus.registry.errors import (
    NotInitializedError,
    PolicyViolationError,
    RegistryError,
)
from ananke.plexus.registry.models import ArtifactKind
from ananke.plexus.registry.policy import RegistryPolicy, SourceConfig, load_policy
from ananke.plexus.registry.registry import Registry
from ananke.plexus.registry.resolver import (
    Requirement,
    ResolutionEnvironment,
    ResolutionOptions,
    ResolutionResult,
    Resolver,
)

PRIORITY = ("project", "user", "enterprise", "public")


class SourceStatus(BaseModel):
    name: str
    type: str
    location: str
    enabled: bool
    available: bool
    note: str = ""


def _remote_note(pol: RegistryPolicy, name: str, cfg: SourceConfig | None) -> str:
    if not cfg or not cfg.url:
        return "enabled but no url configured"
    if not pol.remote_registry_allowed(name):
        return (
            "network access denied by policy ([remote_sources]); pass --allow-network to override"
        )
    return "pull-only: fetch with `ananke registry remote pull`; resolution stays local"


def source_statuses(project_root: Path, policy: RegistryPolicy | None = None) -> list[SourceStatus]:
    project_reg = project_root.resolve() / ".ananke" / "registry"
    pol = policy or (load_policy(project_reg) if project_reg.exists() else RegistryPolicy())
    user_reg = Path.home() / ".ananke" / "registry"
    out: list[SourceStatus] = []
    for name in PRIORITY:
        cfg = pol.sources.get(name)
        if name == "project":
            path = Path(cfg.path).expanduser() if cfg and cfg.path else project_reg
            if not path.is_absolute():
                path = project_root.resolve() / path
            out.append(
                SourceStatus(
                    name=name,
                    type="local",
                    location=str(path),
                    enabled=cfg.enabled if cfg else True,
                    available=(path / "registry.db").exists(),
                )
            )
        elif name == "user":
            path = Path(cfg.path).expanduser() if cfg and cfg.path else user_reg
            out.append(
                SourceStatus(
                    name=name,
                    type="local",
                    location=str(path),
                    enabled=cfg.enabled if cfg else True,
                    available=(path / "registry.db").exists(),
                )
            )
        elif name == "enterprise":
            enabled = bool(cfg and cfg.enabled and cfg.type == "remote")
            out.append(
                SourceStatus(
                    name=name,
                    type="remote",
                    location=(cfg.url if cfg and cfg.url else ""),
                    enabled=enabled,
                    available=False,
                    note=_remote_note(pol, name, cfg) if enabled else "not configured",
                )
            )
        else:
            wanted = bool(cfg and cfg.enabled and cfg.type == "remote")
            out.append(
                SourceStatus(
                    name=name,
                    type="remote",
                    location=(cfg.url if cfg and cfg.url else ""),
                    enabled=wanted,
                    available=False,
                    note=_remote_note(pol, name, cfg)
                    if wanted
                    else (
                        "disabled by policy" if not pol.remote_sources.public else "not configured"
                    ),
                )
            )
    return out


def open_sources(
    project_root: Path, policy: RegistryPolicy | None = None
) -> list[tuple[str, Registry]]:
    """Open every available *local* source in priority order. Raises for enabled remote ones."""
    opened: list[tuple[str, Registry]] = []
    for s in source_statuses(project_root, policy):
        if not s.enabled:
            continue
        if s.type == "remote":
            if s.name == "public" and not (policy and policy.remote_sources.public):
                raise PolicyViolationError("public registry source is disabled by policy")
            continue  # remote registries are pull-only; resolution never touches the network
        if s.available:
            opened.append(
                (
                    s.name,
                    Registry.open(
                        Path(s.location), project_root=project_root if s.name == "project" else None
                    ),
                )
            )
    if not opened:
        raise NotInitializedError("no registry found (run `ananke registry init`)")
    return opened


def federated_resolve(
    project_root: Path,
    ref: str,
    *,
    kind: ArtifactKind | str | None = None,
    env: ResolutionEnvironment | None = None,
    options: ResolutionOptions | None = None,
) -> tuple[str, Registry, ResolutionResult]:
    """Resolve against sources in priority order; the first that succeeds wins.

    The caller owns (and must close) the returned registry.
    """
    sources = open_sources(project_root)
    last: ResolutionResult | None = None
    winner: tuple[str, Registry, ResolutionResult] | None = None
    try:
        for name, reg in sources:
            try:
                res = Resolver(
                    reg, env=env, options=options, source_label=f"{name}-registry"
                ).resolve(Requirement.parse(ref, kind), kind=kind)
            except RegistryError:
                continue
            last = res
            if res.ok:
                winner = (name, reg, res)
                break
        if winner is None:
            if last is not None:
                last.raise_if_failed()
            raise RegistryError(f"{ref!r} was not found in any registry source")
        return winner
    finally:
        for _name, reg in sources:
            if winner is None or reg is not winner[1]:
                reg.close()
