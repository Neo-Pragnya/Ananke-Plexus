from pathlib import Path

from ananke.plexus.apm.audit import audit_manifest
from ananke.plexus.apm.manifest import load_manifest
from ananke.plexus.apm.sandbox import (
    permission_allows_network,
    permission_allows_shell,
    permission_allows_write,
)


def test_audit_manifest_reports_pass_for_sample() -> None:
    manifest = Path(__file__).parents[1] / "fixtures" / "sample-skill" / "ananke-skill.toml"
    findings = audit_manifest(manifest)
    assert any(item.startswith("PASS") or item.startswith("WARN") for item in findings)


def test_sandbox_checks_for_sample() -> None:
    manifest_path = Path(__file__).parents[1] / "fixtures" / "sample-skill" / "ananke-skill.toml"
    manifest = load_manifest(manifest_path)

    assert permission_allows_shell(manifest, "ananke graph review")
    assert not permission_allows_network(manifest, "example.com")
    assert permission_allows_write(manifest, ".ananke/evidence/run-1/output.json")
