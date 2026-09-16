"""Fake/demo backend adapter for vertical slice testing.

Simulates a coding agent that makes deterministic edits for demo and test
purposes.  Used by ``ananke run start --backend fake`` and the golden demo
in Section 57.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from ananke.plexus.backends.base import (
    BackendCapabilities,
    BackendMetadata,
    BackendRequest,
    BackendResult,
    BackendSession,
    BackendTask,
)


class FakeBackend:
    """Deterministic fake backend for demos and CI vertical-slice tests."""

    metadata = BackendMetadata(
        name="fake",
        version="0.1.0",
        description="Deterministic fake backend for demos and tests",
        adapter_type="fake",
    )
    capabilities = BackendCapabilities(
        filesystem_read=True,
        filesystem_write=True,
        shell=False,
        mcp_client=False,
        streaming=False,
        structured_output=True,
    )

    def available(self) -> bool:
        return True

    def start(self, request: BackendRequest) -> BackendSession:
        return BackendSession(
            session_id=uuid4().hex,
            backend_name="fake",
            started_at=datetime.now(UTC).isoformat(),
        )

    def send(self, session: BackendSession, task: BackendTask) -> BackendResult:
        instruction = task.instruction.lower()
        if "ping" in instruction:
            return BackendResult(task_id=task.task_id, ok=True, output="pong")

        if "implement" in instruction or "edit" in instruction:
            return self._fake_edit(task)

        return BackendResult(
            task_id=task.task_id,
            ok=True,
            output=json.dumps(
                {
                    "action": "fake_response",
                    "instruction": task.instruction[:100],
                    "note": "fake backend: no real implementation performed",
                }
            ),
        )

    def _fake_edit(self, task: BackendTask) -> BackendResult:
        worktree = task.context.get("worktree_path", "")
        if worktree:
            marker = Path(worktree) / "fake_backend_edit.txt"
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text(
                f"Fake backend edit for task {task.task_id}\n"
                f"Instruction: {task.instruction[:200]}\n"
                f"Timestamp: {datetime.now(UTC).isoformat()}\n",
                encoding="utf-8",
            )
            return BackendResult(
                task_id=task.task_id,
                ok=True,
                output=f"Wrote fake edit to {marker}",
                artifacts=[str(marker)],
            )
        return BackendResult(
            task_id=task.task_id,
            ok=True,
            output="Fake edit completed (no worktree path provided)",
        )

    def cancel(self, session: BackendSession) -> None:
        pass
