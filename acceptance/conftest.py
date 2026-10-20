"""Ananke Plexus acceptance suite: shared harness steps plus a few Ananke-specific ones."""

from __future__ import annotations

import re

from harness import *  # noqa: F403  (steps, sandbox fixture and the release-gate hooks)
from harness import REPO, Sandbox
from pytest_bdd import then

collect_ignore = ["fixtures"]  # fixture projects carry their own tests; they are data here


def _package_version() -> str:
    text = (REPO / "src" / "ananke" / "plexus" / "version.py").read_text(encoding="utf-8")
    match = re.search(r'__version__\s*=\s*"([^"]+)"', text)
    assert match, "version.py has no __version__"
    return match.group(1)


@then("the output is the package version")
def output_is_version(sandbox: Sandbox) -> None:
    assert sandbox.last.stdout.strip() == _package_version(), sandbox.last.describe()
