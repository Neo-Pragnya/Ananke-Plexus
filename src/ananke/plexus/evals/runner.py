"""EvalRunner — orchestrates case execution across evaluator families."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ananke.plexus.evals.context import EvaluationContext
from ananke.plexus.evals.evaluators.base import Evaluator
from ananke.plexus.evals.models.case import EvalCase
from ananke.plexus.evals.models.report import EvalReport
from ananke.plexus.evals.models.score import EvalScore
from ananke.plexus.evals.models.suite import EvalSuite
from ananke.plexus.evals.models.trace import AgentTrace
from ananke.plexus.evals.policy.decisions import apply_gate_policy


class EvalRunner:
    """Runs an EvalSuite against a case + trace, applies gate policy, returns EvalReport."""

    def __init__(
        self,
        evaluators: list[Evaluator],
        context: EvaluationContext,
    ) -> None:
        self._evaluators = evaluators
        self._context = context

    def run(
        self,
        *,
        suite: EvalSuite,
        case: EvalCase,
        trace: AgentTrace,
        run_id: str | None = None,
    ) -> EvalReport:
        started_at = datetime.now(tz=UTC)
        effective_run_id = run_id or trace.run_id

        all_scores: list[EvalScore] = []
        for ev in self._evaluators:
            try:
                scores = ev.evaluate(case=case, trace=trace, context=self._context)
                all_scores.extend(scores)
            except Exception as exc:
                from ananke.plexus.evals.evaluators.base import error_score

                all_scores.append(
                    error_score(
                        getattr(ev, "id", "unknown"),
                        getattr(ev, "dimension", "unknown"),
                        "evaluate",
                        str(exc),
                    )
                )

        gate = apply_gate_policy(suite.id, effective_run_id, all_scores, suite.policy)
        completed_at = datetime.now(tz=UTC)

        return EvalReport(
            run_id=effective_run_id,
            suite_id=suite.id,
            case_id=case.id,
            scores=all_scores,
            gate_decision=gate,
            started_at=started_at,
            completed_at=completed_at,
        )


def run_suite_on_trace(
    *,
    suite: EvalSuite,
    case: EvalCase,
    trace: AgentTrace,
    evaluators: list[Evaluator],
    project_root: Path,
    run_id: str | None = None,
) -> EvalReport:
    context = EvaluationContext.from_project(project_root)
    runner = EvalRunner(evaluators=evaluators, context=context)
    return runner.run(suite=suite, case=case, trace=trace, run_id=run_id)


def save_eval_evidence(report: EvalReport, evidence_root: Path) -> Path:
    """Write eval evidence bundle under .ananke/evidence/<run_id>/eval/."""
    eval_dir = evidence_root / report.run_id / "eval"
    eval_dir.mkdir(parents=True, exist_ok=True)
    reports_dir = eval_dir / "reports"
    reports_dir.mkdir(exist_ok=True)

    (eval_dir / "scores.json").write_text(
        json.dumps([s.model_dump(mode="json") for s in report.scores], indent=2),
        encoding="utf-8",
    )
    if report.gate_decision:
        (eval_dir / "policy-decision.json").write_text(
            report.gate_decision.model_dump_json(indent=2),
            encoding="utf-8",
        )
    if report.baseline_comparison:
        (eval_dir / "baseline-comparison.json").write_text(
            report.baseline_comparison.model_dump_json(indent=2),
            encoding="utf-8",
        )

    from ananke.plexus.evals.reports.json import generate_json_report
    from ananke.plexus.evals.reports.junit import generate_junit_xml
    from ananke.plexus.evals.reports.markdown import generate_markdown_report

    (reports_dir / "report.md").write_text(generate_markdown_report(report), encoding="utf-8")
    (reports_dir / "summary.json").write_text(generate_json_report(report), encoding="utf-8")
    (reports_dir / "junit.xml").write_text(generate_junit_xml(report), encoding="utf-8")

    manifest: dict[str, Any] = {
        "run_id": report.run_id,
        "suite_id": report.suite_id,
        "case_id": report.case_id,
        "files": {
            "scores.json": _file_hash(eval_dir / "scores.json"),
        },
    }
    (eval_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return eval_dir


def _file_hash(path: Path) -> str:
    if not path.exists():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()
