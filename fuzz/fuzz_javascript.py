"""Fuzz the JavaScript reader, which parses files from repositories nobody has vetted.

    pip install atheris && python fuzz/fuzz_javascript.py -max_total_time=120

Any exception here is a bug: a hostile file must never stop an analysis.
"""

import sys
import tempfile
from pathlib import Path

import atheris

with atheris.instrument_imports():
    from ananke.plexus.graph.javascript import build_javascript, tokenize

_FOLDER = Path(tempfile.mkdtemp())
_FILE = _FOLDER / "fuzzed.js"


def one_input(data: bytes) -> None:
    text = atheris.FuzzedDataProvider(data).ConsumeUnicodeNoSurrogates(len(data))
    tokenize(text)
    _FILE.write_text(text, encoding="utf-8")
    build_javascript(_FOLDER, [_FILE])


if __name__ == "__main__":
    atheris.Setup(sys.argv, one_input)
    atheris.Fuzz()
