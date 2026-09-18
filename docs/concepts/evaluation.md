# Enterprise Agent Evaluation Harness

The Ananke Plexus evaluation harness is the **quality-intelligence plane** of the ADLC. It provides enterprise-grade, runtime-neutral agent quality assurance — without making any single evaluation library a hard dependency.

## Design Philosophy

### Deterministic-First
Every native evaluator is deterministic: given identical inputs, it produces identical scores. Non-deterministic LLM judges are isolated behind the `JudgeGateway` and used only when no deterministic signal exists.

### Trace-Only Rescoring
Evaluations operate on `AgentTrace` — a canonical span model that normalises traces from Pydantic AI, Microsoft Agent Framework, Hermes, or any OTel-compliant system. You can **re-evaluate historical traces** without re-running the agent.

### Enterprise Dependency Policy
The core `ananke-plexus` package has zero evaluation-library dependencies. Every third-party adapter (`mlflow`, `deepeval`, `ragas`, etc.) is a **lazy import** activated only when the appropriate `[eval-*]` extra is installed:

| Extra | Activates |
|---|---|
| `eval-otel` | OpenTelemetry trace export |
| `eval-mlflow` | MLflow experiment tracking (preferred enterprise backend) |
| `eval-deepeval` | DeepEval metrics |
| `eval-inspect` | Inspect AI |
| `eval-ragas` | Ragas RAG evaluators |
| `eval-openevals` | OpenEvals |
| `eval-agentevals` | AgentEvals trajectory matching |
| `eval-enterprise` | OTel + MLflow + jsonschema bundle |

## Canonical Trace Model

```python
from ananke.plexus.evals import AgentTrace, AgentSpan, SpanKind, Usage

trace = AgentTrace(
    trace_id="t-001",
    run_id="run-001",
    runtime="pydantic-ai",
    spans=[
        AgentSpan(
            span_id="s-001",
            kind=SpanKind.TOOL,
            name="read_file",
            start_time=...,
            end_time=...,
            attributes={"file": "spec.md"},
        )
    ],
    usage=Usage(prompt_tokens=1200, completion_tokens=300, total_tokens=1500),
)
```

`SpanKind` values: `agent | model | planning | tool | retrieval | subagent | approval | verification | policy | filesystem | shell`

## Evaluation Domains

Ananke ships 80+ native evaluators across 9 quality dimensions:

| Domain | Example Evaluators |
|---|---|
| **Outcome** | ExactMatch, Regex, JsonEquality, SchemaConformance, TaskCompletion |
| **Trajectory** | StrictTrajectory, LoopDetection, ForbiddenStep, StepEfficiency |
| **Tool Use** | ToolSelection, ToolAllowlist, ToolDenylist, IdempotencyCheck |
| **Planning** | PlanCoverage, PlanDependency, PlanFeasibility, PlanAdherence |
| **Retrieval/RAG** | ContextPrecision, ContextRecall, Faithfulness, AnswerRelevance |
| **Safety/Governance** | PermissionBoundary, SecretLeakage, ApprovalGate, ShellPolicy |
| **Architecture** | SpecAdherence, BehaviorCoverage, ArchitectureConformance |
| **Efficiency** | TokenBudget, CostBudget, Latency, RetryCount, CacheEfficiency |
| **Resilience** | FailureRecognition, RecoveryPath, CheckpointUsage, Rollback |

Plus multi-agent (`DelegationAccuracy`, `HandoffCompleteness`) and meta-evaluation (`cohens_kappa`, `judge_human_agreement`, `detect_positional_bias`).

## Evaluation Status

Each `EvalScore` carries an `EvalStatus`:

| Status | Meaning |
|---|---|
| `pass` | Meets threshold |
| `warn` | Marginal — review recommended |
| `review` | Human judgement required |
| `fail` | Below threshold |
| `error` | Evaluator threw an exception |
| `skipped` | Evaluator requires optional package not installed |

## Gate Policy

After evaluation, the policy engine converts `EvalScore[]` into an `EvalGateDecision`:

| Verdict | Meaning |
|---|---|
| `PASS` | All hard requirements met |
| `WARN` | Soft thresholds missed; proceed with caution |
| `REVIEW` | Human approval required before merge |
| `BLOCK` | Hard failure; pipeline blocked |

Configure thresholds in `.ananke/evals/config.yaml`:

```yaml
suites:
  standard:
    hard_fail_on: [safety, outcome]
    warn_on: [efficiency, resilience]
    pass_threshold: 0.8
    warn_threshold: 0.6
```

## LLM Judge Governance

The `EnterpriseJudgeGateway` ensures:
- Only approved providers are used (`azure | bedrock | anthropic | openai | local | custom`)
- Secret patterns are redacted before any text is sent to a provider
- Trusted rubrics are separated from untrusted agent output via `JudgeInputEnvelope.to_prompt()`
- Credentials are resolved from the Ananke secrets resolver — never hardcoded

## Baseline Regression

Compare a candidate run against an approved baseline:

```bash
ananke eval baseline create --run my-run-id --id v1.0-baseline
ananke eval compare --baseline v1.0-baseline --run my-new-run
```

Policy enforcement uses bootstrap confidence intervals to distinguish statistical noise from real regressions.

## Evidence Bundle

Every evaluation run writes a tamper-evident bundle:

```
.ananke/evidence/<run-id>/eval/
  scores.json          # EvalScore[] with normalized values
  policy-decision.json # EvalGateDecision
  manifest.json        # SHA-256 checksums
  reports/
    report.md          # PR-ready Markdown table
    summary.json       # Machine-readable JSON
    junit.xml          # JUnit XML for CI integration
```

## ADLC Integration

The evaluation harness is wired into the ADLC at the **Evaluation Gate**:

```
Spec → Plan → Implement → [Eval Gate] → Evidence → Deploy
```

When `gate_decision.blocks` is true, `ananke eval run` exits with code 1, blocking the pipeline. CI pipelines consume `junit.xml` for test result reporting.
