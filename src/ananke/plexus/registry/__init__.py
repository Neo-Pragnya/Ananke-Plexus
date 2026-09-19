"""Ananke Plexus Skill & Agent Registry.

A local-first, versioned registry of reusable agent capabilities: skills, agents, tools,
evaluators, prompts, policies and bundles. See ``docs/concepts/registry.md``.

    from ananke.plexus import Ananke

    registry = Ananke.open(".").registry
    registry.skills.register("./skills/graph-review")
    record = registry.skills.get("core/graph-review", "^2")
"""

from ananke.plexus.registry.errors import RegistryError
from ananke.plexus.registry.models import (
    ArtifactKind,
    ArtifactManifest,
    LifecycleStatus,
    ResolutionMode,
    TrustStatus,
    VersionRecord,
)
from ananke.plexus.registry.registry import RegisterResult, Registry
from ananke.plexus.registry.resolver import (
    Requirement,
    ResolutionEnvironment,
    ResolutionOptions,
    ResolutionResult,
    Resolver,
)
from ananke.plexus.registry.semver import Version, VersionReq

__all__ = [
    "ArtifactKind",
    "ArtifactManifest",
    "LifecycleStatus",
    "RegisterResult",
    "Registry",
    "RegistryError",
    "Requirement",
    "ResolutionEnvironment",
    "ResolutionMode",
    "ResolutionOptions",
    "ResolutionResult",
    "Resolver",
    "TrustStatus",
    "Version",
    "VersionRecord",
    "VersionReq",
]
