"""From a working session to a lesson worth reviewing (three-repo plan: CAP-session-learning-01 and -03).

``draft`` gathers what a session left behind (the files it changed, the decisions written in its commit messages,
the tests that failed and the warnings its Ananke runs recorded) into a Markdown lesson for a person to finish. It
holds names and messages only, never file contents, and capture still redacts secrets and paths
(``ananke learning capture``).

``overlap`` asks Dex Trove whether the lesson already exists: it searches the catalogue for the lesson's main words
and scores each result by the words they share. Dex is optional: without it the answer is "not checked", never
"nothing similar".
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any

_STOP = set(
    [
        "the",
        "and",
        "for",
        "with",
        "that",
        "this",
        "from",
        "into",
        "when",
        "then",
        "than",
        "are",
        "was",
        "were",
        "has",
        "have",
        "had",
        "not",
        "but",
        "can",
        "use",
        "used",
        "using",
        "its",
        "it's",
        "our",
        "your",
        "their",
        "what",
        "which",
        "who",
        "how",
        "why",
        "all",
        "any",
        "each",
        "one",
        "two",
        "new",
        "old",
        "add",
        "added",
        "adds",
        "fix",
        "fixed",
        "fixes",
        "make",
        "made",
        "makes",
        "run",
        "runs",
        "ran",
        "test",
        "tests",
        "should",
        "would",
        "could",
        "will",
        "just",
        "only",
        "also",
        "more",
        "most",
        "file",
        "files",
    ]
)


def _git(root: Path, *args: str) -> str:
    argv = ["git", "-C", str(root), *args]
    done = subprocess.run(argv, capture_output=True, text=True, timeout=60, check=False)  # noqa: S603
    if done.returncode != 0:
        raise ValueError(f"git {args[0]} failed: {done.stderr.strip()[:200]}")
    return done.stdout


def draft(
    root: Path, base: str, *, runs: list[dict[str, Any]] | None = None, title: str = ""
) -> dict[str, Any]:
    """What the session since ``base`` changed, decided, broke and ran, as a lesson to finish by hand."""
    root = root.resolve()
    _git(root, "rev-parse", "--verify", "--quiet", f"{base}^{{commit}}")
    files = sorted(
        set(_git(root, "diff", "--name-only", f"{base}...HEAD").split())
        | set(_git(root, "diff", "--name-only", "HEAD").split())
    )
    log = _git(root, "log", "--no-merges", "--format=%h%x1f%s%x1f%b%x1e", f"{base}..HEAD")
    commits, decisions = [], []
    for entry in log.split("\x1e"):
        if not entry.strip():
            continue
        sha, subject, body = [*entry.strip().split("\x1f"), "", ""][:3]
        commits.append({"sha": sha, "subject": subject})
        for raw in body.splitlines():
            said = raw.strip(" -*")
            if len(said) > 20 and not said.lower().startswith(("co-authored-by", "signed-off-by")):
                decisions.append(f"{said} ({sha})")
    from ananke.plexus import intel

    tests = ((intel.analyze(root).get("sections") or {}).get("findings") or {}).get("tests") or []
    failed = [
        f"{f['name']}: {f.get('message', '')}".rstrip(": ")
        for t in tests
        for f in t.get("failures") or []
    ]
    warnings = sorted(
        {
            f"{w.get('code')}: {w.get('message')}"
            for r in runs or []
            for w in r.get("warnings") or []
        }
    )
    name = title or (commits[0]["subject"] if commits else "What this session taught")
    lesson = [
        f"# {name}",
        "",
        "## When this applies",
        "",
        "- (one sentence: the situation in which someone should use this lesson)",
        "",
        "## What happened",
        "",
        *([f"- {c['subject']} ({c['sha']})" for c in commits] or ["- (uncommitted work only)"]),
        "",
        "## Decisions",
        "",
        *(
            [f"- {d}" for d in decisions[:20]]
            or ["- (none written in the commit messages: add why the approach was chosen)"]
        ),
        "",
        "## What failed on the way",
        "",
        *(
            [f"- {f}" for f in failed[:20]] + [f"- run warning: {w}" for w in warnings[:10]]
            or ["- nothing recorded"]
        ),
        "",
        "## Technique",
        "",
        "- (the steps that worked, written so they apply beyond this project)",
        "",
        "## Files involved",
        "",
        *[f"- `{f}`" for f in files[:40]],
        "",
    ]
    return {
        "title": name,
        "markdown": "\n".join(lesson),
        "files": files,
        "commits": commits,
        "decisions": decisions,
        "failed_tests": failed,
        "run_warnings": warnings,
        "next": "finish the lesson, then: ananke learning capture NAME --description ... --body LESSON.md --case ...",
    }


def _words(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z][a-z0-9_-]{2,}", text.lower()) if w not in _STOP]


def overlap(text: str, *, limit: int = 20, threshold: float = 0.12) -> dict[str, Any]:
    """Dex items that look like the same lesson, scored by shared words. Without Dex, says it couldn't check."""
    dex = shutil.which("dex")
    if dex is None:
        return {
            "checked": False,
            "reason": "Dex Trove isn't installed (pip install 'ananke-plexus[dex]')",
            "similar": [],
        }
    words = _words(re.sub(r"\(.*?\)|`[^`]*`", " ", text))
    if not words:
        return {"checked": True, "query": [], "similar": []}
    query = [w for w, _ in Counter(words).most_common(6)]
    done = subprocess.run(  # noqa: S603 - the dex CLI on PATH with fixed arguments and the lesson's own words
        [dex, "search", *query, "--json", "--limit", str(limit)],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    try:
        items = json.loads(done.stdout or "[]")
    except ValueError:
        return {
            "checked": False,
            "reason": f"dex search failed: {done.stderr.strip()[:200]}",
            "similar": [],
        }
    mine = set(words)
    similar = []
    for item in items if isinstance(items, list) else []:
        theirs = set(
            _words(" ".join(str(item.get(k) or "") for k in ("title", "description", "id")))
        )
        if not theirs:
            continue
        shared = mine & theirs
        score = len(shared) / len(mine | theirs)
        if score >= threshold:
            similar.append(
                {
                    "id": item.get("id"),
                    "title": item.get("title"),
                    "score": round(score, 3),
                    "shared": sorted(shared)[:12],
                }
            )
    similar.sort(key=lambda s: (-s["score"], str(s["id"])))
    return {"checked": True, "query": query, "similar": similar[:10]}


__all__ = ["draft", "overlap"]
