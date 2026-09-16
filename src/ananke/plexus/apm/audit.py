"""APM manifest auditing."""

from pathlib import Path

from ananke.plexus.apm.manifest import load_manifest


def audit_manifest(manifest_path: Path) -> list[str]:
    manifest = load_manifest(manifest_path)
    findings: list[str] = []

    if manifest.permissions.network:
        findings.append("WARN: network access requested")

    if any("*" in item for item in manifest.permissions.shell):
        findings.append("WARN: broad shell wildcard detected")

    if any(item == "**" for item in manifest.permissions.filesystem_write):
        findings.append("BLOCK: unrestricted filesystem write requested")

    if not findings:
        findings.append("PASS: manifest permissions are narrow")

    return findings
