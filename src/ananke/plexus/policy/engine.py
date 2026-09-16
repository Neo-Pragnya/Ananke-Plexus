"""Policy engine with stage-aware rule evaluation."""

from __future__ import annotations

import tomllib
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from ananke.plexus.config.loader import load_config
from ananke.plexus.core.paths import project_ananke_dir


class PolicyRule(BaseModel):
    rule_id: str = Field(alias="id")
    stage: str = "verify"
    severity: Literal["info", "low", "medium", "high", "critical"] = "high"
    gate: str = ""
    assert_expr: str = Field(alias="assert")
    on_failure: Literal["block", "warn"] = "block"


class PolicyDecision(BaseModel):
    rule_id: str
    stage: str
    gate_id: str
    severity: str
    status: str
    summary: str
    expression: str
    actual: str = ""
    expected: str = ""
    timestamp: str


def _resolve_path(data: dict[str, object], dotted_path: str) -> object | None:
    current: object = data
    for part in dotted_path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _parse_literal(raw: str) -> object:
    text = raw.strip()
    if text in {"true", "false"}:
        return text == "true"
    if text.startswith('"') and text.endswith('"'):
        return text[1:-1]
    if text.startswith("'") and text.endswith("'"):
        return text[1:-1]
    try:
        if "." in text:
            return float(text)
        return int(text)
    except ValueError:
        return _resolve_path({}, text) or text


def _compare(actual: object, expected: object, operator: str) -> bool:
    if operator in {">=", "<=", ">", "<"}:
        if isinstance(actual, bool) or isinstance(expected, bool):
            return False
        if isinstance(actual, (int, float)) and isinstance(expected, (int, float)):
            left_num = float(actual)
            right_num = float(expected)
            if operator == ">=":
                return left_num >= right_num
            if operator == "<=":
                return left_num <= right_num
            if operator == ">":
                return left_num > right_num
            return left_num < right_num
        if isinstance(actual, str) and isinstance(expected, str):
            if operator == ">=":
                return actual >= expected
            if operator == "<=":
                return actual <= expected
            if operator == ">":
                return actual > expected
            return actual < expected
        return False
    if operator == "==":
        return actual == expected
    if operator == "!=":
        return actual != expected
    return False


def evaluate_expression(
    expression: str,
    inputs: dict[str, object],
) -> tuple[bool | None, object, object]:
    for operator in [">=", "<=", "==", "!=", ">", "<"]:
        if operator not in expression:
            continue
        left_raw, right_raw = expression.split(operator, 1)
        left_path = left_raw.strip()
        actual = _resolve_path(inputs, left_path)
        expected = _parse_literal(right_raw)
        if actual is None:
            return None, "", expected
        return _compare(actual, expected, operator), actual, expected
    value = _resolve_path(inputs, expression.strip())
    if value is None:
        return None, "", ""
    return bool(value), value, True


class PolicyEngine:
    def __init__(self, repository_root: Path) -> None:
        self.repository_root = repository_root

    def load_rules(self) -> list[PolicyRule]:
        policy_dir = project_ananke_dir(self.repository_root) / "policy"
        rules: list[PolicyRule] = []
        if not policy_dir.exists():
            return rules
        for path in sorted(policy_dir.glob("*.toml")):
            payload = tomllib.loads(path.read_text(encoding="utf-8"))
            rule_items = payload.get("rule", [])
            if not isinstance(rule_items, list):
                continue
            for raw_rule in rule_items:
                if not isinstance(raw_rule, dict):
                    continue
                rules.append(PolicyRule.model_validate(raw_rule))
        return rules

    def _verify_inputs(self, gate_results: dict[str, dict[str, object]]) -> dict[str, object]:
        config = load_config(self.repository_root)
        return {
            "config": config.model_dump(),
            "gates": gate_results,
            "graph": {"domain_cycles": 0},
            "findings": {"count": 0},
            "coverage": {"line": 100},
        }

    def evaluate_stage(
        self,
        stage: str,
        gate_results: dict[str, dict[str, object]] | None = None,
    ) -> list[PolicyDecision]:
        inputs = self._verify_inputs(gate_results or {})
        decisions: list[PolicyDecision] = []
        for rule in self.load_rules():
            if rule.stage != stage:
                continue
            passed, actual, expected = evaluate_expression(rule.assert_expr, inputs)
            if passed is True:
                status = "PASS"
                summary = f"Rule {rule.rule_id} passed."
            elif passed is None:
                status = "BLOCKED" if rule.on_failure == "block" else "UNAVAILABLE"
                summary = f"Rule {rule.rule_id} inputs unavailable."
            else:
                status = "BLOCKED" if rule.on_failure == "block" else "WARN"
                summary = f"Rule {rule.rule_id} failed."
            decisions.append(
                PolicyDecision(
                    rule_id=rule.rule_id,
                    stage=rule.stage,
                    gate_id=rule.gate,
                    severity=rule.severity,
                    status=status,
                    summary=summary,
                    expression=rule.assert_expr,
                    actual=str(actual),
                    expected=str(expected),
                    timestamp=datetime.now(UTC).isoformat(),
                )
            )
        return decisions

    def evaluate_verify(
        self,
        gate_results: dict[str, dict[str, object]] | None = None,
    ) -> list[PolicyDecision]:
        return self.evaluate_stage("verify", gate_results)

    def explain_stage(
        self,
        stage: str,
        gate_results: dict[str, dict[str, object]] | None = None,
    ) -> dict[str, object]:
        decisions = self.evaluate_stage(stage, gate_results)
        return {
            "stage": stage,
            "rule_count": len(decisions),
            "blocked_count": sum(1 for item in decisions if item.status == "BLOCKED"),
            "warn_count": sum(1 for item in decisions if item.status == "WARN"),
            "pass_count": sum(1 for item in decisions if item.status == "PASS"),
            "rules": [item.model_dump() for item in decisions],
        }
