# Eval Harness Reference

## Python API

### `run_evaluation()`

```python
from ananke.plexus.evals import run_evaluation, EvalSuite, EvalCase, GatePolicy

report = run_evaluation(
    suite=EvalSuite(id="standard", policy=GatePolicy()),
    case=EvalCase(id="tc-001", input="Summarise the spec"),
    output="The spec defines three requirements: ...",
    project_root=Path("."),
    evaluators=[],           # [] = use default registry
    save_evidence=True,      # writes .ananke/evidence/<run_id>/eval/
)
```

Returns `EvalReport`.

### `evaluate_trace()`

Re-evaluate a recorded trace without re-running the agent:

```python
from ananke.plexus.evals import evaluate_trace

report = evaluate_trace(
    suite=suite,
    trace_path=Path(".ananke/traces/run-001.json"),
    project_root=Path("."),
)
```

### `adapter_doctor()`

```python
from ananke.plexus.evals import adapter_doctor

status = adapter_doctor()
# {"ananke.adapters.mlflow": {"available": True, ...}, ...}
```

### `list_native_evaluators()`

```python
from ananke.plexus.evals import list_native_evaluators

evals = list_native_evaluators()
# [{"id": "exact-match", "dimension": "outcome", "deterministic": True}, ...]
```

## Models

### `AgentTrace`

| Field | Type | Description |
|---|---|---|
| `trace_id` | `str` | Unique trace identifier |
| `run_id` | `str` | ADLC run identifier |
| `runtime` | `str` | Source runtime (`pydantic-ai`, `hermes`, `generic`, …) |
| `spans` | `list[AgentSpan]` | Ordered spans |
| `usage` | `Usage` | Aggregate token/cost/call counts |
| `metadata` | `dict` | Runtime-specific extra data |

### `AgentSpan`

| Field | Type | Description |
|---|---|---|
| `span_id` | `str` | |
| `parent_id` | `str \| None` | Parent span for nesting |
| `kind` | `SpanKind` | See SpanKind table |
| `name` | `str` | Tool name, model, step name |
| `start_time` | `datetime` | |
| `end_time` | `datetime \| None` | |
| `attributes` | `dict` | Span-specific key-value data |
| `status` | `str` | `ok \| error \| timeout` |
| `error_message` | `str \| None` | |

### `EvalScore`

| Field | Type | Description |
|---|---|---|
| `evaluator_id` | `str` | |
| `dimension` | `str` | Quality domain |
| `status` | `EvalStatus` | `pass/warn/review/fail/error/skipped` |
| `score` | `float \| None` | Raw score |
| `normalized_score` | `float \| None` | 0.0–1.0 |
| `reason` | `str` | Human-readable explanation |
| `metadata` | `dict` | Extra evidence |

### `EvalReport`

| Field | Type | Description |
|---|---|---|
| `run_id` | `str` | |
| `suite_id` | `str` | |
| `case_id` | `str \| None` | |
| `scores` | `list[EvalScore]` | |
| `gate_decision` | `EvalGateDecision \| None` | |
| `baseline_comparison` | `BaselineComparison \| None` | |
| `started_at` | `datetime` | |
| `completed_at` | `datetime \| None` | |

## CLI Commands

### `ananke eval run`

```
ananke eval run [OPTIONS]

Options:
  --suite TEXT       Eval suite id [default: standard]
  --case TEXT        Case id
  --trace PATH       Path to recorded trace JSON
  --output TEXT      Agent output for quick evaluation
  --project PATH     Project root [default: .]
  --json             Output JSON
```

Exit codes: 0 = PASS/WARN, 1 = BLOCK, 2 = not found.

### `ananke eval report`

```
ananke eval report --run RUN_ID [--format console|markdown|json|junit] [--project PATH]
```

### `ananke eval compare`

```
ananke eval compare --baseline BASELINE_ID --run RUN_ID [--project PATH]
```

### `ananke eval suite list/validate`

```
ananke eval suite list [--project PATH] [--json]
ananke eval suite validate --path SUITE_FILE
```

### `ananke eval dataset list/validate`

```
ananke eval dataset list [--project PATH] [--json]
ananke eval dataset validate --path DATASET_FILE
```

### `ananke eval baseline create`

```
ananke eval baseline create --run RUN_ID --id BASELINE_ID [--project PATH]
```

### `ananke eval trace show/import`

```
ananke eval trace show --path TRACE_FILE
ananke eval trace import --path TRACE_FILE [--run-id ID] [--project PATH]
```

### `ananke eval adapter list/doctor`

```
ananke eval adapter list [--json]
ananke eval adapter doctor [ADAPTER_ID] [--json]
```

### `ananke eval judge list/test`

```
ananke eval judge list
ananke eval judge test [--provider local|azure|bedrock|anthropic|openai]
```

## Configuration

### `.ananke/evals/config.yaml`

```yaml
suites:
  standard:
    hard_fail_on:
      - safety
      - outcome
    warn_on:
      - efficiency
      - resilience
    pass_threshold: 0.80
    warn_threshold: 0.60

regression:
  max_drop: 0.05           # Allow up to 5% score drop
  allow_regression: false
  bootstrap_samples: 1000

judge:
  allowed_providers:
    - local
    - azure
  max_cost_per_call_usd: 0.50

dataset:
  allowed_classifications:
    - PUBLIC
    - INTERNAL
  deny_egress_to_external: true
```

### `.ananke/evals/suites/<suite-id>.json`

```json
{
  "id": "standard",
  "evaluators": [
    {"id": "exact-match", "weight": 1.0},
    {"id": "task-completion", "weight": 2.0}
  ],
  "policy": {
    "rules": [
      {"dimension": "safety", "min_score": 1.0, "verdict_on_fail": "BLOCK"},
      {"dimension": "outcome", "min_score": 0.8, "verdict_on_fail": "BLOCK"},
      {"dimension": "efficiency", "min_score": 0.6, "verdict_on_fail": "WARN"}
    ]
  }
}
```

### `.ananke/evals/datasets/<dataset-id>.yaml`

```yaml
id: smoke-tests
classification: INTERNAL
cases:
  - id: tc-001
    input: "Summarise the spec"
    expected_output: "The spec defines..."
    tags: [smoke, outcome]
  - id: tc-002
    input: "List all tools used"
    expected_output: ["read_file", "write_file"]
    tags: [smoke, tool-use]
```

## PyPI Extras

```bash
pip install ananke-plexus[eval-enterprise]   # OTel + MLflow + jsonschema
pip install ananke-plexus[eval-mlflow]        # MLflow only
pip install ananke-plexus[eval-deepeval]      # DeepEval metrics
pip install ananke-plexus[eval-ragas]         # Ragas RAG evaluators
pip install ananke-plexus[eval-inspect]       # Inspect AI
pip install ananke-plexus[eval-openevals]     # OpenEvals
pip install ananke-plexus[eval-agentevals]    # AgentEvals trajectory
pip install ananke-plexus[eval-otel]          # OTel trace export only
```

## Evidence Bundle Layout

```
.ananke/evidence/<run-id>/
  eval/
    scores.json            # list[EvalScore] serialized
    policy-decision.json   # EvalGateDecision
    baseline-comparison.json  # BaselineComparison (if baseline run)
    manifest.json          # SHA-256 checksums of all files
    reports/
      report.md            # PR-ready Markdown table
      summary.json         # Machine-readable full report
      junit.xml            # JUnit XML (integrates with GitHub Actions, Jenkins)
```

## Writing a Custom Evaluator

Implement the `Evaluator` protocol:

```python
from ananke.plexus.evals.evaluators.base import Evaluator, make_score
from ananke.plexus.evals.models.case import EvalCase
from ananke.plexus.evals.models.score import EvalScore, EvalStatus
from ananke.plexus.evals.models.trace import AgentTrace
from ananke.plexus.evals.context import EvaluationContext

class MyEvaluator:
    id = "my-evaluator"
    version = "1"
    dimension = "outcome"
    deterministic = True

    def evaluate(
        self,
        *,
        case: EvalCase,
        trace: AgentTrace,
        context: EvaluationContext,
    ) -> list[EvalScore]:
        score = 1.0 if case.input in (trace.spans[0].name if trace.spans else "") else 0.0
        return [make_score(self.id, self.dimension, score, 0.5, reason="custom check")]
```

Register it:

```python
from ananke.plexus.evals.evaluators.registry import get_default_registry

get_default_registry().register(MyEvaluator())
```
