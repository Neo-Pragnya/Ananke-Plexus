"""Enterprise judge gateway — routes to approved providers; handles credentials, redaction, retries."""

from __future__ import annotations

import hashlib
import time
from datetime import UTC, datetime
from typing import Any, ClassVar

from ananke.plexus.evals.judges.base import JudgeInputEnvelope, JudgeResult


class EnterpriseJudgeGateway:
    """
    Routes judge requests to enterprise-approved model providers.
    No vendor SDK is imported at module level — each provider is resolved lazily.
    """

    SUPPORTED_PROVIDERS: ClassVar[set[str]] = {
        "azure",
        "bedrock",
        "local",
        "custom",
        "anthropic",
        "openai",
    }

    def __init__(
        self,
        allowed_providers: list[str] | None = None,
        allowed_models: list[str] | None = None,
        max_cost_per_call_usd: float = 0.50,
        redact_fields: list[str] | None = None,
    ) -> None:
        self._allowed_providers = set(allowed_providers or self.SUPPORTED_PROVIDERS)
        self._allowed_models = set(allowed_models) if allowed_models else None
        self._max_cost = max_cost_per_call_usd
        self._redact_fields = redact_fields or ["password", "token", "api_key", "secret"]

    def _redact(self, text: str) -> str:
        import re

        for field in self._redact_fields:
            text = re.sub(
                rf"(?i)({re.escape(field)})\s*[=:]\s*\S+",
                r"\1=<REDACTED>",
                text,
            )
        return text

    def score(
        self,
        *,
        envelope: JudgeInputEnvelope,
        provider: str = "local",
        model: str | None = None,
        judge_id: str = "enterprise-gateway",
        temperature: float = 0.0,
    ) -> JudgeResult:
        if provider not in self._allowed_providers:
            raise PermissionError(
                f"Provider '{provider}' not in allowed list: {self._allowed_providers}"
            )
        if self._allowed_models and model and model not in self._allowed_models:
            raise PermissionError(f"Model '{model}' not approved")

        prompt = self._redact(envelope.to_prompt())
        prompt_hash = hashlib.sha256(prompt.encode()).hexdigest()[:12]

        start = time.monotonic()
        result_text, score, passed = self._dispatch(
            provider, model, prompt, envelope.rubric, temperature
        )
        elapsed_ms = int((time.monotonic() - start) * 1000)

        return JudgeResult(
            judge_id=judge_id,
            provider=provider,
            model=model,
            rubric_id=envelope.rubric.rubric_id,
            rubric_version=envelope.rubric.version,
            score=score,
            normalized_score=max(0.0, min(1.0, score)),
            passed=passed,
            reason=result_text,
            prompt_hash=prompt_hash,
            latency_ms=elapsed_ms,
            deterministic=False,
            timestamp=datetime.now(tz=UTC),
        )

    def _dispatch(
        self,
        provider: str,
        model: str | None,
        prompt: str,
        rubric: Any,
        temperature: float,
    ) -> tuple[str, float, bool]:
        if provider == "local":
            return self._local_heuristic(prompt, rubric)
        raise NotImplementedError(
            f"Provider '{provider}' requires the appropriate adapter package. "
            "Install ananke-plexus[eval-mlflow] or configure a supported provider."
        )

    def _local_heuristic(self, prompt: str, rubric: Any) -> tuple[str, float, bool]:
        words = len(prompt.split())
        score = min(1.0, words / 500)
        passed = score >= rubric.pass_threshold
        return f"local heuristic score={score:.2f} (words={words})", score, passed

    def list_providers(self) -> list[str]:
        return sorted(self._allowed_providers)
