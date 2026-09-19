"""Registry micro-benchmarks and regression tracking (spec §132, §170).

``ananke registry benchmark`` builds a throw-away registry, times the operations the spec lists
(exact lookup, version list, small and 100-dependency resolves, FTS search, docs builds, CAS insert,
export/import, integrity check) and reports median / p95 latency next to the spec's *goals*. The
goals are targets, not guarantees, so missing one is reported but never a failure by itself.

Regression tracking: ``--save-baseline`` stores a report; ``--baseline`` compares a later run and
fails when an operation slowed down by more than ``--tolerance`` (and by more than a small
absolute floor, so sub-millisecond noise cannot trip it). Compare runs from the *same machine*.
"""

from __future__ import annotations

import json
import platform
import random
import sqlite3
import statistics
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel, Field

from ananke.plexus.registry.importers.base import ImportedArtifact
from ananke.plexus.registry.models import ArtifactManifest, Provenance
from ananke.plexus.registry.policy import RegistryPolicy
from ananke.plexus.registry.registry import Registry

# spec §132 goals (ms)
TARGETS_MS: dict[str, float] = {
    "exact_lookup": 5.0,
    "version_list": 10.0,
    "resolve_small": 20.0,
    "fts_search": 50.0,
    "docs_incremental": 100.0,
}
NOISE_FLOOR_MS = 2.0
_TOPICS = ["graph", "review", "test", "lint", "scan", "deploy", "index", "route", "plan", "audit"]


class BenchResult(BaseModel):
    name: str
    iterations: int
    median_ms: float
    p95_ms: float
    min_ms: float
    target_ms: float | None = None

    @property
    def within_target(self) -> bool | None:
        return None if self.target_ms is None else self.median_ms <= self.target_ms


class BenchReport(BaseModel):
    size: int
    python: str = Field(default_factory=platform.python_version)
    sqlite: str = sqlite3.sqlite_version
    fts: bool = False
    results: list[BenchResult] = Field(default_factory=list)

    def by_name(self) -> dict[str, BenchResult]:
        return {r.name: r for r in self.results}


class Regression(BaseModel):
    name: str
    baseline_ms: float
    current_ms: float
    ratio: float


def _skill(
    name: str, version: str, rng: random.Random, deps: list[str] | None = None
) -> ImportedArtifact:
    topic = rng.choice(_TOPICS)
    data: dict[str, object] = {
        "kind": "skill",
        "namespace": "bench",
        "name": name,
        "version": version,
        "summary": f"{topic} helper {name}",
        "license": {"expression": "MIT"},
        "capabilities": [f"{topic}.run", "bench.common"],
        "metadata": {"tags": [topic, "bench"]},
    }
    if deps:
        data["dependencies"] = {"skills": [{"ref": f"bench/{d}", "version": "^1"} for d in deps]}
    return ImportedArtifact(
        manifest=ArtifactManifest.model_validate(data),
        files={"README.md": f"# {name}\n{topic}\n".encode()},
        provenance=Provenance(source_type="bench", source_name=name),
    )


def _agent(name: str, skills: list[str]) -> ImportedArtifact:
    manifest = ArtifactManifest.model_validate(
        {
            "kind": "agent",
            "namespace": "bench",
            "name": name,
            "version": "1.0.0",
            "summary": f"agent {name}",
            "license": {"expression": "MIT"},
            "skills": [{"ref": f"bench/{s}", "version": "^1"} for s in skills],
        }
    )
    return ImportedArtifact(
        manifest=manifest,
        files={"README.md": f"# {name}\n".encode()},
        provenance=Provenance(source_type="bench"),
    )


def _measure(fn: Callable[[], object], iterations: int) -> list[float]:
    fn()  # warm caches / lazy imports
    samples = []
    for _ in range(iterations):
        start = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - start) * 1000)
    return samples


def _result(name: str, samples: list[float]) -> BenchResult:
    ordered = sorted(samples)
    p95 = ordered[min(len(ordered) - 1, round(0.95 * (len(ordered) - 1)))]
    return BenchResult(
        name=name,
        iterations=len(samples),
        median_ms=round(statistics.median(ordered), 3),
        p95_ms=round(p95, 3),
        min_ms=round(ordered[0], 3),
        target_ms=TARGETS_MS.get(name),
    )


def run_benchmarks(
    size: int = 500,
    iterations: int = 20,
    *,
    only: set[str] | None = None,
    seed: int = 1,
    workdir: Path | None = None,
) -> BenchReport:
    """Run the suite in a temporary registry. ``size`` is the number of seeded artifacts."""
    rng = random.Random(seed)  # noqa: S311 - reproducible benchmark data, not security
    report = BenchReport(size=size)

    def want(name: str) -> bool:
        return only is None or name in only

    def record(name: str, fn: Callable[[], object], iters: int = iterations) -> None:
        if want(name):
            report.results.append(_result(name, _measure(fn, iters)))

    with tempfile.TemporaryDirectory(prefix="ananke-bench-", dir=workdir) as tmp:
        root = Path(tmp)
        reg = Registry.open(root / "registry", create=True, policy=RegistryPolicy(), actor="bench")
        reg.init()
        report.fts = reg.store.fts_available
        try:
            _run(reg, root, size, iterations, rng, want, record, report)
        finally:
            reg.close()
    return report


def _run(
    reg: Registry,
    root: Path,
    size: int,
    iterations: int,
    rng: random.Random,
    want: Callable[[str], bool],
    record: Callable[..., None],
    report: BenchReport,
) -> None:
    from ananke.plexus.registry.payload import pack_files
    from ananke.plexus.registry.portable import export_registry, import_registry
    from ananke.plexus.registry.resolver import Requirement, Resolver
    from ananke.plexus.registry.search import search

    blobs = [rng.randbytes(2048) for _ in range(max(iterations, 1) + 1)]
    counter = iter(range(10**9))
    record(
        "cas_insert",
        lambda: reg.cas.put(
            pack_files({"f": blobs[next(counter) % len(blobs)] + str(next(counter)).encode()})
        ),
    )

    names = [f"s{i:05d}" for i in range(size)]
    start = time.perf_counter()
    for skill_name in names:
        reg.register(_skill(skill_name, "1.0.0", rng))
    if want("register"):
        total_ms = (time.perf_counter() - start) * 1000
        per = total_ms / max(size, 1)
        report.results.append(
            BenchResult(
                name="register",
                iterations=size,
                median_ms=round(per, 3),
                p95_ms=round(per, 3),
                min_ms=round(per, 3),
            )
        )
    many = "s00000"
    for v in range(1, 20):
        reg.register(_skill(many, f"1.{v}.0", rng))
    small = [names[i] for i in range(min(3, len(names)))]
    reg.register(_agent("small-agent", small))
    hundred = [names[i % len(names)] for i in range(100)]
    reg.register(_agent("big-agent", list(dict.fromkeys(hundred))))

    record(
        "exact_lookup",
        lambda: reg.exact_version(f"bench/{rng.choice(names)}@1.0.0"),
        iterations * 5,
    )
    record("version_list", lambda: reg.store.versions_of("skill", "bench", many))
    record(
        "resolve_small",
        lambda: Resolver(reg).resolve(Requirement.parse("bench/small-agent@^1")),
        iterations,
    )
    record(
        "resolve_100_deps",
        lambda: Resolver(reg).resolve(Requirement.parse("bench/big-agent@^1")),
        max(3, iterations // 4),
    )
    record("fts_search", lambda: search(reg, f"{rng.choice(_TOPICS)} helper"), iterations)

    docs = root / "docs"
    if want("docs_full_build"):
        record("docs_full_build", lambda: reg.build_docs(docs, incremental=False), 3)
    if want("docs_incremental"):
        reg.build_docs(docs, incremental=False)
        n = iter(range(2, 10**6))

        def bump() -> None:
            reg.register(_skill("s00001", f"2.{next(n)}.0", rng))
            reg.build_docs(docs)

        record("docs_incremental", bump, 3)

    if want("integrity_quick"):
        record("integrity_quick", lambda: reg.verify(deep=False), 3)

    if want("export_import"):

        def roundtrip() -> None:
            archive = Path(
                export_registry(reg, root / "bench-export.tar.gz", compression="gzip").path
            )
            target = Registry.open(
                root / f"import-{next(counter)}",
                create=True,
                policy=RegistryPolicy(),
                actor="bench",
            )
            try:
                target.init()
                import_registry(target, archive)
            finally:
                target.close()

        record("export_import", roundtrip, 2)


def compare(
    current: BenchReport, baseline: BenchReport, tolerance: float = 1.5
) -> list[Regression]:
    """Operations that got slower than ``baseline`` by more than ``tolerance``x *and* the noise floor."""
    base = baseline.by_name()
    out: list[Regression] = []
    for r in current.results:
        b = base.get(r.name)
        if b is None or b.median_ms <= 0:
            continue
        ratio = r.median_ms / b.median_ms
        if ratio > tolerance and r.median_ms - b.median_ms > NOISE_FLOOR_MS:
            out.append(
                Regression(
                    name=r.name,
                    baseline_ms=b.median_ms,
                    current_ms=r.median_ms,
                    ratio=round(ratio, 2),
                )
            )
    return out


def load_report(path: Path) -> BenchReport:
    return BenchReport.model_validate(json.loads(path.read_text(encoding="utf-8")))


def save_report(report: BenchReport, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
