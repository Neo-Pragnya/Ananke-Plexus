"""An eval run that scored nothing is a warning, never a pass."""

from __future__ import annotations

from ananke.plexus.evals.models.report import GateVerdict
from ananke.plexus.evals.models.suite import GatePolicy
from ananke.plexus.evals.policy.decisions import apply_gate_policy


def test_no_scores_is_a_warning() -> None:
    decision = apply_gate_policy("standard", "r1", [], GatePolicy())
    assert decision.verdict is GateVerdict.WARN
    assert "no-scores" in decision.warning_dimensions
