"""Skill / Agent views over the shared registry core (spec §65, §138).

Both views are thin, kind-scoped facades: ``registry.skills.get("core/graph-review", "^2")``
and ``registry.agents.resolve("engineering/coding-agent", "stable")``.
"""

from __future__ import annotations

import builtins
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ananke.plexus.registry.models import ArtifactKind, VersionRecord
from ananke.plexus.registry.resolver import (
    Requirement,
    ResolutionEnvironment,
    ResolutionOptions,
    ResolutionResult,
    Resolver,
)
from ananke.plexus.registry.search import SearchHit, search

if TYPE_CHECKING:
    from ananke.plexus.registry.registry import RegisterResult, Registry


class KindView:
    def __init__(self, registry: Registry, kind: ArtifactKind) -> None:
        self.registry = registry
        self.kind = kind

    def resolve(
        self,
        ref: str,
        version: str | None = None,
        *,
        env: ResolutionEnvironment | None = None,
        options: ResolutionOptions | None = None,
    ) -> ResolutionResult:
        req = Requirement.parse(ref, self.kind, version)
        return Resolver(self.registry, env=env, options=options).resolve(req, kind=self.kind)

    def get(
        self,
        ref: str,
        version: str | None = None,
        *,
        env: ResolutionEnvironment | None = None,
        options: ResolutionOptions | None = None,
    ) -> VersionRecord:
        """Resolve ``ref`` under registry policy and return the selected version."""
        self.registry.locate(ref, self.kind)  # NOT_FOUND for unknown artifacts, before policy talk
        result = self.resolve(ref, version, env=env, options=options)
        result.raise_if_failed()
        assert result.selected is not None  # noqa: S101
        return self.registry.exact_version(result.selected.ref, self.kind)

    def register(self, source: str | Path, **kwargs: Any) -> builtins.list[RegisterResult]:
        from ananke.plexus.registry.learn import learn

        report = learn(self.registry, str(source), kind=self.kind, register=True, **kwargs)
        return [c.result for c in report.candidates if c.result is not None]

    def list(self, namespace: str | None = None) -> builtins.list[VersionRecord]:
        return self.registry.list_versions(self.kind, namespace)

    def versions(self, ref: str) -> builtins.list[VersionRecord]:
        return self.registry.versions(ref, self.kind)

    def search(self, query: str = "", **kwargs: Any) -> builtins.list[SearchHit]:
        return search(self.registry, query, kind=self.kind, **kwargs)
