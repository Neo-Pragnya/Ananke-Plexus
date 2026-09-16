"""Hermes backend adapter (K6).

Treat Hermes as an optional orchestration/backend adapter.
Hermes state is external to Ananke evidence state.
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


class HermesBackend:
    metadata = BackendMetadata(
        name="hermes",
        version="0.1.0",
        description="Hermes orchestration adapter",
        adapter_type="hermes",
    )
    capabilities = BackendCapabilities(
        filesystem_read=True,
        filesystem_write=True,
        shell=True,
        mcp_client=True,
        streaming=True,
        persistent_session=True,
        structured_output=True,
        subagents=True,
    )

    def available(self) -> bool:
        return bool(shutil.which("hermes"))

    def start(self, request: BackendRequest) -> BackendSession:
        return BackendSession(
            session_id=uuid4().hex,
            backend_name="hermes",
            started_at=datetime.now(UTC).isoformat(),
        )

    def send(self, session: BackendSession, task: BackendTask) -> BackendResult:
        if not self.available():
            return BackendResult(task_id=task.task_id, ok=False, error="hermes CLI not found")
        result = subprocess.run(  # noqa: S603
            ["hermes", "run", "--task", task.instruction, "--session", session.session_id],
            capture_output=True,
            text=True,
            timeout=300,
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
        if not self.available():
            return
        subprocess.run(  # noqa: S603
            ["hermes", "cancel", "--session", session.session_id],
            capture_output=True,
            check=False,
        )
