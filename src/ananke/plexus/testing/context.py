"""TestContext — execution context for a quality test run."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class TestContext:
    project_root: Path
    profile: str = "standard"
    selection_mode: str = "full"  # full|changed|impact|requirement
    kinds_filter: list[str] = field(default_factory=list)
    timeout_seconds: int = 300
    online_mode: bool = False

    @classmethod
    def from_project(cls, root: Path, profile: str = "standard") -> TestContext:
        """Create a TestContext from a project root with sensible defaults."""
        return cls(
            project_root=root.resolve(),
            profile=profile,
        )

    def with_kinds(self, kinds: list[str]) -> TestContext:
        """Return a copy of this context with a kinds filter applied."""
        return TestContext(
            project_root=self.project_root,
            profile=self.profile,
            selection_mode=self.selection_mode,
            kinds_filter=list(kinds),
            timeout_seconds=self.timeout_seconds,
            online_mode=self.online_mode,
        )
