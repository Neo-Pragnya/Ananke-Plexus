"""Judge calibration — agreement metrics, bias detection."""

from __future__ import annotations

from ananke.plexus.evals.evaluators.meta.calibration import (
    cohens_kappa,
    detect_positional_bias,
    judge_human_agreement,
    score_variance,
)


class JudgeCalibrator:
    """Tracks and reports judge quality metrics."""

    def __init__(self, judge_id: str) -> None:
        self.judge_id = judge_id
        self._judge_scores: list[float] = []
        self._human_scores: list[float] = []

    def record(self, judge_score: float, human_score: float) -> None:
        self._judge_scores.append(judge_score)
        self._human_scores.append(human_score)

    def agreement_report(self) -> dict[str, object]:
        agreement = judge_human_agreement(self._judge_scores, self._human_scores)
        kappa = cohens_kappa(
            [round(s * 4) for s in self._judge_scores],
            [round(s * 4) for s in self._human_scores],
        )
        variance = score_variance(self._judge_scores)
        return {
            "judge_id": self.judge_id,
            "n": len(self._judge_scores),
            "agreement": agreement,
            "cohens_kappa": round(kappa, 4),
            "judge_score_variance": round(variance, 4),
        }

    def positional_bias_report(
        self,
        scores_pos_a: list[float],
        scores_pos_b: list[float],
    ) -> dict[str, object]:
        result = detect_positional_bias(scores_pos_a, scores_pos_b)
        result["judge_id"] = self.judge_id
        return result
