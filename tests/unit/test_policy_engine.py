import json
from pathlib import Path

from ananke.plexus.api import Ananke
from ananke.plexus.policy.engine import PolicyEngine, evaluate_expression


def test_evaluate_expression_comparisons() -> None:
    inputs = {
        "config": {"ananke": {"fail_closed": True}},
        "coverage": {"line": 91},
        "gates": {"pytest": {"status": "PASS"}},
    }

    result, actual, expected = evaluate_expression(
        "config.ananke.fail_closed == true",
        inputs,
    )
    assert result is True
    assert actual is True
    assert expected is True

    result, actual, expected = evaluate_expression("coverage.line >= 85", inputs)
    assert result is True
    assert actual == 91
    assert expected == 85

    result, actual, expected = evaluate_expression('gates.pytest.status == "PASS"', inputs)
    assert result is True
    assert actual == "PASS"
    assert expected == "PASS"


def test_policy_engine_blocks_failed_rule(tmp_path: Path) -> None:
    app = Ananke.open(tmp_path)
    app.init_project()
    policy_path = tmp_path / ".ananke" / "policy" / "default.toml"
    policy_path.write_text(
        """[[rule]]
id = "tests.minimum-coverage"
stage = "verify"
severity = "high"
gate = "pytest"
assert = "coverage.line >= 120"
on_failure = "block"
""",
        encoding="utf-8",
    )

    decisions = PolicyEngine(tmp_path).evaluate_verify({"pytest": {"status": "PASS"}})
    assert len(decisions) == 1
    assert decisions[0].status == "BLOCKED"


def test_policy_explain_command_output(tmp_path: Path) -> None:
    app = Ananke.open(tmp_path)
    app.init_project()

    result = app.policy_explain("verify")
    assert result.ok
    assert int(result.details["rule_count"]) >= 1
    payload = json.loads(str(result.details["rule_1"]))
    assert payload["rule_id"]
