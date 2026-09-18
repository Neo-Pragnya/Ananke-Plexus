"""TestRunner — coordinates adapter invocations for a QualitySuite."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from ananke.plexus.testing.context import TestContext
from ananke.plexus.testing.models.result import TestResult, TestRun
from ananke.plexus.testing.models.suite import QualitySuite
from ananke.plexus.testing.models.test import TestStatus


class TestRunner:
    """Iterate adapters, collect results, and return a TestRun."""

    def __init__(self, adapters: list[Any], context: TestContext) -> None:
        self._adapters = adapters
        self._context = context

    def run(
        self,
        suite: QualitySuite,
        run_id: str | None = None,
    ) -> TestRun:
        """Execute the quality suite using all available adapters.

        Each adapter is called with the tests from the suite that match its
        capabilities.  Exceptions from any adapter are caught and converted
        into a ``TestResult`` with ``status=UNAVAILABLE``.

        Parameters
        ----------
        suite:
            The quality suite to run.
        run_id:
            Optional run identifier.  A UUID is generated when omitted.
        """
        effective_run_id = run_id or str(uuid.uuid4())
        started = datetime.now(tz=UTC)

        all_results: list[TestResult] = []
        adapter_versions: dict[str, str] = {}

        for adapter in self._adapters:
            adapter_id: str = getattr(adapter, "adapter_id", type(adapter).__name__)
            try:
                if not adapter.available():
                    continue
                version = adapter.version()
                if version:
                    adapter_versions[adapter_id] = version

                caps = adapter.capabilities()
                relevant_tests = (
                    [t for t in suite.tests if t.kind in caps.kinds] if suite.tests else []
                )

                if not relevant_tests and suite.tests:
                    # Adapter has no tests in this suite, skip it
                    continue

                results = adapter.run(
                    relevant_tests,
                    self._context.project_root,
                    self._context.timeout_seconds,
                )
                all_results.extend(results)
            except Exception as exc:
                all_results.append(
                    TestResult(
                        test_id=f"{adapter_id}-run",
                        kind="unit",  # type: ignore[arg-type]
                        engine=adapter_id,
                        status=TestStatus.UNAVAILABLE,
                        started_at=started,
                        message=f"Adapter {adapter_id} raised: {exc!r}",
                    )
                )

        ended = datetime.now(tz=UTC)
        return TestRun(
            run_id=effective_run_id,
            suite_id=suite.id,
            profile=suite.profile,
            started_at=started,
            ended_at=ended,
            results=all_results,
            adapter_versions=adapter_versions,
        )
