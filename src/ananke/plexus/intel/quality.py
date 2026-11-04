"""Complexity, size and documentation per function, with tests counted apart from production code.

Python is measured from its syntax tree. JavaScript and TypeScript are an estimate from lines and branch keywords, because no
parser for them is available without a dependency: each such hotspot says ``estimated: true``, and ``limits`` says so too.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from typing import Any

from ananke.plexus.intel._common import (
    SCRIPT_SUFFIXES,
    count_loc,
    is_fixture,
    is_test_file,
    language_of,
    read_text,
    some,
)
from ananke.plexus.intel.context import RepoContext

MAX_HOTSPOTS = 20
_MAX_SCRIPT_BYTES = 500_000
_MAX_SCRIPT_LINE = 5_000
_NOT_MEASURED = {"sql", "html", "css", "shell", "terraform", "powershell"}

# Every pattern here is run on one bounded line, and none nests a quantifier over overlapping characters.
_JS_NAME = r"[A-Za-z_$][\w$]*"
_JS_BRANCH = re.compile(r"\b(?:if|for|while|case|catch)\b|&&|\|\||\?\?|\?(?![.:])")
_JS_FUNCTION = re.compile(rf"\bfunction\b[ \t]*(?:\*[ \t]*)?({_JS_NAME})[ \t]*[(<]")
_JS_ARROW = re.compile(
    rf"\b(?:const|let|var)[ \t]+({_JS_NAME})[ \t]*(?::[^=]{{0,200}})?=[ \t]*(?:async\b[ \t]*)?(?:function\b|\(|{_JS_NAME}[ \t]*=>)"
)
_JS_METHOD = re.compile(
    rf"^[ \t]+(?:(?:public|private|protected|static|async|override|get|set)[ \t]+){{0,6}}({_JS_NAME})[ \t]*[(<][^;]{{0,400}}\{{[ \t]*$"
)
_JS_CLASS = re.compile(rf"\bclass[ \t]+({_JS_NAME})")
_JS_TEST = re.compile(r"^[ \t]*(?:it|test)(?:\.[a-z]+)?[ \t]*\(")
_JS_KEYWORDS = {"if", "for", "while", "switch", "catch", "function", "return"}


@dataclass
class _Function:
    symbol: str
    line: int
    complexity: int
    length: int
    documented: bool
    estimated: bool = False


@dataclass
class _FileMetrics:
    loc: int
    functions: list[_Function]
    classes: int
    classes_documented: int
    test_functions: int


def _branches(function: ast.AST) -> int:
    """Decisions in a function, not counting those in functions and classes defined inside it."""
    total = 0
    stack = list(ast.iter_child_nodes(function))
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if isinstance(
            node, (ast.If, ast.IfExp, ast.For, ast.AsyncFor, ast.While, ast.ExceptHandler)
        ):
            total += 1
        elif isinstance(node, ast.BoolOp):
            total += len(node.values) - 1
        elif isinstance(node, ast.comprehension):
            total += len(node.ifs)
        elif isinstance(node, ast.match_case):
            # ``case _:`` is the way out when no case matched, not one more decision.
            wildcard = isinstance(node.pattern, ast.MatchAs) and node.pattern.pattern is None
            total += (0 if wildcard and node.guard is None else 1) + (
                1 if node.guard is not None else 0
            )
        stack.extend(ast.iter_child_nodes(node))
    return total


def complexity_of(function: ast.AST) -> int:
    return 1 + _branches(function)


def _measure_python(path: str, source: str) -> _FileMetrics:
    tree = ast.parse(source)
    functions: list[_Function] = []
    classes = documented_classes = 0
    tests = 0
    stack: list[tuple[ast.AST, str, bool]] = [(tree, "", False)]
    while stack:
        node, prefix, in_class = stack.pop()
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qualified = f"{prefix}{child.name}"
                kind = "method" if in_class else "function"
                functions.append(
                    _Function(
                        symbol=f"{kind}:{path}:{qualified}",
                        line=child.lineno,
                        complexity=complexity_of(child),
                        length=(child.end_lineno or child.lineno) - child.lineno + 1,
                        documented=ast.get_docstring(child, clean=False) is not None,
                    )
                )
                if child.name.startswith("test"):
                    tests += 1
                stack.append((child, f"{qualified}.", False))
            elif isinstance(child, ast.ClassDef):
                classes += 1
                documented_classes += ast.get_docstring(child, clean=False) is not None
                stack.append((child, f"{prefix}{child.name}.", True))
            else:
                stack.append((child, prefix, in_class))
    return _FileMetrics(count_loc(source), functions, classes, documented_classes, tests)


def blank_strings_and_comments(text: str) -> str:
    """The code alone, with line breaks kept. One pass with no backtracking, whatever the input."""
    out: list[str] = []
    index, size = 0, len(text)
    while index < size:
        char = text[index]
        pair = text[index : index + 2]
        if pair == "//":
            end = text.find("\n", index)
            index = size if end < 0 else end
        elif pair == "/*":
            end = text.find("*/", index + 2)
            stop = size if end < 0 else end + 2
            out.append("\n" * text.count("\n", index, stop))
            index = stop
        elif char in "'\"`":
            start, index = index, index + 1
            while index < size and text[index] != char:
                if text[index] == "\n" and char != "`":
                    break
                index += 2 if text[index] == "\\" else 1
            index = min(index + (0 if index < size and text[index] == "\n" else 1), size)
            out.append(char + char)
            out.append("\n" * text.count("\n", start, index))
        else:
            out.append(char)
            index += 1
    return "".join(out)


def _measure_script(path: str, source: str) -> _FileMetrics:
    """An estimate: braces mark where functions end, and branch keywords count as decisions."""
    functions: list[_Function] = []
    classes = tests = depth = 0
    open_functions: list[dict[str, Any]] = []
    pending: dict[str, Any] | None = None
    class_stack: list[tuple[str, int]] = []
    pending_class: str | None = None

    def close(record: dict[str, Any], line: int) -> None:
        functions.append(
            _Function(
                symbol=record["symbol"],
                line=record["line"],
                complexity=1 + record["branches"],
                length=line - record["line"] + 1,
                documented=record["documented"],
                estimated=True,
            )
        )

    lines = source.splitlines()
    cleaned = blank_strings_and_comments(source).splitlines()
    for number, line in enumerate(cleaned, start=1):
        if _JS_TEST.match(line):
            tests += 1
        class_match = _JS_CLASS.search(line)
        if class_match:
            classes += 1
            pending_class = class_match.group(1)

        name = None
        is_method = False
        for pattern in (_JS_FUNCTION, _JS_ARROW):
            match = pattern.search(line)
            if match:
                name = match.group(1)
                break
        if name is None and class_stack and class_stack[-1][1] == depth:
            match = _JS_METHOD.match(line)
            if match and match.group(1) not in _JS_KEYWORDS:
                name, is_method = match.group(1), True
        if name is not None:
            symbol = (
                f"method:{path}:{class_stack[-1][0]}.{name}"
                if is_method
                else f"function:{path}:{name}"
            )
            previous = lines[number - 2].strip() if 1 < number <= len(lines) + 1 else ""
            pending = {
                "symbol": symbol,
                "line": number,
                "branches": 0,
                "documented": previous.endswith("*/"),
                "open_at": None,
            }

        branches = len(_JS_BRANCH.findall(line))
        if open_functions:
            open_functions[-1]["branches"] += branches
        elif pending is not None:
            pending["branches"] += branches

        for char in line:
            if char == "{":
                if pending_class is not None:
                    class_stack.append((pending_class, depth + 1))
                    pending_class = None
                elif pending is not None:
                    pending["open_at"] = depth
                    open_functions.append(pending)
                    pending = None
                depth += 1
            elif char == "}":
                depth = max(depth - 1, 0)
                while open_functions and open_functions[-1]["open_at"] >= depth:
                    close(open_functions.pop(), number)
                while class_stack and class_stack[-1][1] > depth:
                    class_stack.pop()
        if pending is not None and "=>" in line:
            close(pending, number)  # an arrow function with no braces ends on its line
            pending = None
    for record in open_functions:
        close(record, len(cleaned))
    return _FileMetrics(count_loc(source), functions, classes, 0, tests)


def _too_long_a_line(source: str) -> bool:
    return any(len(line) > _MAX_SCRIPT_LINE for line in source.splitlines())


def analyze(ctx: RepoContext) -> dict[str, Any]:
    limits: list[str] = []
    functions: list[tuple[str, _Function]] = []
    files: list[dict[str, Any]] = []
    loc = classes = documented_classes = test_files = test_functions = 0
    malformed: list[str] = []
    too_big: list[str] = []
    unread: list[str] = []
    other: dict[str, int] = {}
    notebooks = 0
    scripts = 0

    separate = (ctx.sections.get("overview") or {}).get("separate_projects", [])
    skipped = 0
    for path in ctx.files:
        language = language_of(path)
        if language is None or is_fixture(path) or path.endswith(".pyi"):
            continue
        if path.split("/", 1)[0] in separate:
            skipped += 1
            continue
        testing = is_test_file(path)
        test_files += testing
        if path.endswith(".ipynb"):
            notebooks += 1
            continue
        python = path.endswith((".py", ".pyi"))
        script = path.endswith(SCRIPT_SUFFIXES)
        if not python and not script:
            if language not in _NOT_MEASURED and not testing:
                other[language] = other.get(language, 0) + 1
            continue
        text = read_text(ctx, path, _MAX_SCRIPT_BYTES if script else 2_000_000)
        if text is None:
            unread.append(path)
            continue
        if script and _too_long_a_line(text):
            too_big.append(path)
            continue
        try:
            metrics = _measure_python(path, text) if python else _measure_script(path, text)
        except (SyntaxError, ValueError, RecursionError, MemoryError):
            malformed.append(path)
            continue
        if testing:
            test_functions += metrics.test_functions
            continue
        scripts += script
        loc += metrics.loc
        classes += metrics.classes
        documented_classes += metrics.classes_documented
        functions += [(path, f) for f in metrics.functions]
        files.append(
            {
                "path": path,
                "loc": metrics.loc,
                "functions": len(metrics.functions),
                "max_complexity": max((f.complexity for f in metrics.functions), default=0),
            }
        )

    if skipped:
        limits.append(
            f"source files in folders of separate projects aren't measured ({skipped}): {some(list(separate))}"
        )
    if malformed:
        limits.append(
            f"files that couldn't be parsed ({len(malformed)}): {some(sorted(malformed))}"
        )
    if unread:
        limits.append(f"files too large to read ({len(unread)}): {some(sorted(unread))}")
    if too_big:
        limits.append(
            f"files that look minified, and weren't measured ({len(too_big)}): {some(sorted(too_big))}"
        )
    if scripts:
        limits.append(
            "JavaScript and TypeScript figures are an estimate from lines and branch keywords"
        )
    if notebooks:
        limits.append(f"notebooks aren't measured ({notebooks})")
    if other:
        named = ", ".join(f"{count} {language}" for language, count in sorted(other.items()))
        limits.append(f"these source files aren't measured: {named}")

    if not files and not test_files:
        return {
            "status": "none",
            "limits": limits,
            "totals": _totals(0, 0, [], 0, 0, 0, 0),
            "hotspots": [],
            "files": [],
        }

    ranked = sorted(
        functions, key=lambda item: (-item[1].complexity, -item[1].length, item[1].symbol)
    )
    hotspots: list[dict[str, Any]] = []
    for path, function in ranked:
        if function.complexity < 2 or len(hotspots) >= MAX_HOTSPOTS:
            break
        entry = {
            "symbol": function.symbol,
            "path": path,
            "line": function.line,
            "complexity": function.complexity,
            "length": function.length,
        }
        if function.estimated:
            entry["estimated"] = True
        hotspots.append(entry)
    return {
        "status": "found",
        "limits": limits,
        "totals": _totals(
            len(files),
            loc,
            [f for _, f in functions],
            classes,
            documented_classes,
            test_files,
            test_functions,
        ),
        "hotspots": hotspots,
        "files": sorted(files, key=lambda f: f["path"]),
    }


def _totals(
    files: int,
    loc: int,
    functions: list[_Function],
    classes: int,
    documented_classes: int,
    test_files: int,
    test_functions: int,
) -> dict[str, Any]:
    documented = sum(f.documented for f in functions) + documented_classes
    documentable = len(functions) + classes
    return {
        "files": files,
        "loc": loc,
        "functions": len(functions),
        "classes": classes,
        "avg_complexity": round(sum(f.complexity for f in functions) / len(functions), 2)
        if functions
        else None,
        "max_complexity": max((f.complexity for f in functions), default=None),
        "documented_ratio": round(documented / documentable, 4) if documentable else None,
        "test_files": test_files,
        "test_functions": test_functions,
    }
