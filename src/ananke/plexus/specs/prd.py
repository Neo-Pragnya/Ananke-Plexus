"""From an approved PRD to features, acceptance ids and tests, then to BMAD contracts (three-repo plan: CAP-auto-sdd-01
and -03).

The PRD is the Markdown spec ``dev spec new`` writes, so there is one format and no second PRD engine:

* front matter with ``status: approved`` (and, if you like, ``approved_by``), set after review;
* a ``## Requirements`` section whose lines start with an id (``R1``, ``REQ-12``, ``**R3**``), optionally grouped
  under ``### Feature`` headings;
* a ``## Acceptance`` section whose lines start with the requirement id they check (``- R1: ...``; several lines per
  requirement are several criteria), and indented bullets under a requirement line count too.

**Map (-01).** Each requirement gets its feature, and each criterion a global id ``AC-<requirement>-<n>``, the test
the behaviour contract will generate for it, and the tests that already mention it (its acceptance id, or the
requirement id). The map is **consistent** when the PRD is approved, has requirements, every requirement has at least
one criterion, no id repeats and every criterion names a requirement that exists. Anything else is an error, and an
inconsistent map blocks compiling.

**Compile (-03).** For each requirement a feature folder with ``requirement.md`` and the BMAD bundle
(``contracts/bmad.py``): behaviour tests and Gherkin, a model contract filled from an API contract where one is given,
and an architecture contract naming the CALM components the requirement mentions. ``bmad-index.json`` links every file
(with its sha256) to the PRD's revision, so evidence produced later can be traced to the exact spec it answers.

Nothing here calls a model, and the same PRD gives the same map: the digest leaves out the time.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from ananke.plexus.contracts.behavior import _safe_id

SCHEMA = "ananke/prd-map@1"
INDEX_SCHEMA = "ananke/bmad-index@1"
MAX_PRD_BYTES = 2_000_000
MAX_TEST_FILES = 3_000
_REQ = re.compile(
    r"^\s*(?:[-*+]\s+)?(?:\*\*)?([A-Z][A-Z0-9]*-?\d+)(?:\*\*)?[:.)]?(?:\*\*)?\s+(.+?)\s*$"
)
_ACC = re.compile(
    r"^\s*[-*+]\s+(?:\*\*)?([A-Z][A-Z0-9]*-?\d+)(?:\*\*)?\s*[:.)\-\u2013]\s*(.+?)\s*$"
)
_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_SUB_BULLET = re.compile(r"^(\s{2,}|\t+)[-*+]\s+(.+?)\s*$")
_TEST_NAME = re.compile(
    r"(^|/)(test_[^/]*\.py|[^/]*_test\.py|[^/]*\.(?:test|spec)\.[jt]sx?|[^/]*_test\.go)$"
)
_TEST_DIRS = ("tests/", "test/", "spec/", "__tests__/", "acceptance/")


class PrdError(ValueError):
    """A PRD that can't be read or compiled; the message says what to do."""


def _front_matter(lines: list[str]) -> tuple[dict[str, str], int]:
    if not lines or lines[0].strip() != "---":
        return {}, 0
    meta: dict[str, str] = {}
    for no in range(1, len(lines)):
        if lines[no].strip() == "---":
            return meta, no + 1
        key, colon, value = lines[no].partition(":")
        if colon and key.strip():
            meta[key.strip().lower()] = value.strip().strip("'\"")
    raise PrdError(
        "the front matter starts with --- but never ends; close it with a second --- line"
    )


def _section(lines: list[str], start: int, name: str) -> tuple[int, int, int] | None:
    for no in range(start, len(lines)):
        m = _HEADING.match(lines[no])
        if m and re.match(rf"{name}\b", m.group(2).strip().lower()):
            level = len(m.group(1))
            end = len(lines)
            for j in range(no + 1, len(lines)):
                h = _HEADING.match(lines[j])
                if h and len(h.group(1)) <= level:
                    end = j
                    break
            return no + 1, end, level
    return None


def parse(text: str) -> dict[str, Any]:
    """Front matter, features, requirements and criteria, each with its line number."""
    lines = text.splitlines()
    meta, body_start = _front_matter(lines)
    title = meta.get("title", "")
    if not title:
        for line in lines[body_start:]:
            m = _HEADING.match(line)
            if m and len(m.group(1)) == 1:
                title = m.group(2).strip()
                break
    req_section = _section(lines, body_start, "requirements")
    acc_section = _section(lines, body_start, "acceptance")
    requirements: list[dict[str, Any]] = []
    features: list[dict[str, Any]] = []
    duplicates: list[dict[str, Any]] = []
    seen: dict[str, int] = {}
    if req_section is not None:
        start, end, level = req_section
        feature: dict[str, Any] | None = None
        current: dict[str, Any] | None = None
        for no in range(start, end):
            line = lines[no]
            h = _HEADING.match(line)
            if h and len(h.group(1)) > level:
                feature = {"title": h.group(2).strip(), "line": no + 1, "requirements": []}
                features.append(feature)
                current = None
                continue
            sub = _SUB_BULLET.match(line)
            if sub and current is not None and not _REQ.match(line.strip()):
                current["inline_acceptance"].append({"text": sub.group(2).strip(), "line": no + 1})
                continue
            m = _REQ.match(line)
            if not m or line.startswith(("  ", "\t")):
                continue
            rid = m.group(1)
            if rid in seen:
                duplicates.append({"id": rid, "line": no + 1, "first": seen[rid]})
                current = None
                continue
            seen[rid] = no + 1
            current = {
                "id": rid,
                "text": m.group(2).strip(),
                "line": no + 1,
                "feature": feature["title"] if feature else None,
                "inline_acceptance": [],
            }
            requirements.append(current)
            if feature is not None:
                feature["requirements"].append(rid)
    criteria: list[dict[str, Any]] = []
    if acc_section is not None:
        start, end, _ = acc_section
        for no in range(start, end):
            m = _ACC.match(lines[no])
            if m:
                criteria.append(
                    {"requirement": m.group(1), "text": m.group(2).strip(), "line": no + 1}
                )
    return {
        "meta": meta,
        "title": title,
        "requirements": requirements,
        "features": features,
        "criteria": criteria,
        "duplicates": duplicates,
        "has_requirements_section": req_section is not None,
    }


def _test_files(root: Path) -> list[Path]:
    found: list[Path] = []
    for path in sorted(root.rglob("*")):
        if len(found) >= MAX_TEST_FILES:
            break
        rel = path.relative_to(root).as_posix()
        if any(
            part.startswith(".") or part in {"node_modules", "venv", "site-packages", "__pycache__"}
            for part in path.relative_to(root).parts
        ):
            continue
        if path.is_file() and (
            _TEST_NAME.search(rel)
            or (
                rel.startswith(_TEST_DIRS)
                and path.suffix
                in {
                    ".py",
                    ".js",
                    ".ts",
                    ".feature",
                    ".go",
                }
            )
        ):
            found.append(path)
    return found


def _mentions(root: Path) -> list[tuple[str, int, str]]:
    rows = []
    for path in _test_files(root):
        try:
            if path.stat().st_size > MAX_PRD_BYTES:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel = path.relative_to(root).as_posix()
        for no, line in enumerate(text.splitlines(), start=1):
            rows.append((rel, no, line))
    return rows


def _finding(
    code: str, severity: str, message: str, line: int | None, remedy: str
) -> dict[str, Any]:
    return {
        "code": code,
        "severity": severity,
        "message": message,
        "line": line,
        "remediation": remedy,
    }


def map_prd(
    prd: Path, root: Path, *, allow_draft: bool = False, require_tests: bool = False
) -> dict[str, Any]:
    """Features, requirements, acceptance ids and tests for one PRD, with what makes it inconsistent."""
    try:
        if prd.stat().st_size > MAX_PRD_BYTES:
            raise PrdError(
                f"{prd.name} is over {MAX_PRD_BYTES // 1_000_000} MB; split it into one PRD per feature"
            )
        if prd.suffix.lower() not in {".md", ".markdown"}:
            raise PrdError(
                f"{prd.name}: unsupported format {prd.suffix or '(none)'}; give the PRD as Markdown"
            )
        raw = prd.read_bytes()
        text = raw.decode("utf-8")
    except FileNotFoundError as exc:
        raise PrdError(
            f"{prd} doesn't exist; write one with `dev spec new` or check the path"
        ) from exc
    except PermissionError as exc:
        raise PrdError(f"{prd} can't be read (permission denied); grant read access") from exc
    except UnicodeDecodeError as exc:
        raise PrdError(f"{prd.name} isn't UTF-8 text") from exc
    parsed = parse(text)
    meta = parsed["meta"]
    status = meta.get("status", "").lower()
    found: list[dict[str, Any]] = []
    if status != "approved":
        found.append(
            _finding(
                "PRD_NOT_APPROVED",
                "warning" if allow_draft else "error",
                f"the PRD's status is {status or 'not set'}, not approved",
                1,
                "review it, then set `status: approved` in its front matter",
            )
        )
    if not parsed["requirements"]:
        found.append(
            _finding(
                "NO_REQUIREMENTS",
                "error",
                "no requirement lines were found"
                + (
                    ""
                    if parsed["has_requirements_section"]
                    else " (there is no ## Requirements section)"
                ),
                None,
                "list requirements as lines starting with an id, such as `- **R1** The cart keeps items`",
            )
        )
    for dup in parsed["duplicates"]:
        found.append(
            _finding(
                "DUPLICATE_REQUIREMENT",
                "error",
                f"{dup['id']} is used again (first at line {dup['first']})",
                dup["line"],
                "give every requirement its own id",
            )
        )
    known = {r["id"] for r in parsed["requirements"]}
    for c in parsed["criteria"]:
        if c["requirement"] not in known:
            found.append(
                _finding(
                    "ACCEPTANCE_UNKNOWN_REQUIREMENT",
                    "error",
                    f"an acceptance line names {c['requirement']}, which isn't a requirement",
                    c["line"],
                    "fix the id or add the requirement",
                )
            )
    mentions = _mentions(root)
    rows = []
    for req in parsed["requirements"]:
        texts = [
            *req["inline_acceptance"],
            *(c for c in parsed["criteria"] if c["requirement"] == req["id"]),
        ]
        req_pattern = re.compile(rf"(?<![\w-]){re.escape(req['id'])}(?![\w-]|-\d)")
        req_tests = sorted(
            {f"{rel}:{no}" for rel, no, line in mentions if req_pattern.search(line)}
        )
        acceptance = []
        for idx, crit in enumerate(texts, start=1):
            acc_id = f"AC-{req['id']}-{idx}"
            acc_pattern = re.compile(rf"(?<![\w-]){re.escape(acc_id)}(?![\w-]|\d)")
            own = sorted({f"{rel}:{no}" for rel, no, line in mentions if acc_pattern.search(line)})
            acceptance.append(
                {
                    "id": acc_id,
                    "text": crit["text"],
                    "line": crit["line"],
                    "planned_test": {
                        "file": f"{_safe_id(req['id'])}/tests/test_{_safe_id(req['id'])}_behavior.py",
                        "name": f"test_ac_{idx}_{_safe_id(crit['text'])}",
                        "status": "planned",
                    },
                    "observed_tests": own,
                    "tested": "by acceptance id"
                    if own
                    else "by requirement id"
                    if req_tests
                    else "no",
                }
            )
            if not own and not req_tests:
                found.append(
                    _finding(
                        "ACCEPTANCE_UNTESTED",
                        "error" if require_tests else "warning",
                        f"{acc_id} has no test yet that mentions {acc_id} or {req['id']}",
                        crit["line"],
                        f"name {acc_id} in the test that checks it (the compiled behaviour test does)",
                    )
                )
        if not acceptance:
            found.append(
                _finding(
                    "REQUIREMENT_WITHOUT_ACCEPTANCE",
                    "error",
                    f"{req['id']} has no acceptance criterion",
                    req["line"],
                    f"add a line `- {req['id']}: how a reviewer can see it works` under ## Acceptance",
                )
            )
        rows.append(
            {
                "id": req["id"],
                "text": req["text"],
                "line": req["line"],
                "feature": req["feature"] or parsed["title"] or prd.stem,
                "acceptance": acceptance,
                "requirement_tests": req_tests,
            }
        )
    features = [
        {"id": f"F{n}", "title": f["title"], "line": f["line"], "requirements": f["requirements"]}
        for n, f in enumerate(parsed["features"], start=1)
    ]
    for f in features:
        if not f["requirements"]:
            found.append(
                _finding(
                    "FEATURE_WITHOUT_REQUIREMENTS",
                    "warning",
                    f"the feature '{f['title']}' has no requirements",
                    f["line"],
                    "add its requirements under that heading, or remove the heading",
                )
            )
    if not features and rows:
        features = [
            {
                "id": "F1",
                "title": parsed["title"] or prd.stem,
                "line": None,
                "requirements": [r["id"] for r in rows],
            }
        ]
    for f in features:
        for r in rows:
            if r["id"] in f["requirements"]:
                r["feature_id"] = f["id"]
    errors = [f for f in found if f["severity"] == "error"]
    result: dict[str, Any] = {
        "schema": SCHEMA,
        "prd": {
            "path": _rel(prd, root),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "title": parsed["title"],
            "status": status or "unset",
            "approved_by": meta.get("approved_by", ""),
            "version": meta.get("version", ""),
        },
        "summary": {
            "features": len(features),
            "requirements": len(rows),
            "acceptance": sum(len(r["acceptance"]) for r in rows),
            "acceptance_with_tests": sum(
                1 for r in rows for a in r["acceptance"] if a["tested"] != "no"
            ),
            "errors": len(errors),
            "warnings": len(found) - len(errors),
        },
        "consistent": not errors,
        "features": features,
        "requirements": rows,
        "findings": sorted(
            found, key=lambda f: (f["severity"] != "error", f["line"] or 0, f["code"])
        ),
    }
    result["digest"] = _digest(result)
    return result


def _rel(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.name


def _digest(obj: Any) -> str:
    def strip(o: Any) -> Any:
        if isinstance(o, dict):
            return {k: strip(v) for k, v in o.items() if k not in {"generated_at", "digest"}}
        if isinstance(o, list):
            return [strip(v) for v in o]
        return o

    body = json.dumps(strip(obj), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest()


def _requirement_md(req: dict[str, Any], prd_map: dict[str, Any]) -> str:
    lines = [
        f"# {req['text']}",
        "",
        f"**ID:** {req['id']}",
        "",
        f"Feature: {req['feature']}. From {prd_map['prd']['path']} line {req['line']} "
        f"(sha256 {prd_map['prd']['sha256'][:12]}).",
        "",
        "## Acceptance criteria",
        "",
        *(f"- {a['text']}" for a in req["acceptance"]),
        "",
    ]
    return "\n".join(lines)


def compile_prd(
    prd_map: dict[str, Any],
    out_dir: Path,
    *,
    api_contract: dict[str, Any] | None = None,
    calm: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """One BMAD bundle per requirement and an index linking each file to the PRD revision. An inconsistent map is
    refused: compiling contracts from a PRD that doesn't hang together would hide the gap."""
    from ananke.plexus.contracts.bmad import compile_bmad
    from ananke.plexus.specs.models import Requirement

    if not prd_map.get("consistent"):
        errors = [
            f"{f['code']}: {f['message']}"
            for f in prd_map.get("findings") or []
            if f["severity"] == "error"
        ]
        raise PrdError(
            "the PRD isn't consistent, so nothing was compiled: " + "; ".join(errors[:5])
        )
    out_dir.mkdir(parents=True, exist_ok=True)
    entries = []
    for req in prd_map["requirements"]:
        folder = out_dir / _safe_id(req["id"])
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "requirement.md").write_text(_requirement_md(req, prd_map), encoding="utf-8")
        requirement = Requirement(
            requirement_id=req["id"],
            title=req["text"],
            body=req["text"],
            acceptance_criteria=[a["text"] for a in req["acceptance"]],
        )
        compile_bmad(folder, requirement, api_contract=api_contract, calm=calm)
        files = {
            p.relative_to(out_dir).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(folder.rglob("*"))
            if p.is_file()
        }
        model_text = (folder / "model-contract.yaml").read_text(encoding="utf-8")
        entries.append(
            {
                "requirement": req["id"],
                "feature": req.get("feature_id") or "",
                "acceptance": [a["id"] for a in req["acceptance"]],
                "folder": folder.relative_to(out_dir).as_posix(),
                "api_operations_mapped": "operations:" in model_text,
                "files": files,
            }
        )
    index: dict[str, Any] = {
        "schema": INDEX_SCHEMA,
        "prd": prd_map["prd"],
        "prd_map_digest": prd_map["digest"],
        "api_contract": (
            {"contract_id": api_contract.get("contract_id"), "digest": api_contract.get("digest")}
            if api_contract is not None
            else None
        ),
        "calm": calm is not None,
        "requirements": entries,
    }
    index["digest"] = _digest(index)
    (out_dir / "bmad-index.json").write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8")
    return index


def to_markdown(prd_map: dict[str, Any]) -> str:
    s = prd_map["summary"]
    verdict = "consistent" if prd_map["consistent"] else "NOT consistent: compiling is blocked"
    lines = [
        f"# PRD map: {prd_map['prd']['title'] or prd_map['prd']['path']}",
        "",
        f"Status **{prd_map['prd']['status']}**, {verdict}. {s['features']} features, {s['requirements']} "
        f"requirements, {s['acceptance']} acceptance criteria ({s['acceptance_with_tests']} with a test).",
        "",
        "| Feature | Requirement | Acceptance id | Planned test | Tested |",
        "| --- | --- | --- | --- | --- |",
    ]
    for r in prd_map["requirements"]:
        if not r["acceptance"]:
            lines.append(f"| {r['feature']} | {r['id']} | - | - | - |")
        for a in r["acceptance"]:
            lines.append(
                f"| {r['feature']} | {r['id']} | {a['id']} | `{a['planned_test']['name']}` | {a['tested']} |"
            )
    if prd_map["findings"]:
        lines += ["", "## Findings", ""]
        for f in prd_map["findings"]:
            where = f" (line {f['line']})" if f["line"] else ""
            lines.append(
                f"- **{f['severity']}** {f['code']}{where}: {f['message']}. {f['remediation']}."
            )
    lines += ["", f"Digest: `{prd_map['digest']}`", ""]
    return "\n".join(lines)
