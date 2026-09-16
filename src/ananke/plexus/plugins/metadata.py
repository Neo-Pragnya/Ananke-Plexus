"""Plugin metadata model (A8)."""

from __future__ import annotations

from pydantic import BaseModel


class PluginMetadata(BaseModel):
    plugin_id: str
    plugin_type: str
    api_version: str
    plugin_version: str
    capabilities: set[str] = set()
    min_ananke_version: str | None = None
    max_ananke_version: str | None = None
    description: str = ""
