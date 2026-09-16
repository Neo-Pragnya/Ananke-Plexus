"""Plugin discovery for entry-point based adapters (A8)."""

from __future__ import annotations

from importlib.metadata import entry_points
from typing import Any

from ananke.plexus.plugins.metadata import PluginMetadata

_KNOWN_GROUPS = [
    "ananke.graph",
    "ananke.backend",
    "ananke.spec_provider",
    "ananke.gate",
    "ananke.lifecycle",
]


def discover_plugins(group: str) -> dict[str, str]:
    """Return a name→value map of entry points for a given group."""
    result: dict[str, str] = {}
    eps = entry_points()
    selected = eps.select(group=group)
    for item in selected:
        result[item.name] = item.value
    return result


def discover_all_plugins() -> dict[str, dict[str, str]]:
    """Return all plugin entry points across all known groups."""
    return {group: discover_plugins(group) for group in _KNOWN_GROUPS}


def load_plugin(group: str, name: str) -> Any:
    """Load and return the plugin object for a given group/name pair."""
    eps = entry_points()
    for item in eps.select(group=group):
        if item.name == name:
            return item.load()
    raise ImportError(f"Plugin not found: group={group!r} name={name!r}")


def load_plugin_metadata(group: str, name: str) -> PluginMetadata | None:
    """Attempt to load plugin metadata from the plugin object."""
    try:
        plugin = load_plugin(group, name)
    except ImportError:
        return None
    meta = getattr(plugin, "metadata", None)
    if isinstance(meta, PluginMetadata):
        return meta
    if isinstance(meta, dict):
        try:
            return PluginMetadata.model_validate(meta)
        except Exception:
            return None
    return None
