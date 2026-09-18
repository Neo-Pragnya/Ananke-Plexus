"""Public high-level API for the Ananke evaluation harness."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from ananke.plexus.evals.context import EvaluationContext
from ananke.plexus.evals.models.case import EvalCase
from ananke.plexus.evals.models.report import EvalReport
from ananke.plexus.evals.models.suite import EvalSuite
from ananke.plexus.evals.models.trace import AgentTrace
from ananke.plexus.evals.runner import EvalRunner, save_eval_evidence
from ananke.plexus.evals.traces.normalize import build_trace_from_run_id


class _EvalAdapter(Protocol):
    adapter_id: str

    def doctor(self) -> dict[str, Any]: ...


def run_evaluation(
    *,
    suite: EvalSuite,
    case: EvalCase,
    trace: AgentTrace | None = None,
    output: Any = None,
    run_id: str | None = None,
    project_root: Path,
    evaluators: list[Any] | None = None,
    save_evidence: bool = True,
) -> EvalReport:
    """
    High-level eval entry point.

    Usage:
        report = run_evaluation(
            suite=my_suite,
            case=my_case,
            output="agent output text",
            project_root=Path("."),
        )
    """
    if trace is None:
        eff_run_id = run_id or "eval-run"
        trace = build_trace_from_run_id(eff_run_id, output=output)

    context = EvaluationContext.from_project(project_root)
    effective_evaluators = evaluators or []
    runner = EvalRunner(evaluators=effective_evaluators, context=context)
    report = runner.run(suite=suite, case=case, trace=trace, run_id=run_id)

    if save_evidence:
        evidence_root = project_root / ".ananke" / "evidence"
        save_eval_evidence(report, evidence_root)

    return report


def evaluate_trace(
    *,
    suite: EvalSuite,
    trace_path: Path,
    case: EvalCase | None = None,
    project_root: Path,
    evaluators: list[Any] | None = None,
    runtime: str = "generic",
) -> EvalReport:
    """Evaluate an existing recorded trace without re-running the agent."""
    from ananke.plexus.evals.traces.importers import load_trace_from_file

    trace = load_trace_from_file(trace_path, runtime=runtime)
    effective_case = case or EvalCase(id="trace-replay", input="(from recorded trace)")
    return run_evaluation(
        suite=suite,
        case=effective_case,
        trace=trace,
        project_root=project_root,
        evaluators=evaluators,
    )


def adapter_doctor() -> dict[str, Any]:
    """Run doctor check on all available adapters."""
    from ananke.plexus.evals.adapters.agentevals.adapter import AgentEvalsAdapter
    from ananke.plexus.evals.adapters.deepeval.adapter import DeepEvalAdapter
    from ananke.plexus.evals.adapters.inspect_ai.adapter import InspectAIAdapter
    from ananke.plexus.evals.adapters.mlflow.adapter import MLflowAdapter
    from ananke.plexus.evals.adapters.openevals.adapter import OpenEvalsAdapter
    from ananke.plexus.evals.adapters.ragas.adapter import RagasAdapter

    adapters: list[_EvalAdapter] = [
        MLflowAdapter(),
        DeepEvalAdapter(),
        InspectAIAdapter(),
        RagasAdapter(),
        OpenEvalsAdapter(),
        AgentEvalsAdapter(),
    ]
    return {a.adapter_id: a.doctor() for a in adapters}


def list_native_evaluators() -> list[dict[str, Any]]:
    """Return metadata for all built-in native evaluators."""
    from ananke.plexus.evals.evaluators.registry import get_default_registry

    return get_default_registry().list_all()
