"""Answering a question about code with as few tokens as it needs (three-repo plan: CAP-context-retrieval-01..04).

``ask`` turns a question into a bounded bundle of source:

1. **Plan**: the question's words (``orderTotal`` and ``order_total`` count as *order* and *total*) are matched to
   symbol names, then to docstrings and file paths. Each candidate says which words it matched.
2. **Neighbourhood**: the best few symbols with their source, then the signatures of what calls them and what they
   call, until the token budget is spent. What didn't fit is counted, not silently dropped.
3. **No repeats, and a cache**: passages that overlap in the same file are merged, and the answer is kept for this
   snapshot of the repository (uncommitted edits included), so asking again is free until the code changes.
4. **Measured**: ``evaluate`` runs a list of questions with the symbols each should find, and reports recall and the
   tokens spent against the naive way: reading every file that contains one of the question's words.

Everything comes from Ananke's own graph and the files; nothing is sent anywhere.
"""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from ananke.plexus.context import (
    _read_cache,
    _write_cache,
    estimate_tokens,
    fresh_graph,
    source_state,
)

SCHEMA = "ananke/context-answer@1"
SYMBOL_KINDS = ("function", "method", "class")
_STOP = set(
    [
        "the",
        "a",
        "an",
        "and",
        "or",
        "of",
        "to",
        "in",
        "on",
        "for",
        "with",
        "by",
        "from",
        "as",
        "at",
        "is",
        "are",
        "be",
        "it",
        "this",
        "that",
        "what",
        "where",
        "which",
        "who",
        "how",
        "why",
        "does",
        "do",
        "when",
        "then",
        "than",
        "there",
        "here",
        "into",
        "out",
        "up",
        "get",
        "set",
        "use",
        "used",
        "uses",
        "using",
        "code",
        "file",
        "files",
        "function",
        "functions",
        "class",
        "method",
        "methods",
        "call",
        "calls",
        "called",
        "work",
        "works",
        "happen",
        "happens",
    ]
)


def words(text: str) -> list[str]:
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    found = re.findall(r"[a-z][a-z0-9]+", spaced.replace("_", " ").replace(".", " ").lower())
    return [stem(w) for w in found if w not in _STOP and len(w) > 2]


def stem(word: str) -> str:
    """A light stem, so promote/promoted, apply/applied and ship/shipped match; nothing finer is attempted."""
    if word.endswith(("ied", "ies")) and len(word) > 4:
        return word[:-3] + "y"
    for suffix in ("ing", "ed", "es", "s", "e"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            base = word[: -len(suffix)]
            if (
                suffix in ("ing", "ed")
                and len(base) > 3
                and base[-1] == base[-2]
                and base[-1] not in "aeiouls"
            ):
                base = base[:-1]  # shipped → ship, running → run
            return base if len(base) >= 3 else word
    return word


_TEST_PATH = re.compile(r"(^|/)(tests?|spec)/|(^|/)test_[^/]+$|_test\.py$")
PLANNER = "3"  # in the cache key: a change to how questions are planned must not serve old answers


def plan(question: str, graph: Any, *, limit: int = 8) -> list[dict[str, Any]]:
    """Candidate symbols for a question, best first, each with the words it matched and where."""
    want = set(words(question))
    if not want:
        return []
    asks_tests = bool({"test", "tests"} & set(re.findall(r"[a-z]+", question.lower())))
    scored = []
    for n in graph.nodes:
        if n.kind not in SYMBOL_KINDS:
            continue
        name_w = set(words(n.name))
        doc_w = set(words(str(n.metadata.get("doc") or "")))
        path_w = set(words(n.path))
        hits_name, hits_doc, hits_path = (
            want & name_w,
            (want & doc_w) - name_w,
            (want & path_w) - name_w - doc_w,
        )
        score = float(3 * len(hits_name) + len(hits_doc) + len(hits_path))
        if _TEST_PATH.search(n.path) and not asks_tests:  # a test is rarely where something is done
            score *= 0.5
        if hits_name or len(hits_doc | hits_path) >= 2:
            scored.append(
                {
                    "id": n.node_id,
                    "name": n.name,
                    "kind": n.kind,
                    "path": n.path,
                    "line": int(n.metadata.get("line") or 0),
                    "end_line": int(n.metadata.get("end_line") or n.metadata.get("line") or 0),
                    "score": score,
                    "matched": {
                        "name": sorted(hits_name),
                        "doc": sorted(hits_doc),
                        "path": sorted(hits_path),
                    },
                }
            )
    scored.sort(key=lambda c: (-c["score"], c["path"], c["line"]))
    return scored[:limit]


def _source(root: Path, path: str, start: int, end: int) -> str:
    try:
        lines = (root / path).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(lines[max(0, start - 1) : end])


def _merge(spans: list[tuple[str, int, int]]) -> list[tuple[str, int, int]]:
    """Overlapping or touching line ranges in one file become one passage."""
    out: list[tuple[str, int, int]] = []
    for path, a, b in sorted(spans):
        if out and out[-1][0] == path and a <= out[-1][2] + 1:
            out[-1] = (path, out[-1][1], max(out[-1][2], b))
        else:
            out.append((path, a, b))
    return out


def ask(root: Path, question: str, *, budget: int = 2000, seeds: int = 3) -> dict[str, Any]:
    root = root.resolve()
    state = source_state(root)
    key = (
        f"ask-v{PLANNER}-"
        + hashlib.sha256(f"{question}|{budget}|{seeds}".encode()).hexdigest()[:20]
        + ".json"
    )
    cached = _read_cache(root, key)
    if isinstance(cached, dict) and cached.get("source_state") == state:
        return {**cached["answer"], "cached": True}
    graph = fresh_graph(root)
    candidates = plan(question, graph)
    nodes = {n.node_id: n for n in graph.nodes}
    callers: dict[str, set[str]] = defaultdict(set)
    callees: dict[str, set[str]] = defaultdict(set)
    for e in graph.edges:
        if e.kind == "calls" and e.target in nodes:
            callers[e.target].add(e.source)
            callees[e.source].add(e.target)
    spans = [(c["path"], c["line"], c["end_line"]) for c in candidates[:seeds] if c["line"]]
    passages: list[dict[str, Any]] = []
    used, omitted = 0, 0
    for path, a, b in _merge(spans):
        text = _source(root, path, a, b)
        cost = estimate_tokens(text)
        if used + cost > budget and passages:
            omitted += 1
            continue
        passages.append({"path": path, "lines": [a, b], "text": text})
        used += cost
    neighbours, seen = [], {c["id"] for c in candidates[:seeds]}
    for c in candidates[:seeds]:
        for rel, ids in (("called by", callers[c["id"]]), ("calls", callees[c["id"]])):
            for other in sorted(ids - seen):
                n = nodes[other]
                line = f"{c['name']} {rel} {n.name} ({n.path}:{n.metadata.get('line')}) {n.metadata.get('signature') or ''}".strip()
                cost = estimate_tokens(line)
                if used + cost > budget:
                    omitted += 1
                    continue
                neighbours.append(line)
                used += cost
                seen.add(other)
    answer = {
        "schema": SCHEMA,
        "question": question,
        "plan": candidates,
        "passages": passages,
        "neighbours": neighbours,
        "tokens": used,
        "budget": budget,
        "diagnostics": {
            "candidates": len(candidates),
            "passages": len(passages),
            "omitted_for_budget": omitted,
            "merged_spans": len(spans) - len(_merge(spans)),
        },
        "cached": False,
    }
    _write_cache(root, key, {"source_state": state, "answer": answer})
    return answer


def baseline_tokens(root: Path, question: str) -> int:
    """The naive way: read every source file containing one of the question's words."""
    want = set(words(question))
    total = 0
    for p in root.rglob("*.py"):
        if any(
            part.startswith(".") or part in ("node_modules", "venv")
            for part in p.relative_to(root).parts
        ):
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        if want & set(words(text)):
            total += estimate_tokens(text)
    return total


def evaluate(root: Path, cases: list[dict[str, Any]], *, budget: int = 2000) -> dict[str, Any]:
    """Recall of the expected symbols and tokens against the naive baseline, per question and overall."""
    rows = []
    for case in cases:
        out = ask(root, case["question"], budget=budget)
        found = [c["name"].rsplit(".", 1)[-1] for c in out["plan"][:3]]
        expected = {e.rsplit(".", 1)[-1] for e in case.get("expect", [])}
        # a hit is the name itself, or the name as a whole word of a found one (gaps in workflow_gaps)
        hits = {e for e in expected if any(f == e or e in f.split("_") for f in found)}
        hit = len(hits)
        base = baseline_tokens(root, case["question"])
        rows.append(
            {
                "question": case["question"],
                "recall": round(hit / len(expected), 3) if expected else None,
                "missing": sorted(expected - hits),
                "tokens": out["tokens"],
                "baseline_tokens": base,
                "saving": round(1 - out["tokens"] / base, 3) if base else None,
            }
        )
    scored = [r for r in rows if r["recall"] is not None]
    spent, base = sum(r["tokens"] for r in rows), sum(r["baseline_tokens"] for r in rows)
    return {
        "questions": rows,
        "recall": round(sum(r["recall"] for r in scored) / len(scored), 3) if scored else None,
        "tokens": spent,
        "baseline_tokens": base,
        "saving": round(1 - spent / base, 3) if base else None,
    }


def to_text(answer: dict[str, Any]) -> str:
    out = [f"# {answer['question']}", ""]
    for c in answer["plan"][:5]:
        why = ", ".join(f"{k}: {' '.join(v)}" for k, v in c["matched"].items() if v)
        out.append(f"- {c['kind']} {c['name']} ({c['path']}:{c['line']}) matched {why}")
    for p in answer["passages"]:
        out += ["", f"## {p['path']}:{p['lines'][0]}-{p['lines'][1]}", "```", p["text"], "```"]
    if answer["neighbours"]:
        out += ["", "## Around it", ""] + [f"- {n}" for n in answer["neighbours"]]
    d = answer["diagnostics"]
    out += [
        "",
        f"{answer['tokens']} of {answer['budget']} tokens; {d['candidates']} candidates, {d['omitted_for_budget']} left out "
        f"for the budget{', from the cache' if answer.get('cached') else ''}.",
    ]
    return "\n".join(out) + "\n"


__all__ = ["SCHEMA", "ask", "baseline_tokens", "evaluate", "plan", "to_text", "words"]
