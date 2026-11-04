"""Coverage from reports that already exist. Tests never run here, and no report is ``not_found``, never 0%.

Reads Cobertura XML, coverage.py JSON, lcov and Istanbul. XML is untrusted: a document with a DOCTYPE or an entity declaration is
refused before it reaches a parser, which rules out external entities and entity expansion attacks.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ananke.plexus.intel._common import is_fixture, is_inside, read_bytes, some
from ananke.plexus.intel.context import RepoContext

_REPORT_NAMES = (
    # In order of preference when one folder has several reports of the same run.
    "coverage.xml", "cobertura.xml", "cobertura-coverage.xml", "coverage.json", "coverage-final.json", "lcov.info",
    "coverage-summary.json",
)  # fmt: skip
_REPORT_FOLDERS = ("", "coverage/", "reports/", "test-results/")
_MAX_REPORT_BYTES = 128_000_000
_HOW_TO = (
    "No coverage report found. Produce one with `pytest --cov --cov-report=xml` (coverage.xml), or "
    "`vitest run --coverage` or `jest --coverage` (coverage/lcov.info), then analyse again."
)


class _BadReport(ValueError):
    """A report that can't be used, with the reason as its message."""


@dataclass
class _Parsed:
    line_rate: float | None = None
    branch_rate: float | None = None
    # raw path to (covered, total)
    files: dict[str, tuple[int, int]] = field(default_factory=dict)
    sources: list[str] = field(default_factory=list)
    branches: tuple[int, int] | None = None


def _safe_xml(raw: bytes) -> ET.Element:
    """Parses a report that's strict UTF-8 with no DOCTYPE and no entities.

    The check has to see what the parser sees, so the document's own encoding declaration is overridden. Without that, UTF-16
    passes for valid UTF-8 with NUL bytes in it, hides its DOCTYPE from a text search, and is still parsed by its declaration.
    """
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise _BadReport("not UTF-8") from exc
    if "\x00" in text:
        raise _BadReport("not UTF-8")
    lowered = text.lower()
    if "<!doctype" in lowered or "<!entity" in lowered:
        raise _BadReport("a DOCTYPE or entity declaration, refused as unsafe")
    parser = ET.XMLParser(encoding="utf-8")  # noqa: S314 - checked above
    try:
        return ET.fromstring(text.encode(), parser=parser)  # noqa: S314 - checked above
    except ET.ParseError as exc:
        raise _BadReport("malformed XML") from exc


def _rate(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        rate = float(str(value))
    except ValueError:
        return None
    return rate if 0.0 <= rate <= 1.0 else None


def _whole(value: object) -> int | None:
    """A count from a report: a non-negative whole number, or nothing."""
    if isinstance(value, bool):
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _ratio(covered: int, total: int) -> float | None:
    return round(covered / total, 4) if total > 0 else None


def _parse_cobertura(raw: bytes) -> _Parsed:
    root = _safe_xml(raw)
    if root.tag != "coverage":
        raise _BadReport("not a Cobertura report")
    parsed = _Parsed(
        line_rate=_rate(root.get("line-rate")), branch_rate=_rate(root.get("branch-rate"))
    )
    # coverage.py writes branch-rate="0" when branches weren't measured: that's unknown, not zero.
    if root.get("branches-valid") == "0":
        parsed.branch_rate = None
    parsed.sources = [s.text.strip() for s in root.iter("source") if s.text and s.text.strip()]
    seen: dict[str, dict[int, bool]] = {}
    fallback: dict[str, float] = {}
    for item in root.iter("class"):
        name = item.get("filename")
        if not name:
            continue
        lines = seen.setdefault(name, {})
        for line in item.iter("line"):
            number, hits = _whole(line.get("number")), _whole(line.get("hits"))
            if number is not None and hits is not None:
                lines[number] = lines.get(number, False) or hits > 0
        rate = _rate(item.get("line-rate"))
        if rate is not None:
            fallback[name] = rate
    for name, lines in seen.items():
        if lines:
            parsed.files[name] = (sum(lines.values()), len(lines))
    if parsed.line_rate is None:
        covered = sum(c for c, _ in parsed.files.values())
        total = sum(t for _, t in parsed.files.values())
        parsed.line_rate = _ratio(covered, total)
    return parsed


def _parse_coverage_json(data: dict[str, Any]) -> _Parsed:
    parsed = _Parsed()
    files = data["files"]
    if not isinstance(files, dict):
        raise _BadReport("not a coverage.py report")
    for name, entry in files.items():
        summary = entry.get("summary") if isinstance(entry, dict) else None
        if not isinstance(summary, dict):
            continue
        covered, total = _whole(summary.get("covered_lines")), _whole(summary.get("num_statements"))
        if covered is not None and total is not None:
            parsed.files[name] = (covered, total)
    totals = data.get("totals")
    totals = totals if isinstance(totals, dict) else {}
    covered, total = _whole(totals.get("covered_lines")), _whole(totals.get("num_statements"))
    if covered is None or total is None:
        covered = sum(c for c, _ in parsed.files.values())
        total = sum(t for _, t in parsed.files.values())
    parsed.line_rate = _ratio(covered, total)
    hit, branches = _whole(totals.get("covered_branches")), _whole(totals.get("num_branches"))
    if hit is not None and branches:
        parsed.branch_rate = _ratio(hit, branches)
        parsed.branches = (hit, branches)
    return parsed


def _parse_istanbul_final(data: dict[str, Any]) -> _Parsed:
    parsed = _Parsed()
    hit_branches = all_branches = 0
    for name, entry in data.items():
        if not isinstance(entry, dict) or not isinstance(entry.get("statementMap"), dict):
            continue
        raw_hits = entry.get("s")
        hits: dict[str, Any] = raw_hits if isinstance(raw_hits, dict) else {}
        lines: dict[int, bool] = {}
        for key, span in entry["statementMap"].items():
            start = span.get("start") if isinstance(span, dict) else None
            line = _whole(start.get("line")) if isinstance(start, dict) else None
            count = _whole(hits.get(key))
            if line is not None:
                lines[line] = lines.get(line, False) or bool(count)
        declared_path = entry.get("path")
        path = declared_path if isinstance(declared_path, str) else name
        parsed.files[path] = (sum(lines.values()), len(lines))
        branch_counts = entry.get("b")
        for counts in branch_counts.values() if isinstance(branch_counts, dict) else ():
            for count in counts if isinstance(counts, list) else ():
                all_branches += 1
                hit_branches += 1 if _whole(count) else 0
    covered = sum(c for c, _ in parsed.files.values())
    total = sum(t for _, t in parsed.files.values())
    parsed.line_rate = _ratio(covered, total)
    if all_branches:
        parsed.branch_rate = _ratio(hit_branches, all_branches)
        parsed.branches = (hit_branches, all_branches)
    return parsed


def _parse_istanbul_summary(data: dict[str, Any]) -> _Parsed:
    parsed = _Parsed()
    for name, entry in data.items():
        lines = entry.get("lines") if isinstance(entry, dict) else None
        if not isinstance(lines, dict):
            continue
        covered, total = _whole(lines.get("covered")), _whole(lines.get("total"))
        if covered is None or total is None:
            continue
        if name == "total":
            parsed.line_rate = _ratio(covered, total)
            branches = entry.get("branches")
            if isinstance(branches, dict):
                hit, valid = _whole(branches.get("covered")), _whole(branches.get("total"))
                if hit is not None and valid:
                    parsed.branch_rate = _ratio(hit, valid)
                    parsed.branches = (hit, valid)
        else:
            parsed.files[name] = (covered, total)
    return parsed


def _parse_lcov(raw: bytes) -> _Parsed:
    parsed = _Parsed()
    current: str | None = None
    lines: dict[int, bool] = {}
    hit_branches = all_branches = 0

    def finish() -> None:
        if current is not None:
            old = parsed.files.get(current, (0, 0))
            parsed.files[current] = (old[0] + sum(lines.values()), old[1] + len(lines))

    for row in raw.decode("utf-8", "replace").splitlines():
        if row.startswith("SF:"):
            finish()
            current, lines = row[3:].strip(), {}
        elif row.startswith("DA:") and current is not None:
            fields = row[3:].split(",")
            number = _whole(fields[0])
            hits = _whole(fields[1]) if len(fields) > 1 else None
            if number is not None and hits is not None:
                lines[number] = lines.get(number, False) or hits > 0
        elif row.startswith("BRF:"):
            all_branches += _whole(row[4:].strip()) or 0
        elif row.startswith("BRH:"):
            hit_branches += _whole(row[4:].strip()) or 0
        elif row.startswith("end_of_record"):
            finish()
            current, lines = None, {}
    finish()
    covered = sum(c for c, _ in parsed.files.values())
    total = sum(t for _, t in parsed.files.values())
    parsed.line_rate = _ratio(covered, total)
    if all_branches:
        parsed.branch_rate = _ratio(hit_branches, all_branches)
        parsed.branches = (hit_branches, all_branches)
    return parsed


def _parse(path: str, raw: bytes) -> _Parsed:
    """Chooses a reader by content where it can, because a report's name doesn't always say its format."""
    if path.endswith(".xml"):
        return _parse_cobertura(raw)
    if path.endswith(".info"):
        return _parse_lcov(raw)
    try:
        data = json.loads(raw)
    except (ValueError, RecursionError) as exc:
        raise _BadReport("malformed JSON") from exc
    if not isinstance(data, dict):
        raise _BadReport("an unknown format")
    if "files" in data and ("totals" in data or "meta" in data):
        return _parse_coverage_json(data)
    if isinstance(data.get("total"), dict):
        return _parse_istanbul_summary(data)
    if any(isinstance(v, dict) and "statementMap" in v for v in data.values()):
        return _parse_istanbul_final(data)
    raise _BadReport("an unknown format")


class _PathMapper:
    """A report's paths, as repository paths. Reports hold absolute paths from wherever they were made."""

    def __init__(self, ctx: RepoContext, report: str, sources: list[str]) -> None:
        self.root = ctx.root
        self.files = set(ctx.files)
        self.base = report.rsplit("/", 1)[0] if "/" in report else ""
        self.sources = sources
        self.by_name: dict[str, list[str]] = {}
        for path in ctx.files:
            self.by_name.setdefault(path.rsplit("/", 1)[-1], []).append(path)

    def _inside(self, raw: str) -> str | None:
        """A report's path as one relative to the repository, if it can be."""
        if raw.startswith("/"):
            try:
                return Path(raw).relative_to(self.root).as_posix()
            except ValueError:
                return None
        return raw

    def map(self, raw: str) -> str | None:
        path = raw.replace("\\", "/").removeprefix("./")
        inside = self._inside(path)
        candidates = [inside] if inside else []
        for source in self.sources:
            base = self._inside(source.replace("\\", "/").rstrip("/"))
            if base and not path.startswith("/"):
                candidates.append(f"{base}/{path}")
        if self.base and not path.startswith("/"):
            candidates.append(f"{self.base}/{path}")
            # coverage/lcov.info sits a folder inside the project it measures.
            parent = self.base.rsplit("/", 1)[0] if "/" in self.base else ""
            if parent:
                candidates.append(f"{parent}/{path}")
        for candidate in candidates:
            if candidate in self.files:
                return candidate
        return self._by_suffix(path)

    def _by_suffix(self, path: str) -> str | None:
        """The one repository file sharing the most trailing folders. A tie is no answer."""
        parts = path.split("/")
        need = min(2, len(parts))
        best: list[str] = []
        most = 0
        for candidate in self.by_name.get(parts[-1], []):
            shared = 0
            for ours, theirs in zip(reversed(parts), reversed(candidate.split("/")), strict=False):
                if ours != theirs:
                    break
                shared += 1
            if shared > most:
                most, best = shared, [candidate]
            elif shared == most:
                best.append(candidate)
        return best[0] if len(best) == 1 and most >= need else None


def _candidates(ctx: RepoContext) -> list[str]:
    """Reports are usually ignored by Git, so well-known places are looked in as well as the file list."""
    summary = ctx.sections.get("overview") or {}
    folders = {""}
    folders |= {f"{p.split('/', 1)[0]}/" for p in ctx.files if "/" in p}
    folders |= {
        f"{c['path']}/"
        for c in summary.get("components", [])
        if isinstance(c, dict) and c.get("path") not in {"", ".", None}
    }
    found = {p for p in ctx.files if p.rsplit("/", 1)[-1] in _REPORT_NAMES and not is_fixture(p)}
    for folder in folders:
        for inner in _REPORT_FOLDERS:
            for name in _REPORT_NAMES:
                path = f"{folder}{inner}{name}"
                if not is_fixture(path) and (ctx.root / path).is_file():
                    found.add(path)
    return sorted(found)


def _project(path: str) -> str:
    """The folder a report measures: where it is, less a ``coverage`` or ``reports`` folder."""
    folder = path.rsplit("/", 1)[0] if "/" in path else ""
    last = folder.rsplit("/", 1)[-1]
    if f"{last}/" in _REPORT_FOLDERS:
        return folder.rsplit("/", 1)[0] if "/" in folder else ""
    return folder


def _newer_sources(ctx: RepoContext, report: str, mapped: list[str]) -> int:
    """How many of the measured files changed after the report was made."""
    try:
        made = (ctx.root / report).stat().st_mtime
    except OSError:
        return 0
    newer = 0
    for path in mapped:
        try:
            newer += (ctx.root / path).stat().st_mtime > made
        except OSError:
            continue
    return newer


def _not_found(ctx: RepoContext, limits: list[str]) -> dict[str, Any]:
    if (ctx.root / ".coverage").is_file():
        limits = [*limits, "A .coverage data file exists: `coverage xml` makes a report from it."]
    return {
        "status": "not_found",
        "limits": [*limits, _HOW_TO],
        "source": None,
        "line_rate": None,
        "branch_rate": None,
        "files": [],
    }


def analyze(ctx: RepoContext) -> dict[str, Any]:
    limits: list[str] = []
    projects: dict[str, tuple[str, _Parsed]] = {}
    order = {name: i for i, name in enumerate(_REPORT_NAMES)}
    candidates = sorted(_candidates(ctx), key=lambda p: order[p.rsplit("/", 1)[-1]])
    for path in candidates:
        project = _project(path)
        if project in projects:
            continue
        if not is_inside(ctx.root, path):
            limits.append(f"{path} points outside the repository, so it wasn't read")
            continue
        raw = read_bytes(ctx, path, _MAX_REPORT_BYTES)
        if raw is None:
            limits.append(f"{path} couldn't be read: too large")
            continue
        try:
            parsed = _parse(path, raw)
        except _BadReport as exc:
            limits.append(f"{path} couldn't be used: {exc}")
            continue
        except (
            RecursionError,
            MemoryError,
            TypeError,
            AttributeError,
            KeyError,
            ValueError,
        ) as exc:
            limits.append(f"{path} couldn't be used: {type(exc).__name__}")
            continue
        if parsed.line_rate is None and not any(total for _, total in parsed.files.values()):
            limits.append(f"{path} measures no lines, so it says nothing about coverage")
            continue
        projects[project] = (path, parsed)
    if not projects:
        return _not_found(ctx, limits)

    merged: dict[str, tuple[int, int]] = {}
    unmapped: list[str] = []
    for path, parsed in projects.values():
        mapper = _PathMapper(ctx, path, parsed.sources)
        mapped_here = []
        for raw_path, counts in parsed.files.items():
            mapped = mapper.map(raw_path)
            if mapped is None:
                unmapped.append(raw_path.rsplit("/", 1)[-1])
                continue
            old = merged.get(mapped, (0, 0))
            merged[mapped] = (old[0] + counts[0], old[1] + counts[1])
            mapped_here.append(mapped)
        newer = _newer_sources(ctx, path, mapped_here)
        if newer:
            limits.append(
                f"{path} may be out of date: {newer} of the files it measures changed after it"
            )

    reports = sorted(path for path, _ in projects.values())
    if len(projects) == 1:
        only = next(iter(projects.values()))[1]
        line_rate, branch_rate = only.line_rate, only.branch_rate
    else:
        # Several reports, from the different parts of a repository, weigh by their lines.
        covered = sum(c for _, p in projects.values() for c, _ in p.files.values())
        total = sum(t for _, p in projects.values() for _, t in p.files.values())
        line_rate = _ratio(covered, total)
        parts = [p.branches for _, p in projects.values()]
        branch_rate = (
            _ratio(sum(b[0] for b in parts if b), sum(b[1] for b in parts if b))
            if all(parts)
            else None
        )
        limits.append(f"overall rates combine several reports: {some(reports)}")
    if unmapped:
        limits.append(
            f"{len(unmapped)} files in the report aren't in this repository: {some(sorted(unmapped))}"
        )
    return {
        "status": "found",
        "limits": limits,
        "source": reports[0],
        "sources": reports,
        "line_rate": line_rate,
        "branch_rate": branch_rate,
        "files": [
            {
                "path": path,
                "line_rate": _ratio(covered, total),
                "lines_covered": covered,
                "lines_total": total,
            }
            for path, (covered, total) in sorted(merged.items())
        ],
    }
