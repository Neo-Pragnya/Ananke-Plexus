"""MLflow adapter — export EvalReport and AgentTrace to MLflow experiments."""

from __future__ import annotations

from typing import Any

from ananke.plexus.evals.models.report import EvalReport
from ananke.plexus.evals.models.trace import AgentTrace


def _mlflow_available() -> bool:
    try:
        import mlflow  # noqa: F401

        return True
    except ImportError:
        return False


class MLflowAdapter:
    adapter_id = "ananke.adapters.mlflow"
    adapter_version = "1"
    requires_package = "mlflow"
    license = "Apache-2.0"

    def available(self) -> bool:
        return _mlflow_available()

    def capabilities(self) -> dict[str, Any]:
        return {
            "available": self.available(),
            "experiment_tracking": True,
            "trace_export": True,
            "score_logging": True,
            "artifact_storage": True,
            "offline": True,
        }

    def export_report(
        self,
        report: EvalReport,
        *,
        experiment_name: str | None = None,
        tracking_uri: str | None = None,
    ) -> bool:
        if not self.available():
            return False
        try:
            import mlflow

            if tracking_uri:
                mlflow.set_tracking_uri(tracking_uri)
            experiment = experiment_name or f"/ananke/{report.suite_id}"
            mlflow.set_experiment(experiment)
            with mlflow.start_run(run_name=report.run_id):
                for score in report.scores:
                    if score.normalized_score is not None:
                        mlflow.log_metric(
                            f"{score.dimension}.{score.metric}", score.normalized_score
                        )
                mlflow.set_tag("ananke.suite", report.suite_id)
                mlflow.set_tag("ananke.run_id", report.run_id)
                if report.gate_decision:
                    mlflow.set_tag("ananke.gate_verdict", report.gate_decision.verdict.value)
            return True
        except Exception:
            return False

    def export_trace(self, trace: AgentTrace, *, tracking_uri: str | None = None) -> bool:
        if not self.available():
            return False
        try:
            import mlflow

            if tracking_uri:
                mlflow.set_tracking_uri(tracking_uri)
            with mlflow.start_run(run_name=trace.run_id):
                mlflow.set_tag("ananke.runtime", trace.runtime)
                mlflow.set_tag("ananke.trace_id", trace.trace_id)
                if trace.usage.total_tokens:
                    mlflow.log_metric("total_tokens", trace.usage.total_tokens)
                if trace.usage.cost_usd:
                    mlflow.log_metric("cost_usd", trace.usage.cost_usd)
            return True
        except Exception:
            return False

    def doctor(self) -> dict[str, Any]:
        available = self.available()
        return {
            "adapter": self.adapter_id,
            "available": available,
            "license": self.license,
            "status": "ok"
            if available
            else "unavailable — install mlflow: pip install ananke-plexus[eval-mlflow]",
        }
