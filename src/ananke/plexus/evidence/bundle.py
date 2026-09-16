"""Evidence bundle generation."""

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from ananke.plexus.evidence.hash import sha256_file
from ananke.plexus.evidence.sarif import write_sarif


def create_evidence_bundle(
    repository_root: Path,
    gate_summary: dict[str, str],
    gate_outcomes: list[Mapping[str, object]] | None = None,
    policy_decisions: list[Mapping[str, object]] | None = None,
) -> Path:
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
    bundle = repository_root / ".ananke" / "evidence" / run_id
    (bundle / "gates").mkdir(parents=True, exist_ok=True)

    run = {
        "run_id": run_id,
        "created_at": datetime.now(UTC).isoformat(),
        "gates": gate_summary,
        "policy_decisions": len(policy_decisions or []),
    }
    run_path = bundle / "run.json"
    run_path.write_text(json.dumps(run, indent=2) + "\n", encoding="utf-8")
    run_hash = sha256_file(run_path)

    policy_path = bundle / "policy-decisions.jsonl"
    with policy_path.open("w", encoding="utf-8") as handle:
        for item in policy_decisions or []:
            handle.write(json.dumps(dict(item), sort_keys=True) + "\n")
    policy_hash = sha256_file(policy_path)

    gate_hashes: dict[str, str] = {}
    outcomes_list = [dict(item) for item in (gate_outcomes or [])]
    for item in outcomes_list:
        gate_id = str(item.get("gate_id", "unknown"))
        gate_path = bundle / "gates" / f"{gate_id}.json"
        gate_path.write_text(
            json.dumps(item, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        gate_hashes[f"gates/{gate_id}.json"] = sha256_file(gate_path)

    sarif_path = bundle / "gates" / "sast.sarif"
    write_sarif(sarif_path, outcomes_list)
    gate_hashes["gates/sast.sarif"] = sha256_file(sarif_path)

    manifest = {
        "run_id": run_id,
        "files": {
            "run.json": run_hash,
            "policy-decisions.jsonl": policy_hash,
            **gate_hashes,
        },
    }
    manifest_path = bundle / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    manifest_hash = sha256_file(manifest_path)

    checksums = bundle / "checksums.sha256"
    checksums.write_text(
        "\n".join(
            [
                f"{run_hash}  run.json",
                f"{policy_hash}  policy-decisions.jsonl",
                f"{manifest_hash}  manifest.json",
                *[f"{value}  {key}" for key, value in gate_hashes.items()],
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    return bundle
