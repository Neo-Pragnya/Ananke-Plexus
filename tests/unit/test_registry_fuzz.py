"""Fuzz targets for every registry parser that reads untrusted input (spec §169).

Deterministic, seeded and bounded so they run in CI. Scale them up locally with
``ANANKE_FUZZ_ITERATIONS=20000 pytest tests/unit/test_registry_fuzz.py`` and vary the corpus with
``ANANKE_FUZZ_SEED``. The contract for each target is the same: hostile input may be *rejected*
with a registry error, but must never crash, hang, escape a directory or change registry state.
"""

from __future__ import annotations

import contextlib
import gzip
import io
import os
import random
import string
import tarfile
import zipfile
from html.parser import HTMLParser
from pathlib import Path

import pytest
from registry_support import add, open_registry

from ananke.plexus.registry.docsgen.markdown import render_markdown
from ananke.plexus.registry.errors import PayloadError, RegistryError
from ananke.plexus.registry.importers.common import parse_structured
from ananke.plexus.registry.importers.vcs_archive import _extract_safely
from ananke.plexus.registry.payload import safe_relpath
from ananke.plexus.registry.portable import export_registry, import_registry
from ananke.plexus.registry.search import parse_query
from ananke.plexus.registry.semver import VersionReq, normalize_version

ITERATIONS = int(os.environ.get("ANANKE_FUZZ_ITERATIONS", "150"))
SEED = int(os.environ.get("ANANKE_FUZZ_SEED", "20260919"))

VALID_MANIFEST = (
    "kind: skill\nnamespace: core\nname: fuzzed\nversion: 1.2.3\nsummary: a skill\n"
    "license: {expression: MIT}\ncapabilities: [graph.query]\n"
    "permissions:\n  filesystem: {read: ['src/**']}\n"
)
ALPHABET = string.printable + "\x00\x1b\u202e\u2028é✓"


def _rng(name: str) -> random.Random:
    return random.Random(f"{SEED}:{name}")


def _mutate(rng: random.Random, data: bytes) -> bytes:
    buf = bytearray(data)
    for _ in range(rng.randint(1, 6)):
        op = rng.randrange(5)
        if not buf:
            buf.append(rng.randrange(256))
        i = rng.randrange(len(buf))
        if op == 0:
            buf[i] ^= 1 << rng.randrange(8)
        elif op == 1:
            del buf[i : i + rng.randint(1, 8)]
        elif op == 2:
            buf[i:i] = rng.randbytes(rng.randint(1, 8))
        elif op == 3:
            buf[i:i] = buf[rng.randrange(len(buf)) :][: rng.randint(1, 16)]
        else:
            buf = buf[: rng.randint(0, len(buf))]
    return bytes(buf)


def _text(rng: random.Random, n: int = 60) -> str:
    return "".join(rng.choice(ALPHABET) for _ in range(rng.randint(0, n)))


# --------------------------------------------------------------------------- versions


def test_version_parsers_reject_cleanly() -> None:
    rng = _rng("semver")
    atoms = ["^", "~", ">=", "<", "||", " ", "1", "2.0", "x", "*", "-", "+", ".", "rc", "0"]
    for _ in range(ITERATIONS * 4):
        text = (
            "".join(rng.choice(atoms) for _ in range(rng.randint(0, 8)))
            if rng.random() < 0.7
            else _text(rng, 30)
        )
        for fn in (VersionReq.parse, normalize_version):
            try:
                out = fn(text)
            except RegistryError:
                continue
            if isinstance(out, VersionReq):
                out.matches(normalize_version("1.2.3"))


# --------------------------------------------------------------------------- query DSL


def test_query_dsl_and_search_never_crash(tmp_path: Path) -> None:
    reg = open_registry(tmp_path)
    add(reg, "graph-review")
    rng = _rng("query")
    specials = [
        '"',
        "'",
        "*",
        "(",
        ")",
        ":",
        "NEAR",
        "OR",
        "AND",
        "NOT",
        "-",
        "^",
        "\\",
        "%",
        "_",
        ";--",
    ]
    for _ in range(ITERATIONS):
        q = " ".join(
            rng.choice([*specials, "kind:skill", "capability:", "trust:approved", _text(rng, 8)])
            for _ in range(rng.randint(0, 6))
        )
        parse_query(q)
        with contextlib.suppress(RegistryError):
            reg.search(q)
    assert reg.store.count_versions() == 1


# --------------------------------------------------------------------------- manifests


def test_structured_parsers_only_raise_registry_errors() -> None:
    rng = _rng("structured")
    for _ in range(ITERATIONS * 2):
        data = _mutate(rng, VALID_MANIFEST.encode()) if rng.random() < 0.8 else rng.randbytes(40)
        for name in ("ananke.yaml", "ananke.toml", "ananke.json"):
            with contextlib.suppress(RegistryError):
                parse_structured(name, data)


def test_learning_mutated_manifests_never_writes_garbage(tmp_path: Path) -> None:
    reg = open_registry(tmp_path)
    rng = _rng("learn")
    skill = tmp_path / "skill"
    skill.mkdir()
    for _ in range(ITERATIONS // 2):
        (skill / "ananke.yaml").write_bytes(_mutate(rng, VALID_MANIFEST.encode()))
        with contextlib.suppress(RegistryError):
            reg.learn(str(skill), dry_run=True)
    assert reg.store.count_versions() == 0  # dry runs never register


# --------------------------------------------------------------------------- portable import


def test_mutated_export_archives_are_rejected_without_side_effects(tmp_path: Path) -> None:
    src = open_registry(tmp_path / "src")
    add(src, "graph-review", "1.0.0")
    add(src, "graph-review", "1.1.0")
    good = Path(export_registry(src, tmp_path / "good.tar.gz", compression="gzip").path)
    raw_tar = gzip.decompress(good.read_bytes())
    rng = _rng("import")
    target = open_registry(tmp_path / "dst")
    baseline = target.semantic_state()
    accepted = 0
    for i in range(ITERATIONS):
        blob = (
            gzip.compress(_mutate(rng, raw_tar))
            if rng.random() < 0.6
            else _mutate(rng, good.read_bytes())
        )
        archive = tmp_path / f"m{i % 5}.tar.gz"
        archive.write_bytes(blob)
        try:
            import_registry(target, archive)
        except RegistryError:
            assert target.semantic_state() == baseline, "a rejected import must change nothing"
            continue
        except (EOFError, OSError) as exc:  # gzip-level corruption must be wrapped, not leaked
            pytest.fail(f"unwrapped decompression error: {exc!r}")
        accepted += 1
        # an archive can only be accepted if it is still self-consistent; reset for the next round
        target = open_registry(tmp_path / f"dst{i}")
        baseline = target.semantic_state()
    assert accepted < ITERATIONS  # the corpus really did exercise rejection paths


# --------------------------------------------------------------------------- archive extraction

HOSTILE_NAMES = [
    "../evil.txt",
    "/etc/passwd",
    "a/../../evil.txt",
    "..\\evil.txt",
    "C:\\evil.txt",
    "a/./b/../../../evil",
    "ok/\x00evil",
    "//host/share/x",
    "~/x",
    "a/" + "b/" * 200 + "c",
]


def _tar_with(entries: list[tuple[str, bytes, str]]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for name, data, kind in entries:
            info = tarfile.TarInfo(name)
            if kind == "symlink":
                info.type, info.linkname = tarfile.SYMTYPE, "/etc/passwd"
            elif kind == "hardlink":
                info.type, info.linkname = tarfile.LNKTYPE, "victim"
            else:
                info.size = len(data)
            tar.addfile(info, io.BytesIO(data) if kind == "file" else None)
    return buf.getvalue()


def _assert_contained(dest: Path, outside: Path) -> None:
    assert not outside.exists(), "archive wrote outside its destination"
    root = dest.resolve()
    for p in dest.rglob("*"):
        assert p.resolve() == root or root in p.resolve().parents
        assert not p.is_symlink()


@pytest.mark.parametrize("kind", ["tar", "zip"])
def test_archive_extraction_is_contained(tmp_path: Path, kind: str) -> None:
    rng = _rng(f"extract-{kind}")
    outside = tmp_path / "evil.txt"
    for i in range(ITERATIONS // 2):
        dest = tmp_path / f"d{i}"
        names = [rng.choice(HOSTILE_NAMES) for _ in range(rng.randint(1, 3))] + ["fine/file.txt"]
        names = list(dict.fromkeys(names))
        rng.shuffle(names)
        if kind == "zip":
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w") as zf:
                for n in names:
                    zf.writestr(n, b"x")
            data, name = buf.getvalue(), "a.zip"
        else:
            entries = [
                (n, b"x", rng.choice(["file", "file", "symlink", "hardlink"])) for n in names
            ]
            data, name = _tar_with(entries), "a.tar"
        with contextlib.suppress(RegistryError):
            _extract_safely(data, name, dest)
        if dest.exists():
            _assert_contained(dest, outside)
        assert not outside.exists()


def test_corrupt_archives_are_wrapped(tmp_path: Path) -> None:
    rng = _rng("corrupt")
    for i in range(ITERATIONS // 3):
        for name in ("a.zip", "a.tar", "a.tar.gz"):
            with contextlib.suppress(RegistryError):
                _extract_safely(rng.randbytes(rng.randint(0, 200)), name, tmp_path / f"c{i}{name}")


def test_safe_relpath_never_returns_an_escaping_path() -> None:
    rng = _rng("relpath")
    parts = ["..", ".", "a", "", "/", "\\", "b.txt", "\x00", "C:", "~", "%2e%2e"]
    for _ in range(ITERATIONS * 4):
        raw = "/".join(rng.choice(parts) for _ in range(rng.randint(1, 6)))
        try:
            clean = safe_relpath(raw)
        except (PayloadError, RegistryError):
            continue
        assert not clean.startswith(("/", "\\")) and ".." not in clean.split("/")
        assert "\x00" not in clean and ":" not in clean.split("/")[0]


# --------------------------------------------------------------------------- HTML sanitizer

ALLOWED_TAGS = {
    "p",
    "a",
    "code",
    "pre",
    "strong",
    "em",
    "ul",
    "ol",
    "li",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "blockquote",
    "br",
    "hr",
}
ALLOWED_ATTRS = {"href", "rel"}


class _Audit(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.problems: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag not in ALLOWED_TAGS:
            self.problems.append(f"tag <{tag}>")
        for k, v in attrs:
            if k not in ALLOWED_ATTRS:
                self.problems.append(f"attr {k}")
            if k == "href" and v:
                low = v.strip().lower()
                if ":" in low.split("/", 1)[0] and not low.startswith(
                    ("http://", "https://", "mailto:")
                ):
                    self.problems.append(f"href {v!r}")
                if low.startswith("//"):
                    self.problems.append(f"protocol-relative href {v!r}")


PAYLOADS = [
    "<script>alert(1)</script>",
    "<img src=x onerror=alert(1)>",
    "[x](javascript:alert(1))",
    "[x](JaVaScRiPt:alert(1))",
    "[x](java\tscript:alert(1))",
    "[x](data:text/html;base64,PHNjcmlwdD4=)",
    "[x](//evil.example/x)",
    "[x](\\\\evil\\share)",
    "![x](http://tracker.example/pixel.gif)",
    "<svg/onload=alert(1)>",
    "`<b>`",
    "**<i>x</i>**",
    '[x](http://a.b" onmouseover="alert(1))',
    "<iframe src=//evil>",
    "&lt;script&gt;alert(1)&lt;/script&gt;",
    "---\nkey: <script>\n---\n# t",
]


def _audit(markup: str) -> list[str]:
    parser = _Audit()
    parser.feed(markup)
    parser.close()
    low = markup.lower()
    problems = list(parser.problems)
    if "<script" in low or "<iframe" in low or "<img" in low:
        problems.append("raw dangerous tag survived")
    return problems


@pytest.mark.parametrize("payload", PAYLOADS)
def test_known_xss_payloads_are_neutralised(payload: str) -> None:
    assert _audit(render_markdown(payload)) == []


def test_random_markdown_never_yields_unsafe_html() -> None:
    rng = _rng("markdown")
    bits = [
        "<",
        ">",
        "[",
        "]",
        "(",
        ")",
        "`",
        "*",
        "_",
        "!",
        "#",
        "\n",
        "- ",
        "1. ",
        "http://",
        "javascript:",
        "on",
        "=",
        '"',
        "'",
        "&",
        " ",
    ]
    for _ in range(ITERATIONS * 3):
        src = "".join(
            rng.choice(bits + PAYLOADS + [_text(rng, 6)]) for _ in range(rng.randint(1, 25))
        )
        assert _audit(render_markdown(src)) == [], src
