"""Amazon Q Developer backend adapter (K4).

Integrates via the ``q`` CLI where available.
Prefer MCP server attachment over hard-coded internals.
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


class AmazonQBackend:
    metadata = BackendMetadata(
        name="amazon-q",
        version="0.1.0",
        description="Amazon Q Developer via q CLI",
        adapter_type="amazon-q",
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
        return bool(shutil.which("q"))

    def start(self, request: BackendRequest) -> BackendSession:
        return BackendSession(
            session_id=uuid4().hex,
            backend_name="amazon-q",
            started_at=datetime.now(UTC).isoformat(),
        )

    def send(self, session: BackendSession, task: BackendTask) -> BackendResult:
        if not self.available():
            return BackendResult(task_id=task.task_id, ok=False, error="q CLI not found")
        result = subprocess.run(  # noqa: S603
            ["q", "chat", "--no-interactive", task.instruction],
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
