"""Tests for gate policy decisions and threshold configuration."""

from __future__ import annotations

import pytest

from ananke.plexus.evals.models.report import GateVerdict
from ananke.plexus.evals.models.score import EvalScore, EvalStatus
from ananke.plexus.evals.models.suite import GatePolicy, GatePolicyRule
from ananke.plexus.evals.policy.decisions import apply_gate_policy
from ananke.plexus.evals.policy.thresholds import check_dependency_allowlist, load_eval_config

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_score(
    evaluator_id: str = "ev",
    dimension: str = "outcome",
    metric: str = "score",
    status: EvalStatus = EvalStatus.PASS,
    normalized: float | None = 0.9,
    value: float | None = None,
) -> EvalScore:
    return EvalScore(
        evaluator_id=evaluator_id,
        dimension=dimension,
        metric=metric,
        status=status,
        normalized_score=normalized,
        value=value,
    )


# ---------------------------------------------------------------------------
# apply_gate_policy
# ---------------------------------------------------------------------------


class TestApplyGatePolicy:
    def test_pass_with_empty_policy(self):
        scores = [make_score(status=EvalStatus.PASS)]
        decision = apply_gate_policy("suite", "run", scores, GatePolicy())
        assert decision.verdict == GateVerdict.PASS

    def test_pass_when_no_rules_match(self):
        scores = [make_score(dimension="outcome", normalized=0.9)]
        policy = GatePolicy(rules={"safety": GatePolicyRule(minimum=0.8, on_failure="block")})
        decision = apply_gate_policy("suite", "run", scores, policy)
        assert decision.verdict == GateVerdict.PASS

    def test_block_when_below_minimum(self):
        scores = [make_score(dimension="outcome", normalized=0.5)]
        policy = GatePolicy(rules={"outcome": GatePolicyRule(minimum=0.8, on_failure="block")})
        decision = apply_gate_policy("suite", "run", scores, policy)
        assert decision.verdict == GateVerdict.BLOCK

    def test_warn_when_below_minimum_and_on_failure_warn(self):
        scores = [make_score(dimension="outcome", normalized=0.5)]
        policy = GatePolicy(rules={"outcome": GatePolicyRule(minimum=0.8, on_failure="warn")})
        decision = apply_gate_policy("suite", "run", scores, policy)
        assert decision.verdict == GateVerdict.WARN

    def test_review_when_below_minimum_and_on_failure_review(self):
        scores = [make_score(dimension="outcome", normalized=0.5)]
        policy = GatePolicy(rules={"outcome": GatePolicyRule(minimum=0.8, on_failure="review")})
        decision = apply_gate_policy("suite", "run", scores, policy)
        assert decision.verdict == GateVerdict.REVIEW

    def test_block_when_above_maximum(self):
        scores = [make_score(dimension="latency", normalized=0.9)]
        policy = GatePolicy(rules={"latency": GatePolicyRule(maximum=0.5, on_failure="block")})
        decision = apply_gate_policy("suite", "run", scores, policy)
        assert decision.verdict == GateVerdict.BLOCK

    def test_block_when_required_and_fail(self):
        scores = [make_score(dimension="safety", status=EvalStatus.FAIL, normalized=0.0)]
        policy = GatePolicy(rules={"safety": GatePolicyRule(required=True, on_failure="block")})
        decision = apply_gate_policy("suite", "run", scores, policy)
        assert decision.verdict == GateVerdict.BLOCK

    def test_block_takes_precedence_over_warn(self):
        scores = [
            make_score("ev1", "safety", normalized=0.3),
            make_score("ev2", "efficiency", normalized=0.3),
        ]
        policy = GatePolicy(
            rules={
                "safety": GatePolicyRule(minimum=0.5, on_failure="block"),
                "efficiency": GatePolicyRule(minimum=0.5, on_failure="warn"),
            }
        )
        decision = apply_gate_policy("suite", "run", scores, policy)
        assert decision.verdict == GateVerdict.BLOCK

    def test_blocking_dimensions_populated(self):
        scores = [make_score(dimension="safety", normalized=0.3)]
        policy = GatePolicy(rules={"safety": GatePolicyRule(minimum=0.8, on_failure="block")})
        decision = apply_gate_policy("suite", "run", scores, policy)
        assert "safety" in decision.blocking_dimensions

    def test_warning_dimensions_populated(self):
        scores = [make_score(dimension="efficiency", normalized=0.3)]
        policy = GatePolicy(rules={"efficiency": GatePolicyRule(minimum=0.8, on_failure="warn")})
        decision = apply_gate_policy("suite", "run", scores, policy)
        assert "efficiency" in decision.warning_dimensions

    def test_review_dimensions_populated(self):
        scores = [make_score(dimension="quality", normalized=0.3)]
        policy = GatePolicy(rules={"quality": GatePolicyRule(minimum=0.8, on_failure="review")})
        decision = apply_gate_policy("suite", "run", scores, policy)
        assert "quality" in decision.review_dimensions

    def test_suite_id_in_decision(self):
        decision = apply_gate_policy("my-suite", "run", [], GatePolicy())
        assert decision.suite_id == "my-suite"

    def test_run_id_in_decision(self):
        decision = apply_gate_policy("suite", "my-run", [], GatePolicy())
        assert decision.run_id == "my-run"

    def test_decided_at_set(self):
        decision = apply_gate_policy("suite", "run", [], GatePolicy())
        assert decision.decided_at is not None

    def test_evidence_populated_on_failure(self):
        scores = [make_score(dimension="outcome", normalized=0.3)]
        policy = GatePolicy(rules={"outcome": GatePolicyRule(minimum=0.8, on_failure="block")})
        decision = apply_gate_policy("suite", "run", scores, policy)
        assert len(decision.evidence) > 0

    def test_pass_when_at_minimum(self):
        scores = [make_score(dimension="outcome", normalized=0.8)]
        policy = GatePolicy(rules={"outcome": GatePolicyRule(minimum=0.8, on_failure="block")})
        decision = apply_gate_policy("suite", "run", scores, policy)
        assert decision.verdict == GateVerdict.PASS

    def test_blocks_property(self):
        scores = [make_score(dimension="safety", normalized=0.0)]
        policy = GatePolicy(rules={"safety": GatePolicyRule(minimum=0.8, on_failure="block")})
        decision = apply_gate_policy("suite", "run", scores, policy)
        assert decision.blocks is True


# ---------------------------------------------------------------------------
# load_eval_config
# ---------------------------------------------------------------------------


class TestLoadEvalConfig:
    def test_no_config_file_returns_empty(self, tmp_path):
        config = load_eval_config(tmp_path)
        assert config == {}

    def test_loads_yaml_config(self, tmp_path):
        config_dir = tmp_path / ".ananke" / "evals"
        config_dir.mkdir(parents=True)
        (config_dir / "config.yaml").write_text("suites:\n  standard:\n    pass_threshold: 0.9\n")
        config = load_eval_config(tmp_path)
        assert isinstance(config, dict)

    def test_config_contains_suites_key(self, tmp_path):
        config_dir = tmp_path / ".ananke" / "evals"
        config_dir.mkdir(parents=True)
        (config_dir / "config.yaml").write_text("suites:\n  standard:\n    pass_threshold: 0.85\n")
        config = load_eval_config(tmp_path)
        assert "suites" in config

    def test_pass_threshold_loaded(self, tmp_path):
        config_dir = tmp_path / ".ananke" / "evals"
        config_dir.mkdir(parents=True)
        (config_dir / "config.yaml").write_text("suites:\n  standard:\n    pass_threshold: 0.95\n")
        config = load_eval_config(tmp_path)
        assert config["suites"]["standard"]["pass_threshold"] == pytest.approx(0.95)


# ---------------------------------------------------------------------------
# check_dependency_allowlist
# ---------------------------------------------------------------------------


class TestCheckDependencyAllowlist:
    def test_allowed_when_no_restrictions(self):
        allowed, _reason = check_dependency_allowlist("numpy", {})
        assert allowed is True

    def test_allowed_when_in_allowed_packages(self):
        config = {"dependencies": {"allowed_packages": ["numpy", "pandas"]}}
        allowed, _reason = check_dependency_allowlist("numpy", config)
        assert allowed is True

    def test_blocked_when_explicit_approval_required(self):
        config = {
            "dependencies": {
                "allowed_packages": ["numpy"],
                "require_explicit_approval": ["openai"],
            }
        }
        allowed, reason = check_dependency_allowlist("openai", config)
        assert allowed is False
        assert "approval" in reason

    def test_blocked_when_deny_external_saas(self):
        config = {
            "dependencies": {
                "allowed_packages": ["numpy"],
                "deny_external_saas": True,
            }
        }
        allowed, reason = check_dependency_allowlist("stripe", config)
        assert allowed is False
        assert "deny_external_saas" in reason

    def test_allowed_when_package_in_allowed_and_deny_saas(self):
        config = {
            "dependencies": {
                "allowed_packages": ["numpy", "openai"],
                "deny_external_saas": True,
            }
        }
        allowed, _reason = check_dependency_allowlist("openai", config)
        assert allowed is True
