"""Dataset versioning — content hashes, case fingerprints."""

from __future__ import annotations

import hashlib
import json

from ananke.plexus.evals.models.dataset import EvalDataset


def content_hash(dataset: EvalDataset) -> str:
    payload = json.dumps(
        [c.model_dump(mode="json") for c in dataset.cases],
        sort_keys=True,
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def case_hashes(dataset: EvalDataset) -> dict[str, str]:
    result = {}
    for case in dataset.cases:
        payload = json.dumps(case.model_dump(mode="json"), sort_keys=True).encode()
        result[case.id] = hashlib.sha256(payload).hexdigest()
    return result


def stamp_provenance(dataset: EvalDataset) -> EvalDataset:
    """Return a copy of the dataset with provenance hashes filled in."""
    hashes = case_hashes(dataset)
    updated_provenance = dataset.provenance.model_copy(update={"case_hashes": hashes})
    return dataset.model_copy(update={"provenance": updated_provenance})
