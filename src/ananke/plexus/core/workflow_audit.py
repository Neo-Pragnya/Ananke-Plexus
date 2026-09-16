"""CI workflow security auditor — detects unpinned GitHub Actions (M4 hardening).

Spec ref: Section 35.4 — actions must be pinned by full commit SHA for
supply-chain security.  This module flags tag-based pins.
"""

from __future__ import annotations

import re
from pathlib import Path

_USES_PATTERN = re.compile(r"uses:\s+([^\s#]+)")
_SHA_PATTERN = re.compile(r"@[a-f0-9]{40}$")
_TAG_PATTERN = re.compile(r"@(v[\d.]+|release/[\w.]+|\w+/v[\d.]+)$")


def audit_workflow_pins(repository_root: Path) -> list[dict[str, str]]:
    """Return a list of unpinned action usages across all workflow files."""
    workflows_dir = repository_root / ".github" / "workflows"
    if not workflows_dir.exists():
        return []

    findings: list[dict[str, str]] = []
    for workflow_file in sorted(workflows_dir.glob("*.yml")):
        lines = workflow_file.read_text(encoding="utf-8").splitlines()
        for lineno, line in enumerate(lines, 1):
            match = _USES_PATTERN.search(line)
            if not match:
                continue
            action_ref = match.group(1).strip()
            if _SHA_PATTERN.search(action_ref):
                continue
            if "@" not in action_ref:
                continue
            findings.append(
                {
                    "file": workflow_file.name,
                    "line": str(lineno),
                    "action": action_ref,
                    "severity": "medium",
                    "message": (
                        f"Action '{action_ref}' is not pinned by full commit SHA. "
                        "Use @<40-char-sha> # version-tag for supply-chain safety."
                    ),
                }
            )
    return findings
