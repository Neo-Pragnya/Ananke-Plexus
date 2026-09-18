"""Tests for eval adapters (MLflow, DeepEval, InspectAI, Ragas, OpenEvals, AgentEvals)."""

from __future__ import annotations

import pytest

from ananke.plexus.evals.adapters.agentevals.adapter import AgentEvalsAdapter
from ananke.plexus.evals.adapters.deepeval.adapter import DeepEvalAdapter
from ananke.plexus.evals.adapters.inspect_ai.adapter import InspectAIAdapter
from ananke.plexus.evals.adapters.mlflow.adapter import MLflowAdapter
from ananke.plexus.evals.adapters.openevals.adapter import OpenEvalsAdapter
from ananke.plexus.evals.adapters.ragas.adapter import RagasAdapter

ALL_ADAPTER_CLASSES = [
    MLflowAdapter,
    DeepEvalAdapter,
    InspectAIAdapter,
    RagasAdapter,
    OpenEvalsAdapter,
    AgentEvalsAdapter,
]

ALL_ADAPTER_INSTANCES = [cls() for cls in ALL_ADAPTER_CLASSES]


# ---------------------------------------------------------------------------
# MLflowAdapter
# ---------------------------------------------------------------------------


class TestMLflowAdapter:
    def setup_method(self):
        self.adapter = MLflowAdapter()

    def test_adapter_id_set(self):
        assert self.adapter.adapter_id == "ananke.adapters.mlflow"

    def test_adapter_id_nonempty(self):
        assert self.adapter.adapter_id != ""

    def test_doctor_returns_dict(self):
        result = self.adapter.doctor()
        assert isinstance(result, dict)

    def test_doctor_has_available_key(self):
        result = self.adapter.doctor()
        assert "available" in result

    def test_doctor_available_is_bool(self):
        result = self.adapter.doctor()
        assert isinstance(result["available"], bool)

    def test_doctor_has_status_key(self):
        result = self.adapter.doctor()
        assert "status" in result

    def test_doctor_adapter_key(self):
        result = self.adapter.doctor()
        assert result["adapter"] == "ananke.adapters.mlflow"

    def test_available_method_returns_bool(self):
        result = self.adapter.available()
        assert isinstance(result, bool)

    def test_unavailable_when_mlflow_not_installed(self):
        try:
            import mlflow  # noqa: F401

            is_available = True
        except ImportError:
            is_available = False
        assert self.adapter.available() == is_available

    def test_doctor_status_contains_unavailable_when_not_installed(self):
        if not self.adapter.available():
            result = self.adapter.doctor()
            assert "unavailable" in result["status"].lower()

    def test_capabilities_dict(self):
        caps = self.adapter.capabilities()
        assert isinstance(caps, dict)
        assert "available" in caps

    def test_export_report_returns_false_when_unavailable(self):
        if not self.adapter.available():
            from ananke.plexus.evals.models.report import EvalReport

            report = EvalReport(run_id="r1", suite_id="s1")
            result = self.adapter.export_report(report)
            assert result is False

    def test_license_attribute(self):
        assert self.adapter.license == "Apache-2.0"


# ---------------------------------------------------------------------------
# DeepEvalAdapter
# ---------------------------------------------------------------------------


class TestDeepEvalAdapter:
    def setup_method(self):
        self.adapter = DeepEvalAdapter()

    def test_adapter_id_set(self):
        assert self.adapter.adapter_id == "ananke.adapters.deepeval"

    def test_doctor_returns_dict(self):
        result = self.adapter.doctor()
        assert isinstance(result, dict)

    def test_doctor_has_available(self):
        assert "available" in self.adapter.doctor()

    def test_doctor_available_is_bool(self):
        assert isinstance(self.adapter.doctor()["available"], bool)

    def test_available_when_deepeval_not_installed(self):
        try:
            import deepeval  # noqa: F401

            is_available = True
        except ImportError:
            is_available = False
        assert self.adapter.available() == is_available

    def test_evaluate_returns_skipped_when_unavailable(self):
        if not self.adapter.available():
            from ananke.plexus.evals.models.score import EvalStatus

            scores = self.adapter.evaluate(case=None, trace=None, context=None)
            assert len(scores) == 1
            assert scores[0].status == EvalStatus.SKIPPED


# ---------------------------------------------------------------------------
# All adapters — universal contract tests
# ---------------------------------------------------------------------------


class TestAllAdapters:
    @pytest.mark.parametrize(
        "adapter", ALL_ADAPTER_INSTANCES, ids=[cls.adapter_id for cls in ALL_ADAPTER_CLASSES]
    )
    def test_adapter_id_is_nonempty_string(self, adapter):
        assert isinstance(adapter.adapter_id, str)
        assert adapter.adapter_id != ""

    @pytest.mark.parametrize(
        "adapter", ALL_ADAPTER_INSTANCES, ids=[cls.adapter_id for cls in ALL_ADAPTER_CLASSES]
    )
    def test_doctor_returns_dict(self, adapter):
        result = adapter.doctor()
        assert isinstance(result, dict)

    @pytest.mark.parametrize(
        "adapter", ALL_ADAPTER_INSTANCES, ids=[cls.adapter_id for cls in ALL_ADAPTER_CLASSES]
    )
    def test_doctor_has_available_key(self, adapter):
        result = adapter.doctor()
        assert "available" in result

    @pytest.mark.parametrize(
        "adapter", ALL_ADAPTER_INSTANCES, ids=[cls.adapter_id for cls in ALL_ADAPTER_CLASSES]
    )
    def test_available_returns_bool(self, adapter):
        result = adapter.available()
        assert isinstance(result, bool)

    @pytest.mark.parametrize(
        "adapter", ALL_ADAPTER_INSTANCES, ids=[cls.adapter_id for cls in ALL_ADAPTER_CLASSES]
    )
    def test_adapter_version_attribute_exists(self, adapter):
        assert hasattr(adapter, "adapter_version")
        assert isinstance(adapter.adapter_version, str)

    @pytest.mark.parametrize(
        "adapter", ALL_ADAPTER_INSTANCES, ids=[cls.adapter_id for cls in ALL_ADAPTER_CLASSES]
    )
    def test_requires_package_attribute_exists(self, adapter):
        assert hasattr(adapter, "requires_package")
        assert isinstance(adapter.requires_package, str)

    @pytest.mark.parametrize(
        "adapter", ALL_ADAPTER_INSTANCES, ids=[cls.adapter_id for cls in ALL_ADAPTER_CLASSES]
    )
    def test_license_attribute_is_str_when_present(self, adapter):
        if hasattr(adapter, "license"):
            assert isinstance(adapter.license, str)
