"""Software engineering evaluators: spec adherence, behavior coverage, architecture conformance, graph scope."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ananke.plexus.evals.evaluators.base import make_score, skipped_score
from ananke.plexus.evals.models.case import EvalCase
from ananke.plexus.evals.models.score import EvalScore
from ananke.plexus.evals.models.trace import AgentTrace, SpanKind

if TYPE_CHECKING:
    from ananke.plexus.evals.context import EvaluationContext

_DIM = "architecture"


class SpecAdherenceEvaluator:
    id = "ananke.architecture.spec_adherence"
    version = "1"
    dimension = _DIM
    deterministic = True

    def evaluate(
        self, *, case: EvalCase, trace: AgentTrace, context: EvaluationContext
    ) -> list[EvalScore]:
        acceptance_criteria: list[str] = case.metadata.get("acceptance_criteria", [])
        if not acceptance_criteria:
            return [
                skipped_score(self.id, _DIM, "spec_adherence", "no acceptance_criteria in metadata")
            ]
        output = str(trace.final_output or "").lower()
        covered = [c for c in acceptance_criteria if c.lower() in output]
        ratio = len(covered) / len(acceptance_criteria)
        passed = ratio >= 0.8
        return [
            make_score(
                self.id,
                _DIM,
                "spec_adherence",
                passed,
                normalized=round(ratio, 3),
                reason=f"{len(covered)}/{len(acceptance_criteria)} acceptance criteria met",
            )
        ]


class BehaviorCoverageEvaluator:
    id = "ananke.architecture.behavior_coverage"
    version = "1"
    dimension = _DIM
    deterministic = True

    def evaluate(
        self, *, case: EvalCase, trace: AgentTrace, context: EvaluationContext
    ) -> list[EvalScore]:
        required_behaviors: list[str] = case.metadata.get("required_behaviors", [])
        if not required_behaviors:
            return [
                skipped_score(
                    self.id, _DIM, "behavior_coverage", "no required_behaviors in metadata"
                )
            ]
        verification_spans = [s for s in trace.spans if s.kind == SpanKind.VERIFICATION]
        verified_text = " ".join(str(s.output or s.name) for s in verification_spans).lower()
        covered = [b for b in required_behaviors if b.lower() in verified_text]
        ratio = len(covered) / len(required_behaviors)
        passed = ratio >= 0.8
        return [make_score(self.id, _DIM, "behavior_coverage", passed, normalized=round(ratio, 3))]


class ModelConformanceEvaluator:
    id = "ananke.architecture.model_conformance"
    version = "1"
    dimension = _DIM
    deterministic = True

    def __init__(self, required_fields: list[str]) -> None:
        self._required = required_fields

    def evaluate(
        self, *, case: EvalCase, trace: AgentTrace, context: EvaluationContext
    ) -> list[EvalScore]:
        output = trace.final_output
        if not isinstance(output, dict):
            return [
                make_score(
                    self.id, _DIM, "model_conformance", False, reason="final_output is not a dict"
                )
            ]
        missing = [f for f in self._required if f not in output]
        passed = len(missing) == 0
        return [
            make_score(
                self.id,
                _DIM,
                "model_conformance",
                passed,
                reason=f"missing fields: {missing}" if missing else None,
            )
        ]


class ArchitectureConformanceEvaluator:
    """Checks agent changes against CALM architecture — cross-references context.calm."""

    id = "ananke.architecture.architecture_conformance"
    version = "1"
    dimension = _DIM
    deterministic = True

    def evaluate(
        self, *, case: EvalCase, trace: AgentTrace, context: EvaluationContext
    ) -> list[EvalScore]:
        calm_path = context.calm_path
        if calm_path is None or not calm_path.exists():
            return [
                skipped_score(
                    self.id, _DIM, "architecture_conformance", "no CALM architecture file"
                )
            ]
        try:
            import json

            calm = json.loads(calm_path.read_text(encoding="utf-8"))
            declared_components: list[str] = [n.get("id", "") for n in calm.get("nodes", [])]
        except Exception as exc:
            return [make_score(self.id, _DIM, "architecture_conformance", False, reason=str(exc))]

        output_text = str(trace.final_output or "").lower()
        violations = [c for c in declared_components if c and f"del {c}" in output_text]
        passed = len(violations) == 0
        return [
            make_score(
                self.id,
                _DIM,
                "architecture_conformance",
                passed,
                value=violations,
                reason=f"architecture violations: {violations}" if violations else None,
            )
        ]


class UnexpectedDependencyEvaluator:
    id = "ananke.architecture.unexpected_dependency"
    version = "1"
    dimension = _DIM
    deterministic = True

    def __init__(self, allowed_dependencies: list[str]) -> None:
        self._allowed = set(allowed_dependencies)

    def evaluate(
        self, *, case: EvalCase, trace: AgentTrace, context: EvaluationContext
    ) -> list[EvalScore]:
        for span in trace.spans:
            if (
                span.kind == SpanKind.TOOL
                and "install" in span.name.lower()
                and isinstance(span.input, dict)
            ):
                pkg = span.input.get("package", "")
                if pkg and pkg not in self._allowed:
                    return [
                        make_score(
                            self.id,
                            _DIM,
                            "unexpected_dependency",
                            False,
                            reason=f"unexpected dependency: {pkg}",
                        )
                    ]
        return [make_score(self.id, _DIM, "unexpected_dependency", True)]


class BlastRadiusDisciplineEvaluator:
    id = "ananke.architecture.blast_radius_discipline"
    version = "1"
    dimension = _DIM
    deterministic = True

    def __init__(self, max_files_changed: int = 20) -> None:
        self._max = max_files_changed

    def evaluate(
        self, *, case: EvalCase, trace: AgentTrace, context: EvaluationContext
    ) -> list[EvalScore]:
        changed: list[str] = case.metadata.get("changed_files", [])
        if not changed:
            return [
                skipped_score(
                    self.id, _DIM, "blast_radius_discipline", "no changed_files in metadata"
                )
            ]
        count = len(changed)
        passed = count <= self._max
        return [
            make_score(
                self.id,
                _DIM,
                "blast_radius_discipline",
                passed,
                value=count,
                reason=f"{count} files changed > max {self._max}" if not passed else None,
            )
        ]


class ChangedFileScopeEvaluator:
    id = "ananke.architecture.changed_file_scope"
    version = "1"
    dimension = _DIM
    deterministic = True

    def __init__(self, allowed_paths: list[str]) -> None:
        self._allowed = allowed_paths

    def evaluate(
        self, *, case: EvalCase, trace: AgentTrace, context: EvaluationContext
    ) -> list[EvalScore]:
        changed: list[str] = case.metadata.get("changed_files", [])
        if not changed:
            return [
                skipped_score(self.id, _DIM, "changed_file_scope", "no changed_files in metadata")
            ]
        out_of_scope = [f for f in changed if not any(f.startswith(a) for a in self._allowed)]
        passed = len(out_of_scope) == 0
        return [
            make_score(
                self.id,
                _DIM,
                "changed_file_scope",
                passed,
                value=out_of_scope,
                reason=f"out-of-scope changes: {out_of_scope}" if out_of_scope else None,
            )
        ]


class BreakingContractEvaluator:
    id = "ananke.architecture.breaking_contract"
    version = "1"
    dimension = _DIM
    deterministic = True

    def __init__(self, breaking_indicators: list[str] | None = None) -> None:
        self._indicators = breaking_indicators or [
            "remove field",
            "rename field",
            "change type",
            "remove endpoint",
            "breaking change",
        ]

    def evaluate(
        self, *, case: EvalCase, trace: AgentTrace, context: EvaluationContext
    ) -> list[EvalScore]:
        output = str(trace.final_output or "").lower()
        triggered = [ind for ind in self._indicators if ind in output]
        passed = len(triggered) == 0
        return [
            make_score(
                self.id,
                _DIM,
                "breaking_contract",
                passed,
                value=triggered,
                reason=f"potential breaking changes: {triggered}" if triggered else None,
            )
        ]
