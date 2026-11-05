"""The same sources give the same analysis, so asking again reads it back, until anything it read changes."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from ananke.plexus import intel
from ananke.plexus.app import AppError, AppFacade
from ananke.plexus.context import repo_map

SHOP = Path(__file__).resolve().parent.parent / "fixtures" / "repo-mapping" / "shop"


@pytest.fixture
def shop(tmp_path: Path) -> Path:
    root = tmp_path / "shop"
    shutil.copytree(SHOP, root)
    run = lambda *a: subprocess.run(["git", *a], cwd=root, check=True, capture_output=True)  # noqa: E731
    run("init", "-q")
    run("add", "-A")
    run("-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "init")
    return root


def _count_runs(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    calls = [0]
    real = intel._analyze

    def counted(root: Path, *, check_vulnerabilities: bool):  # type: ignore[no-untyped-def]
        calls[0] += 1
        return real(root, check_vulnerabilities=check_vulnerabilities)

    monkeypatch.setattr(intel, "_analyze", counted)
    return calls


def test_asking_again_reads_the_analysis_back(shop: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _count_runs(monkeypatch)
    first = intel.analyze(shop)
    assert intel.analyze(shop) == first
    assert calls[0] == 1


def test_an_edit_means_a_new_analysis(shop: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _count_runs(monkeypatch)
    intel.analyze(shop)
    target = next(p for p in shop.rglob("*.py"))
    target.write_text(target.read_text() + "\n\ndef added():\n    return 1\n")
    intel.analyze(shop)
    assert calls[0] == 2


def test_a_coverage_report_git_ignores_still_counts(
    shop: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report = shop / "coverage.xml"
    original = report.read_text()
    report.unlink()
    (shop / ".gitignore").write_text("coverage.xml\n")
    commit = [
        "git",
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@example.com",
        "commit",
        "-qam",
        "ignore",
    ]
    subprocess.run(["git", "add", "-A"], cwd=shop, check=True)
    subprocess.run(commit, cwd=shop, check=True)
    calls = _count_runs(monkeypatch)
    assert intel.analyze(shop)["sections"]["coverage"]["status"] == "not_found"
    report.write_text(original)
    first = intel.analyze(shop)["sections"]["coverage"]
    assert first["status"] == "found"
    # the same report, saying something else, made later
    changed = re.sub(r'(<coverage[^>]*?line-rate=")[^"]*"', r'\g<1>0.123"', original, count=1)
    assert changed != original
    report.write_text(changed)
    later = report.stat().st_mtime_ns + 5_000_000_000
    os.utime(report, ns=(later, later))
    assert intel.analyze(shop)["sections"]["coverage"]["line_rate"] != first["line_rate"]
    assert calls[0] == 3


def test_a_cve_check_always_asks_again(shop: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _count_runs(monkeypatch)
    from ananke.plexus.intel import dependencies

    monkeypatch.setattr(dependencies, "_request", lambda *a, **k: {"results": []})
    intel.analyze(shop)
    intel.analyze(shop, check_vulnerabilities=True)
    intel.analyze(shop, check_vulnerabilities=True)
    assert calls[0] == 3


def test_a_plain_analysis_never_reads_back_a_cve_check(
    shop: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ananke.plexus.intel import dependencies

    monkeypatch.setattr(dependencies, "_request", lambda *a, **k: {"results": []})
    calls = _count_runs(monkeypatch)
    intel.analyze(shop, check_vulnerabilities=True)
    plain = intel.analyze(shop)
    assert calls[0] == 2
    assert (
        plain["sections"]["dependencies"]["status"] != "scanned"
        or plain["sections"]["dependencies"].get("vulnerabilities") is None
    )


def test_the_map_uses_the_analysis_just_made(shop: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _count_runs(monkeypatch)
    intel.analyze(shop)
    assert repo_map(shop)["map"]
    assert calls[0] == 1


def test_many_files_read_at_once_share_one_snapshot(shop: Path) -> None:
    app = AppFacade(shop)
    paths = sorted(p.relative_to(shop).as_posix() for p in shop.rglob("*.py"))[:5]
    found = app.read_sources([*paths, "../outside.py", ".git/config", "missing.py"])
    assert set(found["files"]) == set(paths)
    assert found["problems"] == {
        "../outside.py": "PATH_ESCAPE",
        ".git/config": "PATH_DENIED",
        "missing.py": "SOURCE_NOT_FOUND",
    }
    assert {f["source_snapshot"] for f in found["files"].values()} == {found["source_snapshot"]}
    assert found["files"][paths[0]] == app.read_source(paths[0])


def test_too_many_files_at_once_is_refused(shop: Path) -> None:
    with pytest.raises(AppError) as refused:
        AppFacade(shop).read_sources(["a.py"] * 3, max_files=2)
    assert refused.value.code == "INVALID_REQUEST"
