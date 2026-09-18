"""Dataset validator — schema validation and sensitivity check."""

from __future__ import annotations

from ananke.plexus.evals.models.dataset import DataClassification, EvalDataset


def validate_dataset(dataset: EvalDataset) -> list[str]:
    """Return list of validation issues; empty = valid."""
    issues = []
    if not dataset.id:
        issues.append("dataset.id is required")
    if not dataset.cases:
        issues.append("dataset has no cases")
    for i, case in enumerate(dataset.cases):
        if not case.id:
            issues.append(f"case[{i}].id is required")
        if case.input is None:
            issues.append(f"case[{i}] '{case.id}' has no input")
    return issues


def check_egress_policy(dataset: EvalDataset, max_classification: DataClassification) -> bool:
    """Return True if dataset sensitivity permits egress to a destination with given max classification."""
    order = list(DataClassification)
    ds_level = order.index(dataset.provenance.sensitivity)
    max_level = order.index(max_classification)
    return ds_level <= max_level
