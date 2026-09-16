import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ananke.plexus.api import Ananke
from ananke.plexus.lifecycle.evidence import append_remote_event


def test_lifecycle_idempotent_transitions(tmp_path: Path) -> None:
    app = Ananke.open(tmp_path)
    app.init_project()

    first = app.lifecycle_transition_issue("PROJ-1", "In Progress", "idem-1")
    second = app.lifecycle_transition_issue("PROJ-1", "In Progress", "idem-1")

    assert first.ok
    assert second.ok
    assert str(second.details["status"]) == "idempotent_replay"


def test_lifecycle_pr_creation(tmp_path: Path) -> None:
    app = Ananke.open(tmp_path)
    app.init_project()

    branch = app.lifecycle_branch("feature", "PROJ-2", "demo flow")
    result = app.lifecycle_create_pr("Demo PR", str(branch.details["branch"]), "idem-pr-1")

    assert result.ok
    assert str(result.details["status"]) in {"created", "idempotent_replay"}


def test_lifecycle_evidence_filters_and_limits(tmp_path: Path) -> None:
    app = Ananke.open(tmp_path)
    app.init_project()

    append_remote_event(
        tmp_path,
        "jira",
        "issue-transition",
        "remote-live",
        {"status": "remote_live_error", "attempts": 3, "elapsed_ms": 120},
    )
    append_remote_event(
        tmp_path,
        "bitbucket",
        "pr-create",
        "remote-live",
        {"status": "remote_live_success", "attempts": 1, "elapsed_ms": 50},
    )

    latest = app.lifecycle_evidence(limit=1, output_format="json")
    assert latest.ok
    assert int(latest.details["event_count"]) == 1
    latest_event = json.loads(str(latest.details["event_1"]))
    assert latest_event["service"] == "bitbucket"

    jira_only = app.lifecycle_evidence(
        service="jira",
        status="remote_live_error",
        limit=5,
        output_format="json",
    )
    assert jira_only.ok
    assert int(jira_only.details["event_count"]) == 1
    jira_event = json.loads(str(jira_only.details["event_1"]))
    assert jira_event["service"] == "jira"
    assert jira_event["status"] == "remote_live_error"


def test_lifecycle_evidence_since_hours_filter(tmp_path: Path) -> None:
    app = Ananke.open(tmp_path)
    app.init_project()

    evidence_path = tmp_path / ".ananke" / "evidence" / "lifecycle-remote-events.jsonl"
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    old_event = {
        "ts": (datetime.now(UTC) - timedelta(hours=5)).isoformat(),
        "service": "jira",
        "operation": "issue-transition",
        "mode": "remote-live",
        "status": "remote_live_error",
        "endpoint": "",
        "http_status": "500",
        "attempts": 3,
        "elapsed_ms": 100,
        "retry_delays": [0.25, 0.5],
        "mode_reason": "",
    }
    new_event = {
        "ts": datetime.now(UTC).isoformat(),
        "service": "jira",
        "operation": "issue-transition",
        "mode": "remote-live",
        "status": "remote_live_success",
        "endpoint": "",
        "http_status": "204",
        "attempts": 1,
        "elapsed_ms": 20,
        "retry_delays": [],
        "mode_reason": "",
    }
    evidence_path.write_text(
        json.dumps(old_event) + "\n" + json.dumps(new_event) + "\n",
        encoding="utf-8",
    )

    filtered = app.lifecycle_evidence(
        service="jira",
        since_hours=1,
        output_format="json",
    )
    assert filtered.ok
    assert int(filtered.details["event_count"]) == 1
    event = json.loads(str(filtered.details["event_1"]))
    assert event["status"] == "remote_live_success"


def test_lifecycle_evidence_aggregate_and_csv_export(tmp_path: Path) -> None:
    app = Ananke.open(tmp_path)
    app.init_project()

    append_remote_event(
        tmp_path,
        "jira",
        "issue-transition",
        "remote-live",
        {"status": "remote_live_error", "attempts": 3, "elapsed_ms": 120},
    )
    append_remote_event(
        tmp_path,
        "jira",
        "issue-transition",
        "remote-live",
        {"status": "remote_live_error", "attempts": 2, "elapsed_ms": 100},
    )
    append_remote_event(
        tmp_path,
        "bitbucket",
        "pr-create",
        "remote-live",
        {"status": "remote_live_success", "attempts": 1, "elapsed_ms": 50},
    )

    aggregate_result = app.lifecycle_evidence(aggregate=True, output_format="compact")
    assert aggregate_result.ok
    assert aggregate_result.details["aggregate"] is True
    assert int(aggregate_result.details["event_count"]) == 3
    assert "count=2" in str(aggregate_result.details["event_1"]) or "count=2" in str(
        aggregate_result.details.get("event_2", "")
    )

    csv_target = tmp_path / "evidence-summary.csv"
    csv_result = app.lifecycle_evidence(
        aggregate=True,
        output_format="csv",
        csv_path=str(csv_target),
    )
    assert csv_result.ok
    assert csv_result.details["csv_path"] == str(csv_target)
    csv_content = csv_target.read_text(encoding="utf-8")
    assert "service,status,count" in csv_content
    assert "jira,remote_live_error,2" in csv_content


def test_lifecycle_evidence_severity_filter_and_trend(tmp_path: Path) -> None:
    app = Ananke.open(tmp_path)
    app.init_project()

    evidence_path = tmp_path / ".ananke" / "evidence" / "lifecycle-remote-events.jsonl"
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "ts": "2026-09-16T08:10:00+00:00",
            "service": "jira",
            "operation": "issue-transition",
            "mode": "remote-live",
            "status": "remote_live_error",
            "http_status": "500",
            "attempts": 2,
            "elapsed_ms": 90,
            "retry_delays": [0.25],
            "mode_reason": "",
        },
        {
            "ts": "2026-09-16T08:20:00+00:00",
            "service": "jira",
            "operation": "issue-transition",
            "mode": "remote-live",
            "status": "remote_live_success",
            "http_status": "204",
            "attempts": 1,
            "elapsed_ms": 40,
            "retry_delays": [],
            "mode_reason": "",
        },
        {
            "ts": "2026-09-16T09:15:00+00:00",
            "service": "confluence",
            "operation": "confluence-upsert",
            "mode": "remote-live",
            "status": "policy_blocked",
            "http_status": "",
            "attempts": 0,
            "elapsed_ms": 0,
            "retry_delays": [],
            "mode_reason": "",
        },
    ]
    evidence_path.write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n",
        encoding="utf-8",
    )

    critical_only = app.lifecycle_evidence(
        severity="critical",
        output_format="json",
        limit=10,
    )
    assert critical_only.ok
    assert int(critical_only.details["event_count"]) == 1
    event = json.loads(str(critical_only.details["event_1"]))
    assert event["status"] == "remote_live_error"
    assert event["severity"] == "critical"

    trend_hour = app.lifecycle_evidence(
        trend="hour",
        output_format="json",
        limit=10,
    )
    assert trend_hour.ok
    assert trend_hour.details["trend"] == "hour"
    assert int(trend_hour.details["row_count"]) >= 2
    sample = json.loads(str(trend_hour.details["event_1"]))
    assert "bucket" in sample
    assert "severity" in sample

    trend_csv_target = tmp_path / "trend.csv"
    trend_csv = app.lifecycle_evidence(
        trend="day",
        output_format="csv",
        csv_path=str(trend_csv_target),
        limit=10,
    )
    assert trend_csv.ok
    assert trend_csv_target.exists()
    trend_csv_content = trend_csv_target.read_text(encoding="utf-8")
    assert "bucket,service,status,severity,count" in trend_csv_content
