"""Token and context budget observatory (ADV07): how much graph context is worth its tokens, over time.

A fixed list of questions with the symbols each should find (the ``ananke context eval`` format) is answered at
several token budgets. For each budget: recall, tokens spent, the full-file baseline's tokens and recall per thousand
tokens. The **knee** is the smallest budget that reaches the best recall: spending more buys nothing on these
questions.

``record`` appends the run to a history file with the commit it measured, so later runs show **regressions**: a
question whose recall dropped, or whose cost rose by more than a fifth for the same recall. Questions nothing finds
are listed: they're the ones the map or the question set needs work on.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ananke.plexus.insight import InsightError, digest, git

SCHEMA = "ananke/context-observatory@1"
DEFAULT_BUDGETS = (500, 1000, 2000, 4000)
HISTORY = Path(".ananke/context/observatory.jsonl")


def load_cases(path: Path) -> list[dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise InsightError(
            f"{path} doesn't exist; write a JSON list of {{'question': ..., 'expect': [...]}}"
        ) from exc
    except ValueError as exc:
        raise InsightError(f"{path.name} is malformed JSON: {exc}") from exc
    if (
        not isinstance(data, list)
        or not data
        or not all(isinstance(c, dict) and c.get("question") for c in data)
    ):
        raise InsightError(
            f"{path.name} isn't a non-empty JSON list of {{'question': ..., 'expect': [...]}}"
        )
    if not all(isinstance(c.get("expect"), list) and c["expect"] for c in data):
        raise InsightError(
            f"{path.name}: every question needs an 'expect' list, or recall can't be measured"
        )
    return data


def observe(
    root: Path, cases: list[dict[str, Any]], budgets: tuple[int, ...] = DEFAULT_BUDGETS
) -> dict[str, Any]:
    from ananke.plexus.retrieval import evaluate

    if not budgets or any(b < 100 or b > 200_000 for b in budgets):
        raise InsightError("budgets must each be between 100 and 200000 tokens")
    root = root.resolve()
    rows = []
    per_question: dict[str, dict[str, Any]] = {}
    for budget in sorted(set(budgets)):
        r = evaluate(root, cases, budget=budget)
        rows.append(
            {
                "budget": budget,
                "recall": r["recall"],
                "tokens": r["tokens"],
                "baseline_tokens": r["baseline_tokens"],
                "saving": r["saving"],
                "recall_per_1k_tokens": round(r["recall"] / (r["tokens"] / 1000), 4)
                if r["tokens"]
                else 0.0,
            }
        )
        for q in r["questions"]:
            per_question.setdefault(q["question"], {})[str(budget)] = {
                "recall": q["recall"],
                "tokens": q["tokens"],
                "missing": q["missing"],
            }
    best = max(r["recall"] for r in rows)
    knee = next(r["budget"] for r in rows if r["recall"] >= best)
    top = str(max(budgets))
    never_found = sorted(q for q, by in per_question.items() if by[top]["recall"] == 0)
    try:
        commit = git(root, "rev-parse", "HEAD").strip()
    except InsightError:
        commit = ""
    report: dict[str, Any] = {
        "schema": SCHEMA,
        "commit": commit,
        "questions": len(cases),
        "budgets": rows,
        "best_recall": best,
        "knee_budget": knee,
        "never_found": never_found,
        "per_question": per_question,
        "limits": [
            "Recall counts the expected symbols the answer names; it doesn't judge whether the answer is useful.",
            "The baseline reads every file containing a question word, a generous upper bound on a careless read.",
        ],
    }
    report["digest"] = digest(report)
    return report


def compare(previous: dict[str, Any], current: dict[str, Any]) -> list[dict[str, Any]]:
    """Questions that got worse since the previous run, at the knee budget of the current one."""
    out = []
    budget = str(current["knee_budget"])
    for q, now in current["per_question"].items():
        before = (previous.get("per_question") or {}).get(q, {}).get(budget)
        if before is None:
            continue
        cur = now[budget]
        if cur["recall"] < before["recall"]:
            out.append(
                {
                    "question": q,
                    "change": "recall dropped",
                    "before": before["recall"],
                    "after": cur["recall"],
                    "missing": cur["missing"],
                }
            )
        elif (
            cur["recall"] == before["recall"]
            and before["tokens"]
            and cur["tokens"] > 1.2 * before["tokens"]
        ):
            out.append(
                {
                    "question": q,
                    "change": "cost rose over 20%",
                    "before": before["tokens"],
                    "after": cur["tokens"],
                }
            )
    return out


def record(
    root: Path, report: dict[str, Any], history: Path | None = None
) -> tuple[Path, dict[str, Any] | None]:
    path = history or (root / HISTORY)
    previous = None
    if path.is_file():
        lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
        if lines:
            try:
                previous = json.loads(lines[-1])
            except ValueError:
                previous = None
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {**report, "generated_at": datetime.now(UTC).isoformat(timespec="seconds")}
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, sort_keys=True) + "\n")
    return path, previous


def to_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Context budget observatory",
        "",
        f"{report['questions']} questions at commit `{report['commit'][:12] or 'unknown'}`. Best recall "
        f"{report['best_recall']}, reached at {report['knee_budget']} tokens per question.",
        "",
        "| Budget | Recall | Tokens | Full-file baseline | Saving | Recall per 1k tokens |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for r in report["budgets"]:
        lines.append(
            f"| {r['budget']} | {r['recall']} | {r['tokens']} | {r['baseline_tokens']} | {r['saving']} | "
            f"{r['recall_per_1k_tokens']} |"
        )
    if report["never_found"]:
        lines += ["", "## Found at no budget", "", *(f"- {q}" for q in report["never_found"])]
    if report.get("regressions") is not None:
        lines += ["", "## Since the previous run", ""]
        lines += [
            f"- {r['question']}: {r['change']} ({r['before']} → {r['after']})"
            for r in report["regressions"]
        ] or ["- no regressions"]
    lines += ["", f"Digest: `{report['digest']}`", ""]
    return "\n".join(lines)
