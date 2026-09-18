"""select_tests — filter and select tests for a run."""

from __future__ import annotations

from ananke.plexus.testing.models.test import TestDefinition


def select_tests(
    tests: list[TestDefinition],
    mode: str = "full",
    kinds_filter: list[str] | None = None,
) -> list[TestDefinition]:
    """Select a subset of tests to run.

    Parameters
    ----------
    tests:
        Full list of discovered tests.
    mode:
        Selection mode.

        * ``full`` — run all tests (after optional kind filter)
        * ``changed`` — stub: run all (requires VCS integration)
        * ``impact`` — stub: run all (requires graph integration)
        * ``requirement`` — stub: run all (requires spec integration)
    kinds_filter:
        Optional list of TestKind values to restrict the run to.  When empty
        or ``None``, no kind filtering is applied.
    """
    selected = list(tests)

    # Apply kinds filter first regardless of mode
    if kinds_filter:
        kinds_set = set(kinds_filter)
        selected = [t for t in selected if t.kind in kinds_set]

    if mode == "full":
        return selected

    if mode == "changed":
        # Stub: without VCS integration return all selected tests
        return selected

    if mode == "impact":
        # Stub: without graph integration return all selected tests
        return selected

    if mode == "requirement":
        # Stub: without spec integration return all selected tests
        return selected

    # Unknown mode — return all
    return selected
