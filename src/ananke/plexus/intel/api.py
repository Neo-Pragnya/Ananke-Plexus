"""Section analyzer. See docs/reference/repo-intelligence.md for the shape it answers in."""

from __future__ import annotations

from typing import Any

from ananke.plexus.intel.context import RepoContext, not_built


def analyze(ctx: RepoContext) -> dict[str, Any]:
    return not_built("API routes")
