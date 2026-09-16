"""Backend registry — discovers and manages all registered agent backends."""

from __future__ import annotations

from pathlib import Path

from ananke.plexus.backends.adapters.amazon_q import AmazonQBackend
from ananke.plexus.backends.adapters.copilot import CopilotBackend
from ananke.plexus.backends.adapters.fake import FakeBackend
from ananke.plexus.backends.adapters.generic_cli import GenericCLIBackend
from ananke.plexus.backends.adapters.hermes import HermesBackend
from ananke.plexus.backends.adapters.kiro import KiroBackend

_ALL_BACKENDS = [
    FakeBackend(),
    CopilotBackend(),
    AmazonQBackend(),
    KiroBackend(),
    HermesBackend(),
]


class BackendRegistry:
    def __init__(self, backends: list[object]) -> None:
        self._backends = backends

    @classmethod
    def load(cls, repository_root: Path) -> BackendRegistry:
        return cls(list(_ALL_BACKENDS))

    def to_list(self) -> list[dict[str, object]]:
        result = []
        for b in self._backends:
            meta = getattr(b, "metadata", None)
            available = b.available() if callable(getattr(b, "available", None)) else False  # type: ignore[arg-type]
            result.append(
                {
                    "name": getattr(meta, "name", "?") if meta else "?",
                    "description": getattr(meta, "description", "") if meta else "",
                    "adapter_type": getattr(meta, "adapter_type", "") if meta else "",
                    "status": "available" if available else "unavailable",
                }
            )
        return result

    def get_backend(self, name: str) -> object | None:
        for b in self._backends:
            meta = getattr(b, "metadata", None)
            if meta and getattr(meta, "name", "") == name:
                return b
        return None

    def doctor(self, name: str) -> dict[str, object]:
        backend = self.get_backend(name)
        if backend is None:
            generic = GenericCLIBackend([name], name=name)
            available = generic.available()
            return {
                "ok": available,
                "name": name,
                "summary": f"{name}: {'available' if available else 'not found'}",
            }
        available = backend.available() if callable(getattr(backend, "available", None)) else False  # type: ignore[arg-type]
        meta = getattr(backend, "metadata", None)
        caps = getattr(backend, "capabilities", None)
        return {
            "ok": available,
            "name": name,
            "description": getattr(meta, "description", "") if meta else "",
            "adapter_type": getattr(meta, "adapter_type", "") if meta else "",
            "capabilities": caps.model_dump() if caps else {},
            "summary": f"{name}: {'available' if available else 'unavailable'}",
        }

    def test(self, name: str) -> dict[str, object]:
        backend = self.get_backend(name)
        if backend is None:
            backend = GenericCLIBackend([name], name=name)
        available = backend.available() if callable(getattr(backend, "available", None)) else False  # type: ignore[arg-type]
        if not available:
            return {"ok": False, "name": name, "summary": f"{name}: binary not found"}
        from uuid import uuid4

        from ananke.plexus.backends.base import BackendRequest, BackendTask

        session = backend.start(BackendRequest(spec_id="test", task_description="ping"))  # type: ignore[arg-type]
        result = backend.send(session, BackendTask(task_id=uuid4().hex, instruction="ping"))  # type: ignore[arg-type]
        return {
            "ok": result.ok,
            "name": name,
            "summary": f"{name}: {'responded' if result.ok else 'no response'}",
            "output": result.output[:200],
            "error": result.error[:200],
        }
