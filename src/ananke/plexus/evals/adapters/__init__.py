"""Third-party eval framework adapters — all lazy-loaded, graceful when unavailable."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class EvaluatorAdapter(Protocol):
    adapter_id: str
    adapter_version: str
    requires_package: str

    def available(self) -> bool: ...

    def capabilities(self) -> dict[str, Any]: ...

    def evaluate(self, *, case: Any, trace: Any, context: Any) -> list[Any]: ...
