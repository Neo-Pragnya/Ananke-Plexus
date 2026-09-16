"""Backend Protocol and data models (K1)."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field


class BackendCapabilities(BaseModel):
    filesystem_read: bool = False
    filesystem_write: bool = False
    shell: bool = False
    mcp_client: bool = False
    streaming: bool = False
    persistent_session: bool = False
    structured_output: bool = False
    subagents: bool = False


class BackendMetadata(BaseModel):
    name: str
    version: str = "0.0.0"
    description: str = ""
    adapter_type: str = "generic"


class BackendRequest(BaseModel):
    spec_id: str
    task_description: str
    context_files: list[str] = Field(default_factory=list)
    extra: dict[str, Any] = Field(default_factory=dict)


class BackendTask(BaseModel):
    task_id: str
    instruction: str
    context: dict[str, Any] = Field(default_factory=dict)


class BackendResult(BaseModel):
    task_id: str
    ok: bool
    output: str = ""
    artifacts: list[str] = Field(default_factory=list)
    error: str = ""


class BackendSession(BaseModel):
    session_id: str
    backend_name: str
    started_at: str = ""


@runtime_checkable
class AgentBackend(Protocol):
    metadata: BackendMetadata
    capabilities: BackendCapabilities

    def start(self, request: BackendRequest) -> BackendSession: ...

    def send(self, session: BackendSession, task: BackendTask) -> BackendResult: ...

    def cancel(self, session: BackendSession) -> None: ...

    def available(self) -> bool: ...
