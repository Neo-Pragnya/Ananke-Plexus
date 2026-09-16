"""Tests for CI workflow action SHA auditor."""

from ananke.plexus.core.workflow_audit import audit_workflow_pins


def test_no_workflows_dir(tmp_path):
    findings = audit_workflow_pins(tmp_path)
    assert findings == []


def test_pinned_by_sha_no_finding(tmp_path):
    workflows_dir = tmp_path / ".github" / "workflows"
    workflows_dir.mkdir(parents=True)
    sha = "a" * 40
    (workflows_dir / "ci.yml").write_text(
        f"jobs:\n  build:\n    steps:\n      - uses: actions/checkout@{sha}\n",
        encoding="utf-8",
    )
    findings = audit_workflow_pins(tmp_path)
    assert findings == []


def test_tag_based_pin_finding(tmp_path):
    workflows_dir = tmp_path / ".github" / "workflows"
    workflows_dir.mkdir(parents=True)
    (workflows_dir / "ci.yml").write_text(
        "jobs:\n  build:\n    steps:\n      - uses: actions/checkout@v4\n",
        encoding="utf-8",
    )
    findings = audit_workflow_pins(tmp_path)
    assert len(findings) == 1
    assert findings[0]["action"] == "actions/checkout@v4"
    assert findings[0]["severity"] == "medium"


def test_multiple_files_multiple_findings(tmp_path):
    workflows_dir = tmp_path / ".github" / "workflows"
    workflows_dir.mkdir(parents=True)
    for name in ("ci.yml", "security.yml"):
        (workflows_dir / name).write_text(
            "      - uses: actions/setup-python@v5\n",
            encoding="utf-8",
        )
    findings = audit_workflow_pins(tmp_path)
    assert len(findings) == 2


def test_no_at_sign_no_finding(tmp_path):
    workflows_dir = tmp_path / ".github" / "workflows"
    workflows_dir.mkdir(parents=True)
    (workflows_dir / "ci.yml").write_text(
        "      - run: echo hello\n",
        encoding="utf-8",
    )
    findings = audit_workflow_pins(tmp_path)
    assert findings == []
