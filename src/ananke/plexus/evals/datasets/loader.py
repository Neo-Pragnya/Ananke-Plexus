"""Dataset loader — YAML/JSON with provenance tracking."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ananke.plexus.evals.models.case import EvalCase
from ananke.plexus.evals.models.dataset import DatasetProvenance, EvalDataset


def _load_yaml_or_json(path: Path) -> Any:
    text = path.read_text(encoding="utf-8")
    if path.suffix in (".yaml", ".yml"):
        try:
            import yaml

            return yaml.safe_load(text)
        except ImportError:
            import re

            simple = re.sub(r"#.*", "", text)
            return json.loads(simple)
    return json.loads(text)


def load_dataset(path: Path) -> EvalDataset:
    """Load an EvalDataset from YAML or JSON."""
    raw = _load_yaml_or_json(path)
    cases_raw = raw.get("cases", [])
    cases = [
        EvalCase(
            id=c.get("id") or c.get("case_id", f"case-{i}"),
            input=c.get("input"),
            expected=c.get("expected"),
            metadata=c.get("metadata", {}),
            tags=c.get("tags", []),
        )
        for i, c in enumerate(cases_raw)
    ]
    provenance_raw = raw.get("provenance", {})
    provenance = DatasetProvenance(**provenance_raw) if provenance_raw else DatasetProvenance()

    return EvalDataset(
        id=raw.get("id", path.stem),
        version=int(raw.get("version", 1)),
        description=raw.get("description", ""),
        dataset_class=raw.get("dataset_class") or raw.get("class", "regression"),
        cases=cases,
        provenance=provenance,
        tags=raw.get("tags", []),
    )


def load_datasets_from_dir(directory: Path) -> dict[str, EvalDataset]:
    result: dict[str, EvalDataset] = {}
    for p in (
        sorted(directory.glob("*.yaml"))
        + sorted(directory.glob("*.yml"))
        + sorted(directory.glob("*.json"))
    ):
        try:
            ds = load_dataset(p)
            result[ds.id] = ds
        except Exception:
            pass
    return result
