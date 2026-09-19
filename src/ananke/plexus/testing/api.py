"""Public API for the Ananke Plexus testing module."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ananke.plexus.testing.adapters.base import TestAdapter
from ananke.plexus.testing.models.profile import BUILTIN_PROFILES
from ananke.plexus.testing.models.result import TestRun
from ananke.plexus.testing.models.suite import QualityProfile, QualitySuite


def run_quality_suite(
    *,
    project_root: Path,
    profile: str = "standard",
    kinds: list[str] | None = None,
    selection: str = "full",
    adapters: list[TestAdapter] | None = None,
    run_id: str | None = None,
    save_evidence: bool = True,
) -> TestRun:
    """Run the unified quality test suite.

    Parameters
    ----------
    project_root:
        Root of the project to test.
    profile:
        Quality profile name (fast|standard|strict|verification|release).
    kinds:
        Optional list of TestKind values to restrict the run to.
    selection:
        Selection mode (full|changed|impact|requirement).
    adapters:
        Explicit adapter list.  Uses the default set when ``None``.
    run_id:
        Optional run identifier; a UUID is generated when omitted.
    save_evidence:
        When ``True`` (default), persist the run to the Ananke evidence store.

    Returns
    -------
    TestRun
        The completed run with all results.
    """
    from ananke.plexus.testing.adapters import default_adapters
    from ananke.plexus.testing.adapters.pytest import PytestAdapter
    from ananke.plexus.testing.context import TestContext
    from ananke.plexus.testing.discovery import discover_tests
    from ananke.plexus.testing.policy.thresholds import load_quality_config
    from ananke.plexus.testing.runner import TestRunner
    from ananke.plexus.testing.selection import select_tests

    effective_adapters = adapters if adapters is not None else default_adapters()
    if load_quality_config(project_root).coverage_threshold is not None:
        for adapter in effective_adapters:
            if isinstance(adapter, PytestAdapter):
                adapter.collect_coverage = True
    effective_run_id = run_id or str(uuid.uuid4())

    ctx = TestContext(
        project_root=project_root.resolve(),
        profile=profile,
        selection_mode=selection,
        kinds_filter=list(kinds) if kinds else [],
    )

    # Discover
    all_tests = discover_tests(project_root, profile=profile, adapters=effective_adapters)

    # Select
    selected = select_tests(all_tests, mode=selection, kinds_filter=kinds)

    # Build suite
    try:
        quality_profile = QualityProfile(profile)
    except ValueError:
        quality_profile = QualityProfile.STANDARD

    suite = QualitySuite(
        id=f"quality-{profile}",
        profile=quality_profile,
        tests=selected,
    )

    # Run — if there are no selected tests discovered via discovery, still
    # let adapters do their own internal discovery by passing an empty test
    # list (they will discover from project_root themselves).
    if not suite.tests:
        # Create minimal per-adapter stubs
        from ananke.plexus.testing.models.test import TestKind

        started = datetime.now(tz=UTC)
        all_results = []
        adapter_versions: dict[str, str] = {}
        for adapter in effective_adapters:
            adapter_id: str = getattr(adapter, "adapter_id", type(adapter).__name__)
            try:
                if not adapter.available():
                    continue
                ver = adapter.version()
                if ver:
                    adapter_versions[adapter_id] = ver
                adapter_tests = adapter.discover(project_root, profile)
                if kinds:
                    adapter_tests = [t for t in adapter_tests if t.kind in set(kinds)]
                results = adapter.run(
                    adapter_tests,
                    project_root,
                    ctx.timeout_seconds,
                )
                all_results.extend(results)
            except Exception as exc:
                from ananke.plexus.testing.models.result import TestResult
                from ananke.plexus.testing.models.test import TestStatus

                all_results.append(
                    TestResult(
                        test_id=f"{adapter_id}-run",
                        kind=TestKind.UNIT,
                        engine=adapter_id,
                        status=TestStatus.UNAVAILABLE,
                        started_at=started,
                        message=str(exc),
                    )
                )
        ended = datetime.now(tz=UTC)
        run = TestRun(
            run_id=effective_run_id,
            suite_id=suite.id,
            profile=profile,
            started_at=started,
            ended_at=ended,
            results=all_results,
            adapter_versions=adapter_versions,
        )
    else:
        runner = TestRunner(adapters=effective_adapters, context=ctx)
        run = runner.run(suite, run_id=effective_run_id)

    if save_evidence:
        evidence_root = project_root / ".ananke" / "evidence"
        try:
            from ananke.plexus.testing.evidence import save_test_evidence

            save_test_evidence(run, evidence_root)
        except Exception:
            pass  # Evidence saving is best-effort

    return run


def adapter_doctor() -> dict[str, Any]:
    """Run doctor() on all default adapters and return a health report."""
    from ananke.plexus.testing.adapters import default_adapters

    report: dict[str, Any] = {}
    for adapter in default_adapters():
        aid: str = adapter.adapter_id
        try:
            report[aid] = adapter.doctor()
        except Exception as exc:
            report[aid] = {"available": False, "error": str(exc)}
    return report


def list_profiles() -> dict[str, Any]:
    """Return the built-in quality profiles."""
    return dict(BUILTIN_PROFILES)
