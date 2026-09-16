"""Coverage for lifecycle http client and execution compensation paths."""

from ananke.plexus.execution.compensation import compensate_plan
from ananke.plexus.execution.plan import ExecutionPlan, ExecutionStep
from ananke.plexus.lifecycle.http_client import json_request


def test_json_request_unsupported_scheme():
    result = json_request(
        url="ftp://example.com/api",
        method="GET",
        payload={},
        auth_header="Bearer fake",
    )
    assert result["ok"] is False
    assert "unsupported" in str(result.get("error", ""))


def test_json_request_network_failure():
    result = json_request(
        url="http://localhost:19876/no-such-server",
        method="GET",
        payload={},
        auth_header="Bearer fake",
        timeout_seconds=0.5,
        retries=0,
    )
    assert result["ok"] is False


def test_compensate_plan_post_evidence(tmp_path):
    plan = ExecutionPlan(
        plan_id="p-ev",
        spec_id="s",
        run_id="run-ev",
        steps=[
            ExecutionStep(
                step_id="ev1",
                step_type="post_evidence",
                state="SUCCEEDED",
            )
        ],
    )
    result = compensate_plan(tmp_path, plan)
    assert result is not None
    retry_steps = [s for s in result.steps if s.action == "retry_evidence_comment"]
    assert len(retry_steps) >= 1
