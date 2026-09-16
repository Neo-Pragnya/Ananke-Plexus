"""GitHub Copilot backend adapter (K3).

Integrates via the ``gh`` CLI copilot extension where available.
Prefer agent skills + MCP over hard-coded internal assumptions.
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


class CopilotBackend:
    metadata = BackendMetadata(
        name="copilot",
        version="0.1.0",
        description="GitHub Copilot via gh CLI",
        adapter_type="copilot",
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
        if not shutil.which("gh"):
            return False
        result = subprocess.run(
            ["gh", "extension", "list"],
            capture_output=True,
            text=True,
            check=False,
        )
        return "copilot" in result.stdout.lower()

    def start(self, request: BackendRequest) -> BackendSession:
        return BackendSession(
            session_id=uuid4().hex,
            backend_name="copilot",
            started_at=datetime.now(UTC).isoformat(),
        )

    def send(self, session: BackendSession, task: BackendTask) -> BackendResult:
        if not self.available():
            return BackendResult(
                task_id=task.task_id,
                ok=False,
                error="gh copilot extension not available",
            )
        result = subprocess.run(  # noqa: S603
            ["gh", "copilot", "suggest", "-t", "shell", task.instruction],
            capture_output=True,
            text=True,
            timeout=120,
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
