"""Kiro backend adapter (K5).

Integrates via custom agent + MCP + hook capability.
Uses documented CLI integration only — no internal Kiro assumptions.
"""

from __future__ import annotations

import shutil
import subprocess
from datetime import UTC, datetime
from uuid import uuid4

from ananke.plexus.backends.base import (
    BackendCapabilities,
    BackendMetadata,
    BackendRequest,
    BackendResult,
    BackendSession,
    BackendTask,
)


class KiroBackend:
    metadata = BackendMetadata(
        name="kiro",
        version="0.1.0",
        description="Kiro agent via kiro CLI",
        adapter_type="kiro",
    )
    capabilities = BackendCapabilities(
        filesystem_read=True,
        filesystem_write=True,
        shell=True,
        mcp_client=True,
        streaming=False,
        structured_output=False,
    )

    def available(self) -> bool:
        return bool(shutil.which("kiro"))

    def start(self, request: BackendRequest) -> BackendSession:
        return BackendSession(
            session_id=uuid4().hex,
            backend_name="kiro",
            started_at=datetime.now(UTC).isoformat(),
        )

    def send(self, session: BackendSession, task: BackendTask) -> BackendResult:
        if not self.available():
            return BackendResult(task_id=task.task_id, ok=False, error="kiro CLI not found")
        result = subprocess.run(  # noqa: S603
            ["kiro", "run", "--task", task.instruction],
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        ok = result.returncode == 0
        return BackendResult(
            task_id=task.task_id,
            ok=ok,
            output=result.stdout.strip()[:2000],
            error=result.stderr.strip()[:500] if not ok else "",
        )

    def cancel(self, session: BackendSession) -> None:
        pass
