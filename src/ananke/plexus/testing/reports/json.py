"""generate_json_report — JSON output for a TestRun."""

from __future__ import annotations

from ananke.plexus.testing.models.result import TestRun


def generate_json_report(run: TestRun) -> str:
    """Return the TestRun serialized as pretty-printed JSON."""
    return run.model_dump_json(indent=2)
