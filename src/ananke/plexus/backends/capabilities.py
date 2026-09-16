"""Capability detection helpers for agent backends."""

from __future__ import annotations

import shutil

from ananke.plexus.backends.base import BackendCapabilities


def detect_cli_capabilities(binary: str) -> BackendCapabilities:
    available = shutil.which(binary) is not None
    return BackendCapabilities(
        filesystem_read=available,
        filesystem_write=available,
        shell=available,
        mcp_client=False,
        streaming=False,
        persistent_session=False,
        structured_output=False,
        subagents=False,
    )
