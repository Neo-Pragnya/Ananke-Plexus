# Test Harness Reference

## CLI

```text
ananke test run       [--profile fast|standard|strict|verification|release] [--kind KIND]…
                      [--select full|changed|impact|requirement] [--project PATH] [--json]
ananke test discover  [--profile P] [--project PATH] [--json]
ananke test list      [--project PATH] [--json]
ananke test report    --run RUN_ID [--format console|markdown|json|junit|sarif] [--project PATH]

ananke test profile list [--json]
ananke test profile show PROFILE

ananke test adapter list   [--json]
ananke test adapter doctor [ADAPTER_ID] [--json]

ananke test mutation run [--target PATH] [--project PATH]
ananke test fuzz run     [--target PATH] [--project PATH]
ananke test formal run   [--target PATH] [--project PATH]
```

## Python API

```python
from pathlib import Path
from ananke.plexus.testing.api import run_quality_suite

run = run_quality_suite(
    project_root=Path("."),
    profile="standard",          # fast | standard | strict | verification | release
    kinds=None,                  # restrict to specific TestKind values
    selection="full",            # full | changed | impact | requirement
    save_evidence=True,          # writes .ananke/evidence/<run>/quality/
)
for result in run.results:
    print(result.engine, result.kind, result.status)
```

`TestRun` holds `run_id`, `suite_id`, `profile`, timing, `adapter_versions`, and `results: list[TestResult]` (`test_id`, `kind`, `engine`, `status` ∈ `pass|fail|error|skipped|warn|unavailable`, duration, metrics, evidence refs, artifacts, seed, message).

```python
from ananke.plexus.testing.policy.gates import apply_quality_gate
from ananke.plexus.testing.policy.thresholds import load_quality_config

decision = apply_quality_gate(run, load_quality_config(Path(".")))   # reads .ananke/quality.yaml
decision.verdict      # pass | warn | block (review is reserved for policy-driven human gates)
decision.reasons      # e.g. ['coverage 71.5% is below the 80% threshold']
decision.blocks       # True when the pipeline must stop
```

`ananke test run` applies this gate itself: exit `1` when it blocks or any result is `fail`/`error`, exit `2` for an invalid `.ananke/quality.yaml` (`QualityConfigError`). Thresholds (`coverage_threshold`, `mutation_score_threshold`, `max_duration_seconds`) are enforced; coverage is collected by the pytest adapter only when a coverage threshold is configured (`PytestAdapter(collect_coverage=True)`, needs `pytest-cov`), and mutation score is read from mutmut output (`parse_mutation_score`). Result metrics used by the gate: `coverage_percent`, `mutation_score`.

## Writing an adapter

```python
from pathlib import Path
from ananke.plexus.testing.adapters.base import AdapterCapabilities, TestAdapter
from ananke.plexus.testing.models.result import TestResult
from ananke.plexus.testing.models.test import TestDefinition

class MyAdapter:
    adapter_id = "my-engine"

    def available(self) -> bool: ...
    def version(self) -> str | None: ...
    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(kinds={"unit"}, supports_junit=True, languages=["python"])
    def discover(self, project_root: Path, profile: str) -> list[TestDefinition]: ...
    def run(self, tests: list[TestDefinition], project_root: Path, timeout: int | None) -> list[TestResult]: ...
    def doctor(self) -> dict[str, object]: ...
```

Pass it via `run_quality_suite(..., adapters=[MyAdapter(), *default_adapters()])`.

## Files

| Path | Purpose |
|---|---|
| `.ananke/quality.yaml` | Quality-gate thresholds |
| `.ananke/evidence/<run-id>/quality/results.json` | Full run |
| `.ananke/evidence/<run-id>/quality/manifest.json` | SHA-256 checksums |
| `.ananke/evidence/<run-id>/quality/reports/summary.md`, `junit.xml` | Human / CI reports |

## Extras

`test-python`, `test-bdd`, `test-property`, `test-api`, `test-snapshot`, `test-mutation`, `test-contract`, `test-automation`, and `test-all` (python + bdd + property + api + snapshot).
