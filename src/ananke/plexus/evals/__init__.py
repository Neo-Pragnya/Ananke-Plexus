"""
Ananke Plexus — Enterprise Agent Evaluation Harness

Runtime-independent, enterprise-grade agent evaluation:
- 80+ native deterministic evaluators across 9 quality dimensions
- Trace-only rescoring (no agent re-execution required)
- Versioned datasets with provenance and sensitivity classification
- LLM judge gateway with enterprise provider routing
- Regression engine with bootstrap CI and policy enforcement
- Multi-format reporting: console, Markdown, JSON, JUnit XML
- Lazy third-party adapters: MLflow, DeepEval, Inspect AI, Ragas, OpenEvals, AgentEvals
- ADLC gate integration: EvalScore[] → PASS/WARN/REVIEW/BLOCK
"""

from ananke.plexus.evals.api import (
    adapter_doctor,
    evaluate_trace,
    list_native_evaluators,
    run_evaluation,
)
from ananke.plexus.evals.context import EvaluationContext
from ananke.plexus.evals.models import (
    AgentSpan,
    AgentTrace,
    Baseline,
    EvalCase,
    EvalDataset,
    EvalGateDecision,
    EvalReport,
    EvalScore,
    EvalStatus,
    EvalSuite,
    EvaluatorSpec,
    GatePolicy,
    Rubric,
    SpanKind,
    Usage,
)

__all__ = [
    "AgentSpan",
    "AgentTrace",
    "Baseline",
    "EvalCase",
    "EvalDataset",
    "EvalGateDecision",
    "EvalReport",
    "EvalScore",
    "EvalStatus",
    "EvalSuite",
    "EvaluationContext",
    "EvaluatorSpec",
    "GatePolicy",
    "Rubric",
    "SpanKind",
    "Usage",
    "adapter_doctor",
    "evaluate_trace",
    "list_native_evaluators",
    "run_evaluation",
]
