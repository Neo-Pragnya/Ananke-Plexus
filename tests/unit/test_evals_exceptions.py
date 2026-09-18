"""Tests for eval harness exception types."""

from __future__ import annotations

import pytest

from ananke.plexus.evals.exceptions import (
    EvalAdapterUnavailableError,
    EvalDataGovernanceError,
    EvalDatasetError,
    EvalError,
    EvalJudgeError,
    EvalPolicyViolationError,
    EvalSuiteNotFoundError,
)

# ---------------------------------------------------------------------------
# Importability and hierarchy
# ---------------------------------------------------------------------------


class TestExceptionImportability:
    def test_eval_error_importable(self):
        assert EvalError is not None

    def test_eval_suite_not_found_error_importable(self):
        assert EvalSuiteNotFoundError is not None

    def test_eval_dataset_error_importable(self):
        assert EvalDatasetError is not None

    def test_eval_adapter_unavailable_error_importable(self):
        assert EvalAdapterUnavailableError is not None

    def test_eval_policy_violation_error_importable(self):
        assert EvalPolicyViolationError is not None

    def test_eval_judge_error_importable(self):
        assert EvalJudgeError is not None

    def test_eval_data_governance_error_importable(self):
        assert EvalDataGovernanceError is not None


class TestExceptionHierarchy:
    def test_eval_error_is_exception(self):
        assert issubclass(EvalError, Exception)

    def test_suite_not_found_is_eval_error(self):
        assert issubclass(EvalSuiteNotFoundError, EvalError)

    def test_dataset_error_is_eval_error(self):
        assert issubclass(EvalDatasetError, EvalError)

    def test_adapter_unavailable_is_eval_error(self):
        assert issubclass(EvalAdapterUnavailableError, EvalError)

    def test_policy_violation_is_eval_error(self):
        assert issubclass(EvalPolicyViolationError, EvalError)

    def test_judge_error_is_eval_error(self):
        assert issubclass(EvalJudgeError, EvalError)

    def test_data_governance_error_is_eval_error(self):
        assert issubclass(EvalDataGovernanceError, EvalError)


class TestExceptionRaising:
    def test_eval_error_can_be_raised(self):
        with pytest.raises(EvalError):
            raise EvalError("base error")

    def test_eval_error_message(self):
        with pytest.raises(EvalError, match="test message"):
            raise EvalError("test message")

    def test_suite_not_found_can_be_raised(self):
        with pytest.raises(EvalSuiteNotFoundError):
            raise EvalSuiteNotFoundError("suite not found")

    def test_suite_not_found_is_caught_as_eval_error(self):
        with pytest.raises(EvalError):
            raise EvalSuiteNotFoundError("caught as eval error")

    def test_dataset_error_can_be_raised(self):
        with pytest.raises(EvalDatasetError):
            raise EvalDatasetError("dataset error")

    def test_adapter_unavailable_can_be_raised(self):
        with pytest.raises(EvalAdapterUnavailableError):
            raise EvalAdapterUnavailableError("adapter not available")

    def test_policy_violation_can_be_raised(self):
        with pytest.raises(EvalPolicyViolationError):
            raise EvalPolicyViolationError("policy violated")

    def test_judge_error_can_be_raised(self):
        with pytest.raises(EvalJudgeError):
            raise EvalJudgeError("judge failed")

    def test_data_governance_error_can_be_raised(self):
        with pytest.raises(EvalDataGovernanceError):
            raise EvalDataGovernanceError("data governance violation")

    def test_suite_not_found_caught_as_exception(self):
        with pytest.raises(Exception):
            raise EvalSuiteNotFoundError("caught as base Exception")

    def test_all_errors_have_str_representation(self):
        errors = [
            EvalError("base"),
            EvalSuiteNotFoundError("suite"),
            EvalDatasetError("dataset"),
            EvalAdapterUnavailableError("adapter"),
            EvalPolicyViolationError("policy"),
            EvalJudgeError("judge"),
            EvalDataGovernanceError("governance"),
        ]
        for err in errors:
            assert str(err) != ""

    def test_dataset_error_is_caught_as_eval_error(self):
        with pytest.raises(EvalError):
            raise EvalDatasetError("caught")

    def test_judge_error_is_caught_as_eval_error(self):
        with pytest.raises(EvalError):
            raise EvalJudgeError("caught")

    def test_governance_error_is_caught_as_eval_error(self):
        with pytest.raises(EvalError):
            raise EvalDataGovernanceError("caught")
