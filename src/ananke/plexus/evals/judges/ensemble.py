"""Judge ensemble — consensus scoring from multiple judges."""

from __future__ import annotations

from ananke.plexus.evals.judges.base import JudgeInputEnvelope, JudgeResult
from ananke.plexus.evals.judges.gateway import EnterpriseJudgeGateway


class JudgeEnsemble:
    """Aggregates multiple judge scores into a consensus result."""

    def __init__(
        self,
        judges: list[EnterpriseJudgeGateway],
        strategy: str = "mean",
    ) -> None:
        self._judges = judges
        self._strategy = strategy

    def score(self, *, envelope: JudgeInputEnvelope, **kwargs: object) -> JudgeResult:
        results = [j.score(envelope=envelope, **kwargs) for j in self._judges]  # type: ignore[arg-type]
        if not results:
            raise ValueError("No judges in ensemble")

        scores = [r.normalized_score for r in results]
        if self._strategy == "mean":
            agg = sum(scores) / len(scores)
        elif self._strategy == "majority":
            passed_count = sum(1 for r in results if r.passed)
            agg = passed_count / len(results)
        elif self._strategy == "min":
            agg = min(scores)
        else:
            agg = sum(scores) / len(scores)

        primary = results[0]
        return JudgeResult(
            judge_id=f"ensemble-{self._strategy}",
            provider="ensemble",
            model=None,
            rubric_id=primary.rubric_id,
            rubric_version=primary.rubric_version,
            score=agg,
            normalized_score=max(0.0, min(1.0, agg)),
            passed=agg >= 0.7,
            reason=f"{self._strategy} of {len(results)} judges: scores={[round(s, 3) for s in scores]}",
            evidence=[r.reason for r in results if r.reason],
            deterministic=False,
            timestamp=primary.timestamp,
        )
