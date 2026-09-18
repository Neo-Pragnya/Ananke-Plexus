"""discover_tests — aggregate test definitions from all available adapters."""

from __future__ import annotations

from pathlib import Path

from ananke.plexus.testing.adapters.base import TestAdapter
from ananke.plexus.testing.models.test import TestDefinition


def discover_tests(
    project_root: Path,
    profile: str = "standard",
    adapters: list[TestAdapter] | None = None,
) -> list[TestDefinition]:
    """Discover all test definitions from available adapters.

    Calls ``adapter.discover()`` for each adapter that reports itself as
    available, merges the results, and deduplicates by test ID.

    Parameters
    ----------
    project_root:
        Root of the project to discover tests in.
    profile:
        Quality profile name passed through to each adapter.
    adapters:
        Explicit list of adapters to use.  If ``None``, the default adapter
        set is used.
    """
    if adapters is None:
        from ananke.plexus.testing.adapters import default_adapters

        adapters = default_adapters()

    seen_ids: set[str] = set()
    all_tests: list[TestDefinition] = []

    for adapter in adapters:
        try:
            if not adapter.available():
                continue
            discovered = adapter.discover(project_root, profile)
            for test in discovered:
                if test.id not in seen_ids:
                    seen_ids.add(test.id)
                    all_tests.append(test)
        except Exception:
            # Discovery failures are non-fatal
            continue

    return all_tests
