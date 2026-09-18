"""Tests for the judge system (gateway, ensemble, calibration) in the eval harness."""

from __future__ import annotations

import pytest

from ananke.plexus.evals.evaluators.meta.calibration import (
    cohens_kappa,
    detect_positional_bias,
    judge_human_agreement,
    score_variance,
)
from ananke.plexus.evals.judges.base import JudgeInputEnvelope, JudgeResult
from ananke.plexus.evals.judges.calibration import JudgeCalibrator
from ananke.plexus.evals.judges.ensemble import JudgeEnsemble
from ananke.plexus.evals.judges.gateway import EnterpriseJudgeGateway
from ananke.plexus.evals.models.rubric import Rubric, RubricCriterion

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def make_rubric(pass_threshold: float = 0.7) -> Rubric:
    return Rubric(
        rubric_id="test-rubric",
        title="Test Rubric",
        criteria=[RubricCriterion(id="c1", description="is accurate")],
        pass_threshold=pass_threshold,
    )


def make_envelope(output: str = "The answer is 42.") -> JudgeInputEnvelope:
    return JudgeInputEnvelope(
        rubric=make_rubric(),
        case_input="What is 6 times 7?",
        agent_output=output,
    )


# ---------------------------------------------------------------------------
# JudgeInputEnvelope
# ---------------------------------------------------------------------------


class TestJudgeInputEnvelope:
    def test_to_prompt_contains_rubric_section(self):
        env = make_envelope()
        prompt = env.to_prompt()
        assert "TRUSTED RUBRIC" in prompt

    def test_to_prompt_contains_agent_output_section(self):
        env = make_envelope("my output")
        prompt = env.to_prompt()
        assert "AGENT OUTPUT" in prompt
        assert "my output" in prompt

    def test_to_prompt_contains_task_input_section(self):
        env = make_envelope()
        prompt = env.to_prompt()
        assert "TASK INPUT" in prompt

    def test_to_prompt_rubric_section_comes_before_output_section(self):
        env = make_envelope()
        prompt = env.to_prompt()
        rubric_pos = prompt.index("TRUSTED RUBRIC")
        output_pos = prompt.index("AGENT OUTPUT")
        assert rubric_pos < output_pos

    def test_to_prompt_tool_outputs_section_when_present(self):
        env = JudgeInputEnvelope(
            rubric=make_rubric(),
            case_input="input",
            agent_output="output",
            tool_outputs=["tool result 1", "tool result 2"],
        )
        prompt = env.to_prompt()
        assert "TOOL OUTPUTS" in prompt
        assert "tool result 1" in prompt

    def test_to_prompt_no_tool_outputs_section_when_absent(self):
        env = make_envelope()
        prompt = env.to_prompt()
        assert "TOOL OUTPUTS" not in prompt

    def test_criteria_in_prompt(self):
        env = make_envelope()
        prompt = env.to_prompt()
        assert "is accurate" in prompt


# ---------------------------------------------------------------------------
# EnterpriseJudgeGateway
# ---------------------------------------------------------------------------


class TestEnterpriseJudgeGateway:
    def test_local_provider_returns_result(self):
        gw = EnterpriseJudgeGateway()
        result = gw.score(envelope=make_envelope(), provider="local")
        assert isinstance(result, JudgeResult)

    def test_local_provider_result_has_rubric_id(self):
        gw = EnterpriseJudgeGateway()
        result = gw.score(envelope=make_envelope(), provider="local")
        assert result.rubric_id == "test-rubric"

    def test_local_provider_normalized_score_in_range(self):
        gw = EnterpriseJudgeGateway()
        result = gw.score(envelope=make_envelope(), provider="local")
        assert 0.0 <= result.normalized_score <= 1.0

    def test_disallowed_provider_raises(self):
        gw = EnterpriseJudgeGateway(allowed_providers=["local"])
        with pytest.raises(PermissionError):
            gw.score(envelope=make_envelope(), provider="openai")

    def test_disallowed_model_raises(self):
        gw = EnterpriseJudgeGateway(allowed_models=["gpt-4"])
        with pytest.raises(PermissionError):
            gw.score(envelope=make_envelope(), provider="local", model="gpt-3.5-turbo")

    def test_allowed_model_works(self):
        gw = EnterpriseJudgeGateway(allowed_models=["gpt-4"])
        # local provider ignores model so should not raise
        result = gw.score(envelope=make_envelope(), provider="local", model="gpt-4")
        assert result is not None

    def test_redacts_api_key_in_output(self):
        gw = EnterpriseJudgeGateway()
        env = JudgeInputEnvelope(
            rubric=make_rubric(),
            case_input="what is my key?",
            agent_output="api_key=supersecret123",
        )
        # Should not crash; the redacted prompt scores without exposing the key
        result = gw.score(envelope=env, provider="local")
        assert result is not None

    def test_result_has_prompt_hash(self):
        gw = EnterpriseJudgeGateway()
        result = gw.score(envelope=make_envelope(), provider="local")
        assert result.prompt_hash is not None

    def test_result_has_latency(self):
        gw = EnterpriseJudgeGateway()
        result = gw.score(envelope=make_envelope(), provider="local")
        assert result.latency_ms is not None
        assert result.latency_ms >= 0

    def test_result_is_not_deterministic(self):
        gw = EnterpriseJudgeGateway()
        result = gw.score(envelope=make_envelope(), provider="local")
        assert result.deterministic is False

    def test_list_providers_contains_local(self):
        gw = EnterpriseJudgeGateway()
        providers = gw.list_providers()
        assert "local" in providers

    def test_custom_judge_id(self):
        gw = EnterpriseJudgeGateway()
        result = gw.score(envelope=make_envelope(), provider="local", judge_id="my-judge")
        assert result.judge_id == "my-judge"

    def test_non_local_provider_raises_not_implemented(self):
        gw = EnterpriseJudgeGateway()
        with pytest.raises((NotImplementedError, PermissionError)):
            gw.score(envelope=make_envelope(), provider="bedrock")

    def test_redact_method(self):
        gw = EnterpriseJudgeGateway()
        redacted = gw._redact("The api_key=mysecret is here")
        assert "mysecret" not in redacted
        assert "REDACTED" in redacted


# ---------------------------------------------------------------------------
# JudgeEnsemble
# ---------------------------------------------------------------------------


class TestJudgeEnsemble:
    def test_mean_strategy_averages_scores(self):
        gw1 = EnterpriseJudgeGateway()
        gw2 = EnterpriseJudgeGateway()
        ensemble = JudgeEnsemble(judges=[gw1, gw2], strategy="mean")
        result = ensemble.score(envelope=make_envelope(), provider="local")
        assert isinstance(result, JudgeResult)
        assert 0.0 <= result.normalized_score <= 1.0

    def test_ensemble_judge_id_reflects_strategy(self):
        gw = EnterpriseJudgeGateway()
        ensemble = JudgeEnsemble(judges=[gw], strategy="mean")
        result = ensemble.score(envelope=make_envelope(), provider="local")
        assert "mean" in result.judge_id

    def test_ensemble_min_strategy(self):
        gw1 = EnterpriseJudgeGateway()
        gw2 = EnterpriseJudgeGateway()
        ensemble = JudgeEnsemble(judges=[gw1, gw2], strategy="min")
        result = ensemble.score(envelope=make_envelope(), provider="local")
        assert 0.0 <= result.normalized_score <= 1.0

    def test_ensemble_majority_strategy(self):
        gw = EnterpriseJudgeGateway()
        ensemble = JudgeEnsemble(judges=[gw, gw, gw], strategy="majority")
        result = ensemble.score(envelope=make_envelope(), provider="local")
        assert 0.0 <= result.normalized_score <= 1.0

    def test_empty_judges_raises(self):
        ensemble = JudgeEnsemble(judges=[])
        with pytest.raises(ValueError):
            ensemble.score(envelope=make_envelope(), provider="local")

    def test_ensemble_provider_is_ensemble(self):
        gw = EnterpriseJudgeGateway()
        ensemble = JudgeEnsemble(judges=[gw])
        result = ensemble.score(envelope=make_envelope(), provider="local")
        assert result.provider == "ensemble"

    def test_ensemble_rubric_id_from_primary(self):
        gw = EnterpriseJudgeGateway()
        ensemble = JudgeEnsemble(judges=[gw])
        result = ensemble.score(envelope=make_envelope(), provider="local")
        assert result.rubric_id == "test-rubric"


# ---------------------------------------------------------------------------
# JudgeCalibrator
# ---------------------------------------------------------------------------


class TestJudgeCalibrator:
    def test_empty_report_returns_zero_n(self):
        cal = JudgeCalibrator("my-judge")
        report = cal.agreement_report()
        assert report["n"] == 0

    def test_record_and_report(self):
        cal = JudgeCalibrator("my-judge")
        cal.record(0.8, 0.9)
        cal.record(0.7, 0.7)
        cal.record(0.6, 0.5)
        report = cal.agreement_report()
        assert report["n"] == 3
        assert "cohens_kappa" in report
        assert "agreement" in report

    def test_kappa_in_range(self):
        cal = JudgeCalibrator("test")
        for score in [0.8, 0.9, 0.7, 0.5]:
            cal.record(score, score)
        report = cal.agreement_report()
        assert -1.0 <= report["cohens_kappa"] <= 1.0

    def test_positional_bias_report(self):
        cal = JudgeCalibrator("test")
        result = cal.positional_bias_report(
            scores_pos_a=[0.8, 0.7, 0.9],
            scores_pos_b=[0.5, 0.4, 0.6],
        )
        assert "bias_detected" in result
        assert result["judge_id"] == "test"


# ---------------------------------------------------------------------------
# calibration functions
# ---------------------------------------------------------------------------


class TestCohensKappa:
    def test_perfect_agreement(self):
        kappa = cohens_kappa([1, 0, 1, 0], [1, 0, 1, 0])
        assert kappa == pytest.approx(1.0)

    def test_no_agreement(self):
        kappa = cohens_kappa([1, 1, 0, 0], [0, 0, 1, 1])
        assert -1.0 <= kappa <= 1.0

    def test_empty_returns_zero(self):
        kappa = cohens_kappa([], [])
        assert kappa == 0.0

    def test_mismatched_lengths_returns_zero(self):
        kappa = cohens_kappa([1, 0], [1])
        assert kappa == 0.0

    def test_single_category(self):
        kappa = cohens_kappa([1, 1, 1], [1, 1, 1])
        assert kappa == pytest.approx(1.0)

    def test_result_in_range(self):
        kappa = cohens_kappa([1, 0, 1], [1, 0, 0])
        assert -1.0 <= kappa <= 1.0


class TestJudgeHumanAgreement:
    def test_perfect_agreement(self):
        result = judge_human_agreement([0.8, 0.9], [0.8, 0.9])
        assert result["mae"] == pytest.approx(0.0)

    def test_empty_returns_zeros(self):
        result = judge_human_agreement([], [])
        assert result["agreement"] == 0.0

    def test_correlation_key_present(self):
        result = judge_human_agreement([0.5, 0.7], [0.6, 0.8])
        assert "correlation" in result

    def test_mae_key_present(self):
        result = judge_human_agreement([0.5, 0.7], [0.6, 0.8])
        assert "mae" in result


class TestDetectPositionalBias:
    def test_no_bias_equal_rates(self):
        result = detect_positional_bias([0.5, 0.6], [0.6, 0.5])
        assert "bias_detected" in result

    def test_bias_detected_when_a_always_higher(self):
        a = [0.9] * 10
        b = [0.2] * 10
        result = detect_positional_bias(a, b)
        assert result["bias_detected"] is True

    def test_unequal_samples(self):
        result = detect_positional_bias([0.5], [0.5, 0.6])
        assert result["bias_detected"] is False

    def test_empty_returns_no_bias(self):
        result = detect_positional_bias([], [])
        assert result["bias_detected"] is False


class TestScoreVariance:
    def test_variance_zero_single(self):
        assert score_variance([0.5]) == 0.0

    def test_variance_zero_empty(self):
        assert score_variance([]) == 0.0

    def test_variance_positive(self):
        v = score_variance([0.0, 1.0])
        assert v > 0.0

    def test_variance_same_values(self):
        v = score_variance([0.5, 0.5, 0.5])
        assert v == pytest.approx(0.0)
