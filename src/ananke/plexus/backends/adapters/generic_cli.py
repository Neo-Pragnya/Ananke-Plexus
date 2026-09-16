"""Generic CLI backend adapter (K2).

Invokes a user-configured command with a JSON-encoded task on stdin and reads
a JSON-encoded result from stdout.  This is the fallback adapter for any
agent that exposes a simple stdin/stdout interface.
"""

from __future__ import annotations

import json
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


class GenericCLIBackend:
    """Wraps an arbitrary CLI command as an agent backend."""

    def __init__(self, command: list[str], name: str = "generic-cli") -> None:
        self._command = command
        self.metadata = BackendMetadata(
            name=name,
            version="0.1.0",
            description="Generic CLI agent backend",
            adapter_type="generic-cli",
        )
        self.capabilities = BackendCapabilities(
            filesystem_read=True,
            filesystem_write=True,
            shell=True,
            streaming=False,
            structured_output=True,
        )

    def available(self) -> bool:
        return shutil.which(self._command[0]) is not None

    def start(self, request: BackendRequest) -> BackendSession:
        return BackendSession(
            session_id=uuid4().hex,
            backend_name=self.metadata.name,
            started_at=datetime.now(UTC).isoformat(),
        )

    def send(self, session: BackendSession, task: BackendTask) -> BackendResult:
        if not self.available():
            return BackendResult(
                task_id=task.task_id,
                ok=False,
                error=f"binary not found: {self._command[0]}",
            )
        payload = json.dumps(
            {
                "session_id": session.session_id,
                "task_id": task.task_id,
                "instruction": task.instruction,
                "context": task.context,
            }
        )
        try:
            result = subprocess.run(  # noqa: S603
                [shutil.which(self._command[0]), *self._command[1:]],  # type: ignore[list-item]
                input=payload,
                capture_output=True,
                text=True,
                timeout=300,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return BackendResult(task_id=task.task_id, ok=False, error="timeout")
        except OSError as exc:
            return BackendResult(task_id=task.task_id, ok=False, error=str(exc))

        ok = result.returncode == 0
        output = result.stdout.strip()
        error = result.stderr.strip() if not ok else ""
        return BackendResult(task_id=task.task_id, ok=ok, output=output, error=error)

    def cancel(self, session: BackendSession) -> None:
        pass
