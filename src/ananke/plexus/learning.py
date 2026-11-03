"""Learning candidates: turn a lesson from a run into reviewed, reusable capability memory
(three-repo plan: AN-LEARNING-CANDIDATES, AN-PROMOTION).

    capture → redact → evaluate (positive + regression) → review → promote to a writable source

A candidate is a folder under ``.ananke/learning/candidates/<name>/`` in the same format Dex Trove's candidate gate
reads (``SKILL.md``, ``evaluation.json`` with schema ``dex-trove/evaluation@1``, ``review.json``), plus
``provenance.json``. So a lesson can go to an internal folder through Ananke, or to Dex Trove through
``dex candidate publish``.

Rules:

* Project data stays private. A candidate holds only the lesson's own text: no source files are copied, secrets are
  redacted and then re-scanned (a remaining match refuses the capture), and absolute workspace and home paths are
  replaced. Candidates are ``internal`` unless marked otherwise.
* Evaluation cases are commands that must exit 0. They run only with explicit opt-in (``allow_commands=True``),
  inside the workspace, with a timeout.
* Promotion needs: at least one positive and one regression case, every case passing, an approving review newer than
  the evaluation, a *writable* destination, and matching visibility (an internal lesson never reaches a public
  source). It publishes a new entry through the provider port, never in place of an existing one.
"""

from __future__ import annotations

import json
import re
import shlex
import shutil
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ananke.plexus.registry.providers import ProviderSet, SourceError
from ananke.plexus.registry.secrets import redact_files, scan_files

EVALUATION_SCHEMA = "dex-trove/evaluation@1"
PROVENANCE_SCHEMA = "ananke/learning-provenance@1"
GATE_FILES = ("evaluation.json", "review.json", "provenance.json")
_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class LearningError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"[{code}] {message}")
        self.code, self.message = code, message


def _now() -> str:
    return datetime.now(tz=UTC).isoformat(timespec="seconds")


def candidates_dir(workspace: Path) -> Path:
    return workspace.resolve() / ".ananke" / "learning" / "candidates"


def capture(
    workspace: Path,
    name: str,
    *,
    description: str,
    body: str,
    cases: list[dict[str, str]],
    runs: list[str] | None = None,
    visibility: str = "internal",
    licence: str = "MIT",
) -> Path:
    """Write a candidate. ``cases``: ``[{id, kind: positive|regression, command}]``."""
    if not _NAME.match(name):
        raise LearningError("INVALID_NAME", f"{name!r}: use lowercase words joined by hyphens")
    if visibility not in ("internal", "public"):
        raise LearningError("INVALID_VISIBILITY", "visibility is internal or public")
    if len(description.strip()) < 40:
        raise LearningError(
            "WEAK_DESCRIPTION", "say what the lesson does and when to use it (40+ characters)"
        )
    ids = [c.get("id") for c in cases]
    if (
        not cases
        or len(set(ids)) != len(ids)
        or any(
            c.get("kind") not in ("positive", "regression") or not c.get("command") for c in cases
        )
    ):
        raise LearningError(
            "INVALID_CASES", "cases need unique ids, kind positive|regression and a command"
        )
    ws = workspace.resolve()
    text = f"---\nname: {name}\ndescription: {json.dumps(description.strip())}\nlicense: {licence}\n---\n\n{body.strip()}\n"
    text = text.replace(str(ws), "<workspace>").replace(str(Path.home()), "~")
    files, redactions = redact_files({"SKILL.md": text.encode()})
    if scan_files(files):
        raise LearningError(
            "SECRET_REMAINS", "secret-shaped text survived redaction; rewrite the lesson without it"
        )
    folder = candidates_dir(ws) / name
    if folder.exists():
        raise LearningError(
            "CANDIDATE_EXISTS", f"{name} already exists; evaluate it, or choose another name"
        )
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_bytes(files["SKILL.md"])
    evaluation = {
        "schema": EVALUATION_SCHEMA,
        "finished_at": None,
        "cases": [
            {"id": c["id"], "kind": c["kind"], "command": c["command"], "passed": None}
            for c in cases
        ],
    }
    (folder / "evaluation.json").write_text(json.dumps(evaluation, indent=2) + "\n")
    provenance = {
        "schema": PROVENANCE_SCHEMA,
        "captured_at": _now(),
        "runs": list(runs or []),
        "visibility": visibility,
        "redactions": redactions,
        "published": [],
    }
    (folder / "provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")
    return folder


def _read(folder: Path, name: str) -> dict[str, Any]:
    path = folder / name
    if not path.is_file():
        raise LearningError("MISSING_FILE", f"{folder.name} has no {name}")
    data: dict[str, Any] = json.loads(path.read_text())
    return data


def evaluate(
    folder: Path, workspace: Path, *, allow_commands: bool = False, timeout: int = 300
) -> dict[str, Any]:
    """Run every case's command in the workspace; a case passes when it exits 0."""
    if not allow_commands:
        raise LearningError(
            "COMMANDS_NOT_ALLOWED",
            "evaluation runs the cases' commands; pass allow_commands=True to agree",
        )
    ev = _read(folder, "evaluation.json")
    for case in ev["cases"]:
        try:
            res = subprocess.run(  # noqa: S603 - the reviewer opted in to running these commands
                shlex.split(case["command"]),
                cwd=workspace,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            case["passed"], case["exit"] = res.returncode == 0, res.returncode
        except subprocess.TimeoutExpired:
            case["passed"], case["exit"] = False, "timeout"
        except (OSError, ValueError) as exc:  # not found, or not a parseable command
            case["passed"], case["exit"] = False, type(exc).__name__
    ev["finished_at"] = _now()
    (folder / "evaluation.json").write_text(json.dumps(ev, indent=2) + "\n")
    return ev


def review(folder: Path, *, approve: bool, reviewer: str, reason: str) -> dict[str, Any]:
    if not reviewer.strip() or not reason.strip():
        raise LearningError("INVALID_REVIEW", "a review needs a reviewer and a reason")
    decision = {
        "decision": "approved" if approve else "rejected",
        "reviewer": reviewer,
        "reason": reason,
        "at": _now(),
    }
    (folder / "review.json").write_text(json.dumps(decision, indent=2) + "\n")
    return decision


def gate(folder: Path) -> list[str]:
    """Why this candidate can't be promoted yet (empty when it can)."""
    problems = []
    ev = _read(folder, "evaluation.json")
    kinds = {c["kind"] for c in ev["cases"]}
    if not {"positive", "regression"} <= kinds:
        problems.append("evaluation needs a positive and a regression case")
    if ev.get("finished_at") is None:
        problems.append("not evaluated yet")
    failing = [c["id"] for c in ev["cases"] if c.get("passed") is not True]
    if failing and ev.get("finished_at"):
        problems.append(f"evaluation cases failed: {', '.join(failing)}")
    rv_path = folder / "review.json"
    if not rv_path.is_file():
        problems.append("not reviewed")
    else:
        rv = json.loads(rv_path.read_text())
        if rv.get("decision") != "approved":
            problems.append(f"review {rv.get('decision')}: {rv.get('reason')}")
        elif (folder / "evaluation.json").stat().st_mtime > rv_path.stat().st_mtime:
            problems.append("the evaluation changed after the review; review again")
    if scan_files({"SKILL.md": (folder / "SKILL.md").read_bytes()}):
        problems.append("secret-shaped text in SKILL.md")
    return problems


def promote(
    folder: Path, providers: ProviderSet, source_id: str, *, approved_by: str
) -> dict[str, Any]:
    """Publish an approved, passing candidate into a writable source. Raises with every reason it can't."""
    problems = gate(folder)
    prov = _read(folder, "provenance.json")
    target = providers.providers.get(source_id)
    if target is None:
        problems.append(f"no source {source_id!r}")
    elif not target.writable:
        problems.append(
            f"source {source_id} is read-only"
            + (
                "; for Dex Trove use `dex candidate publish` on this folder"
                if type(target).__name__ == "TroveProvider"
                else ""
            )
        )
    elif (
        prov.get("visibility") == "internal"
        and getattr(target, "visibility", "internal") == "public"
    ):
        problems.append(f"an internal lesson can't go to public source {source_id}")
    if problems:
        raise LearningError("NOT_PROMOTABLE", "; ".join(problems))
    if target is None:  # unreachable: reported above; keeps type checkers honest
        raise LearningError("NOT_PROMOTABLE", f"no source {source_id!r}")
    with tempfile.TemporaryDirectory(prefix="ananke-promote-") as tmp:
        staging = (
            Path(tmp) / folder.name
        )  # published under the candidate's own name, without the gate files
        shutil.copytree(
            folder, staging, ignore=lambda _d, names: [n for n in names if n in GATE_FILES]
        )
        try:
            receipt = target.publish(staging, approved_by=approved_by)
        except SourceError as exc:
            raise LearningError(exc.code, exc.message) from exc
    prov["published"].append(receipt | {"at": _now()})
    (folder / "provenance.json").write_text(json.dumps(prov, indent=2) + "\n")
    return receipt


__all__ = ["LearningError", "capture", "evaluate", "gate", "promote", "review"]
