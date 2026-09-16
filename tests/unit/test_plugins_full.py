"""Tests for plugin discovery and metadata (A8)."""

from ananke.plexus.plugins.discovery import discover_all_plugins, discover_plugins
from ananke.plexus.plugins.metadata import PluginMetadata


def test_discover_plugins_empty_group():
    # No adapters registered in test env
    result = discover_plugins("ananke.graph")
    assert isinstance(result, dict)


def test_discover_all_plugins_returns_dict():
    result = discover_all_plugins()
    assert "ananke.graph" in result
    assert "ananke.backend" in result
    assert "ananke.gate" in result


def test_plugin_metadata_model():
    meta = PluginMetadata(
        plugin_id="test-graph",
        plugin_type="graph",
        api_version="1.0",
        plugin_version="0.1.0",
        capabilities={"build", "impact"},
        description="Test graph plugin",
    )
    assert meta.plugin_id == "test-graph"
    assert "build" in meta.capabilities


def test_plugin_metadata_optional_version_range():
    meta = PluginMetadata(
        plugin_id="x",
        plugin_type="backend",
        api_version="1",
        plugin_version="1.0.0",
        min_ananke_version="0.5",
        max_ananke_version="1.0",
    )
    assert meta.min_ananke_version == "0.5"
    assert meta.max_ananke_version == "1.0"
