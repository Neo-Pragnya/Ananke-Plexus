"""RAG and context evaluators — precision, recall, faithfulness, efficiency, Ananke-specific metrics."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ananke.plexus.evals.evaluators.base import make_score, skipped_score
from ananke.plexus.evals.models.case import EvalCase
from ananke.plexus.evals.models.score import EvalScore
from ananke.plexus.evals.models.trace import AgentTrace, SpanKind

if TYPE_CHECKING:
    from ananke.plexus.evals.context import EvaluationContext

_DIM = "retrieval"


def _retrieval_docs(trace: AgentTrace) -> list[str]:
    docs: list[str] = []
    for s in trace.spans:
        if s.kind == SpanKind.RETRIEVAL and isinstance(s.output, list):
            docs.extend(str(d) for d in s.output)
    return docs


class ContextPrecisionEvaluator:
    id = "ananke.retrieval.context_precision"
    version = "1"
    dimension = _DIM
    deterministic = True

    def evaluate(
        self, *, case: EvalCase, trace: AgentTrace, context: EvaluationContext
    ) -> list[EvalScore]:
        docs = _retrieval_docs(trace)
        if not docs:
            return [skipped_score(self.id, _DIM, "context_precision", "no retrieval spans")]
        relevant_refs: list[str] = case.metadata.get("relevant_docs", [])
        if not relevant_refs:
            return [
                skipped_score(self.id, _DIM, "context_precision", "no relevant_docs in metadata")
            ]
        relevant_retrieved = sum(1 for d in docs if any(r in d for r in relevant_refs))
        precision = relevant_retrieved / len(docs)
        passed = precision >= 0.7
        return [
            make_score(self.id, _DIM, "context_precision", passed, normalized=round(precision, 3))
        ]


class ContextRecallEvaluator:
    id = "ananke.retrieval.context_recall"
    version = "1"
    dimension = _DIM
    deterministic = True

    def evaluate(
        self, *, case: EvalCase, trace: AgentTrace, context: EvaluationContext
    ) -> list[EvalScore]:
        docs = _retrieval_docs(trace)
        relevant_refs: list[str] = case.metadata.get("relevant_docs", [])
        if not relevant_refs:
            return [skipped_score(self.id, _DIM, "context_recall", "no relevant_docs in metadata")]
        found = sum(1 for r in relevant_refs if any(r in d for d in docs))
        recall = found / len(relevant_refs)
        passed = recall >= 0.7
        return [make_score(self.id, _DIM, "context_recall", passed, normalized=round(recall, 3))]


class FaithfulnessEvaluator:
    id = "ananke.retrieval.faithfulness"
    version = "1"
    dimension = _DIM
    deterministic = True

    def evaluate(
        self, *, case: EvalCase, trace: AgentTrace, context: EvaluationContext
    ) -> list[EvalScore]:
        docs = _retrieval_docs(trace)
        if not docs:
            return [skipped_score(self.id, _DIM, "faithfulness", "no retrieval spans")]
        output = str(trace.final_output or "").lower()
        if not output:
            return [skipped_score(self.id, _DIM, "faithfulness", "no final_output")]
        doc_tokens = set(" ".join(docs).lower().split())
        output_tokens = set(output.split())
        grounded = len(output_tokens & doc_tokens) / len(output_tokens) if output_tokens else 0.0
        passed = grounded >= 0.5
        return [make_score(self.id, _DIM, "faithfulness", passed, normalized=round(grounded, 3))]


class AnswerRelevanceEvaluator:
    id = "ananke.retrieval.answer_relevance"
    version = "1"
    dimension = _DIM
    deterministic = True

    def evaluate(
        self, *, case: EvalCase, trace: AgentTrace, context: EvaluationContext
    ) -> list[EvalScore]:
        query = str(case.input).lower()
        output = str(trace.final_output or "").lower()
        if not query or not output:
            return [skipped_score(self.id, _DIM, "answer_relevance", "missing input or output")]
        q_tokens = set(query.split())
        o_tokens = set(output.split())
        overlap = len(q_tokens & o_tokens) / len(q_tokens) if q_tokens else 0.0
        passed = overlap >= 0.3
        return [make_score(self.id, _DIM, "answer_relevance", passed, normalized=round(overlap, 3))]


class ContextEfficiencyEvaluator:
    """Ratio of relevant context tokens to total context tokens supplied."""

    id = "ananke.retrieval.context_efficiency"
    version = "1"
    dimension = _DIM
    deterministic = True

    def __init__(self, minimum: float = 0.5) -> None:
        self._minimum = minimum

    def evaluate(
        self, *, case: EvalCase, trace: AgentTrace, context: EvaluationContext
    ) -> list[EvalScore]:
        total_tokens = trace.usage.prompt_tokens
        if total_tokens is None or total_tokens == 0:
            return [skipped_score(self.id, _DIM, "context_efficiency", "no prompt_tokens in usage")]
        relevant_refs: list[str] = case.metadata.get("relevant_docs", [])
        docs = _retrieval_docs(trace)
        if not docs or not relevant_refs:
            return [
                skipped_score(
                    self.id, _DIM, "context_efficiency", "missing retrieval or relevant_docs"
                )
            ]
        relevant_text = " ".join(d for d in docs if any(r in d for r in relevant_refs))
        relevant_tokens = len(relevant_text.split())
        efficiency = min(1.0, relevant_tokens / total_tokens)
        passed = efficiency >= self._minimum
        return [
            make_score(self.id, _DIM, "context_efficiency", passed, normalized=round(efficiency, 3))
        ]


class BlastRadiusCoverageEvaluator:
    """Ananke-specific: did retrieval include all materially affected dependencies from the code graph?"""

    id = "ananke.retrieval.blast_radius_coverage"
    version = "1"
    dimension = _DIM
    deterministic = True

    def evaluate(
        self, *, case: EvalCase, trace: AgentTrace, context: EvaluationContext
    ) -> list[EvalScore]:
        affected_files: list[str] = case.metadata.get("affected_files", [])
        if not affected_files:
            return [
                skipped_score(
                    self.id, _DIM, "blast_radius_coverage", "no affected_files in case metadata"
                )
            ]
        docs = _retrieval_docs(trace)
        retrieved_text = " ".join(docs)
        covered = [f for f in affected_files if f in retrieved_text]
        ratio = len(covered) / len(affected_files)
        passed = ratio >= 0.8
        return [
            make_score(
                self.id,
                _DIM,
                "blast_radius_coverage",
                passed,
                normalized=round(ratio, 3),
                reason=f"{len(covered)}/{len(affected_files)} affected files in context",
            )
        ]


class SpecContextCoverageEvaluator:
    id = "ananke.retrieval.spec_context_coverage"
    version = "1"
    dimension = _DIM
    deterministic = True

    def evaluate(
        self, *, case: EvalCase, trace: AgentTrace, context: EvaluationContext
    ) -> list[EvalScore]:
        required_specs: list[str] = case.metadata.get("required_specs", [])
        if not required_specs:
            return [
                skipped_score(
                    self.id, _DIM, "spec_context_coverage", "no required_specs in metadata"
                )
            ]
        docs = _retrieval_docs(trace)
        retrieved_text = " ".join(docs)
        covered = [s for s in required_specs if s in retrieved_text]
        ratio = len(covered) / len(required_specs)
        passed = ratio >= 0.9
        return [
            make_score(
                self.id,
                _DIM,
                "spec_context_coverage",
                passed,
                normalized=round(ratio, 3),
                reason=f"{len(covered)}/{len(required_specs)} specs in context",
            )
        ]


class GraphContextPrecisionEvaluator:
    id = "ananke.retrieval.graph_context_precision"
    version = "1"
    dimension = _DIM
    deterministic = True

    def evaluate(
        self, *, case: EvalCase, trace: AgentTrace, context: EvaluationContext
    ) -> list[EvalScore]:
        relevant_symbols: list[str] = case.metadata.get("relevant_symbols", [])
        docs = _retrieval_docs(trace)
        if not docs or not relevant_symbols:
            return [
                skipped_score(
                    self.id,
                    _DIM,
                    "graph_context_precision",
                    "missing retrieval or relevant_symbols",
                )
            ]
        retrieved_text = " ".join(docs)
        relevant_in_context = sum(1 for sym in relevant_symbols if sym in retrieved_text)
        precision = relevant_in_context / len(docs)
        passed = precision >= 0.6
        return [
            make_score(
                self.id, _DIM, "graph_context_precision", passed, normalized=round(precision, 3)
            )
        ]


class ArchitectureContextCoverageEvaluator:
    id = "ananke.retrieval.architecture_context_coverage"
    version = "1"
    dimension = _DIM
    deterministic = True

    def evaluate(
        self, *, case: EvalCase, trace: AgentTrace, context: EvaluationContext
    ) -> list[EvalScore]:
        required_components: list[str] = case.metadata.get("required_arch_components", [])
        if not required_components:
            return [
                skipped_score(
                    self.id, _DIM, "architecture_context_coverage", "no required_arch_components"
                )
            ]
        docs = _retrieval_docs(trace)
        retrieved_text = " ".join(docs)
        covered = [c for c in required_components if c in retrieved_text]
        ratio = len(covered) / len(required_components)
        passed = ratio >= 0.8
        return [
            make_score(
                self.id, _DIM, "architecture_context_coverage", passed, normalized=round(ratio, 3)
            )
        ]
