"""Notebook reproducibility inspector (ADV17): would this notebook give the same results if run top to bottom?

Read from the ``.ipynb`` file alone, nothing executed:

* **Execution history:** execution counts out of cell order, code cells never run, and gaps in the counts (cells run
  and then deleted or re-run, so hidden state may have fed the outputs).
* **Lineage:** names a cell uses that no earlier cell defines (they only worked because of hidden state, or because a
  later cell was run first); each with the cell that defines it, if any.
* **Outputs:** cells whose saved output is an error, and outputs saved without an execution count.
* **Environment:** the kernel and language version recorded, imports not declared in the project's requirements,
  and files read by relative path that aren't in the project.

Each finding names the cell (its index and first line) and is **observed** (from the saved file) or a **hypothesis**
(hidden state is a likely cause, not a proven one). A notebook that passes every check is labelled
**likely reproducible**, never proven: only running it shows that.
"""

from __future__ import annotations

import ast
import builtins
import itertools
import json
import re
import sys
from pathlib import Path
from typing import Any

from ananke.plexus.insight import InsightError, digest

SCHEMA = "ananke/notebook-repro@1"
MAX_NOTEBOOK_BYTES = 50_000_000
_BUILTINS = set(dir(builtins)) | {
    "display",
    "get_ipython",
    "In",
    "Out",
    "_",
    "__",
    "___",
    "exit",
    "quit",
}
_MAGIC = re.compile(r"^\s*[%!]")
_READ_CALL = re.compile(
    r"""\b(?:read_csv|read_parquet|read_json|read_excel|read_table|open|load|loadtxt|read_feather|imread)\(\s*[rbf]?(['"])([^'"\n]+)\1"""
)
_STDLIB = set(getattr(sys, "stdlib_module_names", ())) | {"__future__"}
_IMPORT_NAMES = {  # import name -> distribution name, for the common ones that differ
    "sklearn": "scikit-learn",
    "cv2": "opencv-python",
    "PIL": "pillow",
    "yaml": "pyyaml",
    "bs4": "beautifulsoup4",
    "dotenv": "python-dotenv",
    "skimage": "scikit-image",
    "google": "google",
    "dateutil": "python-dateutil",
}


def _source(cell: dict[str, Any]) -> str:
    src = cell.get("source") or ""
    return "".join(src) if isinstance(src, list) else str(src)


def _python(src: str) -> str:
    """Cell source with IPython magics and shell lines blanked, so it parses as Python."""
    out = []
    for line in src.splitlines():
        out.append("" if _MAGIC.match(line) else line)
    text = "\n".join(out)
    if text.lstrip().startswith("%%"):
        return ""
    return text


def _names(src: str) -> tuple[set[str], set[str], set[str]]:
    """Names a cell defines, names it reads before defining them itself, and modules it imports."""
    try:
        tree = ast.parse(_python(src))
    except SyntaxError:
        return set(), set(), set()
    defined: set[str] = set()
    used: set[str] = set()
    imports: set[str] = set()
    local_scopes: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            args = node.args
            for a in [*args.posonlyargs, *args.args, *args.kwonlyargs, args.vararg, args.kwarg]:
                if a is not None:
                    local_scopes.add(a.arg)
        if isinstance(node, (ast.comprehension,)):
            for t in ast.walk(node.target):
                if isinstance(t, ast.Name):
                    local_scopes.add(t.id)
    # top-level order matters: a name used before it's assigned in this cell is a read
    for stmt in tree.body:
        for node in ast.walk(stmt):
            if (
                isinstance(node, ast.Name)
                and isinstance(node.ctx, ast.Load)
                and node.id not in defined
            ):
                used.add(node.id)
        for node in ast.walk(stmt):
            if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
                defined.add(node.id)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                defined.add(node.name)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    defined.add((alias.asname or alias.name).split(".")[0])
                    imports.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module and node.level == 0:
                    imports.add(node.module.split(".")[0])
                for alias in node.names:
                    if alias.name != "*":
                        defined.add(alias.asname or alias.name)
            elif isinstance(node, ast.ExceptHandler) and node.name:
                defined.add(node.name)
            elif isinstance(node, (ast.With, ast.AsyncWith)):
                for item in node.items:
                    if item.optional_vars is not None:
                        for t in ast.walk(item.optional_vars):
                            if isinstance(t, ast.Name):
                                defined.add(t.id)
    return defined, used - local_scopes - _BUILTINS, imports


def _folders(path: Path, root: Path) -> list[Path]:
    """The notebook's folder and each parent up to the project root: where its requirements can be declared."""
    out = []
    folder = path.parent.resolve()
    root = root.resolve()
    while True:
        out.append(folder)
        if folder == root or root not in folder.parents:
            break
        folder = folder.parent
    return out


def _declared(path: Path, root: Path) -> set[str] | None:
    """Distribution names declared next to the notebook or above it, or ``None`` when nothing is declared there."""
    names: set[str] = set()
    found = False
    for req in [r for folder in _folders(path, root) for r in folder.glob("requirements*.txt")]:
        found = True
        for line in req.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.split("#")[0].strip()
            m = re.match(r"^([A-Za-z0-9_.\-]+)", line)
            if m and not line.startswith("-"):
                names.add(m.group(1).lower().replace("_", "-"))
    for pyproject in [
        f / "pyproject.toml" for f in _folders(path, root) if (f / "pyproject.toml").is_file()
    ]:
        import tomllib

        try:
            data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        except (tomllib.TOMLDecodeError, UnicodeDecodeError):
            data = {}
        deps = list((data.get("project") or {}).get("dependencies") or [])
        for extra in ((data.get("project") or {}).get("optional-dependencies") or {}).values():
            deps += list(extra)
        for dep in deps:
            m = re.match(r"^([A-Za-z0-9_.\-]+)", str(dep))
            if m:
                found = True
                names.add(m.group(1).lower().replace("_", "-"))
    return names if found else None


def _installed_in_notebook(cells: list[dict[str, Any]]) -> set[str]:
    """Packages a notebook installs itself (``%pip install x``, ``!pip install x``, ``!uv pip install x``)."""
    names: set[str] = set()
    for cell in cells:
        for line in _source(cell).splitlines():
            m = re.match(r"^\s*[%!]\s*(?:uv\s+)?pip\s+install\s+(.*)$", line)
            if m:
                for tok in m.group(1).split():
                    if not tok.startswith("-"):
                        base = re.match(r"^([A-Za-z0-9_.\-]+)", tok.strip("'\""))
                        if base:
                            names.add(base.group(1).lower().replace("_", "-"))
    return names


def inspect(path: Path, root: Path) -> dict[str, Any]:
    if path.suffix != ".ipynb":
        raise InsightError(f"{path.name} isn't a notebook (.ipynb)")
    if path.stat().st_size > MAX_NOTEBOOK_BYTES:
        raise InsightError(f"{path.name} is over {MAX_NOTEBOOK_BYTES // 1_000_000} MB")
    try:
        nb = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise InsightError(f"{path.name} isn't valid notebook JSON: {exc}") from exc
    if not isinstance(nb, dict) or not isinstance(nb.get("cells"), list):
        raise InsightError(f"{path.name} has no cells list; it isn't an nbformat 4 notebook")
    rel = _rel(path, root)
    cells = [c for c in nb["cells"] if isinstance(c, dict)]
    findings: list[dict[str, Any]] = []

    def cell_ref(i: int) -> str:
        first = next((ln.strip() for ln in _source(cells[i]).splitlines() if ln.strip()), "")
        return f"cell {i + 1}: {first[:70]}"

    def add(
        rule: str, severity: str, i: int | None, message: str, status: str = "observed"
    ) -> None:
        findings.append(
            {
                "rule": rule,
                "severity": severity,
                "cell": i + 1 if i is not None else None,
                "where": cell_ref(i) if i is not None else rel,
                "message": message,
                "status": status,
            }
        )

    code = [i for i, c in enumerate(cells) if c.get("cell_type") == "code" and _source(c).strip()]
    counts = [(i, cells[i].get("execution_count")) for i in code]
    ran = [(i, n) for i, n in counts if isinstance(n, int)]
    never = [i for i, n in counts if not isinstance(n, int)]
    if ran:
        for (_, a), (j, b) in itertools.pairwise(ran):
            if b < a:
                add("out-of-order", "high", j, f"ran as [{b}] after a cell above ran as [{a}]")
        nums = sorted(n for _, n in ran)
        if nums[0] != 1 or nums[-1] - nums[0] + 1 != len(nums):
            missing = nums[-1] - len(nums) - (nums[0] - 1)
            add(
                "hidden-runs",
                "medium",
                None,
                f"execution counts go from [{nums[0]}] to [{nums[-1]}] over {len(nums)} cells: {max(missing, 0) + nums[0] - 1} "
                "runs aren't in the saved cells (re-runs or deleted cells), so hidden state may have fed the outputs",
                "hypothesis",
            )
    if never and ran:
        listed = ", ".join(str(i + 1) for i in never[:15]) + (
            f" and {len(never) - 15} more" if len(never) > 15 else ""
        )
        add(
            "never-run",
            "medium",
            never[0],
            f"{len(never)} code cells have no execution count (cells {listed}): they "
            "weren't run for the saved outputs",
        )
    defined_at: dict[str, int] = {}
    per_cell = []
    all_imports: set[str] = set()
    for i in code:
        defined, used, imports = _names(_source(cells[i]))
        per_cell.append((i, defined, used))
        all_imports |= imports
    first_def: dict[str, int] = {}
    for i, defined, _ in per_cell:
        for name in defined:
            first_def.setdefault(name, i)
    undefined: dict[str, list[int]] = {}
    for i, defined, used in per_cell:
        for name in sorted(used):
            where = first_def.get(name)
            if where is None:
                undefined.setdefault(name, []).append(i)
            elif where > i:
                add(
                    "used-before-defined",
                    "high",
                    i,
                    f"`{name}` is used here but first defined in cell {where + 1}, further down",
                    "observed",
                )
        for name in defined:
            defined_at.setdefault(name, i)
    for name, where_used in sorted(undefined.items(), key=lambda kv: (kv[1][0], kv[0])):
        more = f" (and {len(where_used) - 1} later cells)" if len(where_used) > 1 else ""
        add(
            "undefined-name",
            "high",
            where_used[0],
            f"`{name}` is used{more} but no cell defines it: it must come "
            "from outside the notebook or from deleted cells",
            "hypothesis",
        )
    for i in code:
        for out in cells[i].get("outputs") or []:
            if isinstance(out, dict) and out.get("output_type") == "error":
                add(
                    "error-output",
                    "high",
                    i,
                    f"the saved output is an error: {out.get('ename')}: {str(out.get('evalue'))[:120]}",
                )
            if (
                isinstance(out, dict)
                and out.get("output_type") == "execute_result"
                and not isinstance(cells[i].get("execution_count"), int)
            ):
                add(
                    "orphan-output",
                    "low",
                    i,
                    "an output is saved but the cell has no execution count",
                )
    meta = nb.get("metadata") or {}
    kernel = (meta.get("kernelspec") or {}).get("name") or ""
    version = (meta.get("language_info") or {}).get("version") or ""
    if not version:
        add("no-environment", "low", None, "the notebook records no language version", "observed")
    declared = _declared(path, root)
    installed = _installed_in_notebook(cells)
    third_party = sorted(
        m
        for m in all_imports
        if m not in _STDLIB and not (root / m).exists() and not (root / f"{m}.py").exists()
    )
    if declared is not None:
        for module in third_party:
            dist = _IMPORT_NAMES.get(module, module).lower().replace("_", "-")
            if (
                dist not in declared
                and dist not in installed
                and module.lower() not in declared | installed
            ):
                add(
                    "undeclared-import",
                    "medium",
                    None,
                    f"imports `{module}`, which no requirements file or "
                    "pyproject declares and the notebook doesn't install",
                )
    elif third_party and not installed:
        add(
            "no-requirements",
            "medium",
            None,
            "no requirements are declared next to the notebook or above it, and "
            "it imports "
            f"{', '.join(third_party[:8])} without installing them",
        )
    base = path.parent
    for i in code:
        for m in _READ_CALL.finditer(_python(_source(cells[i]))):
            target = m.group(2)
            if "://" in target or target.startswith(("/", "~")) or "{" in target:
                continue
            if not (base / target).exists() and not (root / target).exists():
                writers = [j for j in code if target in _source(cells[j]) and j != i and j < i]
                if not writers:
                    add(
                        "missing-input",
                        "medium",
                        i,
                        f"reads `{target}`, which isn't in the project and no earlier "
                        "cell writes it",
                        "hypothesis",
                    )
    high = sum(1 for f in findings if f["severity"] == "high")
    verdict = (
        "likely reproducible"
        if not findings or (not high and len(findings) <= 1)
        else ("not reproducible as saved" if high else "reproducible with care")
    )
    return {
        "notebook": rel,
        "cells": len(cells),
        "code_cells": len(code),
        "ran": len(ran),
        "kernel": kernel,
        "language_version": version,
        "verdict": verdict,
        "findings": findings,
    }


def _rel(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.name


def inspect_all(root: Path, paths: list[Path] | None = None) -> dict[str, Any]:
    root = root.resolve()
    if paths:
        notebooks = [p.resolve() for p in paths]
        for p in notebooks:
            if not p.is_file():
                raise InsightError(f"{p} doesn't exist")
    else:
        notebooks = sorted(
            p
            for p in root.rglob("*.ipynb")
            if not any(
                part.startswith(".") or part in {"node_modules", "site-packages", "venv"}
                for part in p.relative_to(root).parts
            )
        )
    if not notebooks:
        raise InsightError(f"no notebooks under {root}; pass one with --notebook")
    results = [inspect(p, root) for p in notebooks]
    report: dict[str, Any] = {
        "schema": SCHEMA,
        "summary": {
            "notebooks": len(results),
            "likely_reproducible": sum(1 for r in results if r["verdict"] == "likely reproducible"),
            "with_care": sum(1 for r in results if r["verdict"] == "reproducible with care"),
            "not_reproducible": sum(
                1 for r in results if r["verdict"] == "not reproducible as saved"
            ),
            "findings": sum(len(r["findings"]) for r in results),
        },
        "notebooks": results,
        "limits": [
            "Read from the saved file; nothing ran. 'Likely reproducible' isn't proof: run the notebook top to bottom "
            "in a fresh kernel to confirm.",
            "Names created dynamically (exec, globals(), star imports) aren't tracked.",
            "Hidden state is a hypothesis drawn from execution counts, not observed.",
        ],
    }
    report["digest"] = digest(report)
    return report


def to_markdown(report: dict[str, Any]) -> str:
    s = report["summary"]
    lines = [
        "# Notebook reproducibility",
        "",
        f"{s['notebooks']} notebooks: {s['likely_reproducible']} likely reproducible, {s['with_care']} with care, "
        f"{s['not_reproducible']} not reproducible as saved.",
        "",
    ]
    for nb in report["notebooks"]:
        lines += [
            f"## {nb['notebook']}: {nb['verdict']}",
            "",
            f"{nb['code_cells']} code cells, {nb['ran']} ran; kernel {nb['kernel'] or 'unknown'}, "
            f"version {nb['language_version'] or 'unknown'}.",
            "",
        ]
        for f in nb["findings"]:
            lines.append(
                f"- **{f['severity']}** {f['rule']} ({f['where']}): {f['message']} [{f['status']}]"
            )
        lines.append("")
    lines += [
        "## Limits",
        "",
        *(f"- {x}" for x in report["limits"]),
        "",
        f"Digest: `{report['digest']}`",
        "",
    ]
    return "\n".join(lines)
