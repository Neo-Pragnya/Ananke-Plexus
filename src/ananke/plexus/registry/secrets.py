"""Secret detection before registration (spec §157, §158).

Runtime credentials must never be persisted in the registry. Findings never include
the secret itself — only a redacted excerpt.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

REDACTION = "[REDACTED]"
_MAX_SCAN_BYTES = 1_000_000

_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("aws-access-key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("private-key", re.compile(r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----")),
    ("github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b")),
    ("pypi-token", re.compile(r"\bpypi-[A-Za-z0-9_\-]{40,}\b")),
    ("slack-token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b")),
    ("bearer-token", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{24,}\b")),
    ("openai-key", re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}\b")),
    (
        "generic-credential",
        re.compile(
            r"""(?ix)\b(?:api[_-]?key|secret|token|passwd|password)\b\s*[:=]\s*
            ['"]?(?!\$\{|<|\{\{|your[_-]|example|changeme|xxx|\*\*\*)[A-Za-z0-9_\-/+=]{16,}['"]?"""
        ),
    ),
]


_PRIVATE_KEY_BLOCK = re.compile(
    r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----.*?(?:-----END (?:[A-Z]+ )?PRIVATE KEY-----|\Z)",
    re.DOTALL,
)


@dataclass(frozen=True)
class SecretFinding:
    path: str
    line: int
    rule: str
    excerpt: str


def _redact_excerpt(line: str, span: tuple[int, int]) -> str:
    start, end = span
    out = line[:start] + REDACTION + line[end:]
    return out.strip()[:120]


def scan_text(path: str, text: str) -> list[SecretFinding]:
    findings: list[SecretFinding] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        for rule, pattern in _RULES:
            match = pattern.search(line)
            if match:
                findings.append(
                    SecretFinding(path, lineno, rule, _redact_excerpt(line, match.span()))
                )
                break
    return findings


def _decode(data: bytes) -> str | None:
    if len(data) > _MAX_SCAN_BYTES or b"\x00" in data[:4096]:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def scan_files(files: dict[str, bytes]) -> list[SecretFinding]:
    out: list[SecretFinding] = []
    for path in sorted(files):
        text = _decode(files[path])
        if text is not None:
            out.extend(scan_text(path, text))
    return out


def redact_files(files: dict[str, bytes]) -> tuple[dict[str, bytes], int]:
    """Replace every detected secret with ``[REDACTED]``; returns (files, replacements)."""
    result = dict(files)
    count = 0
    for path in sorted(files):
        text = _decode(files[path])
        if text is None:
            continue
        new_text, n = _PRIVATE_KEY_BLOCK.subn(REDACTION, text)
        count += n
        for _, pattern in _RULES:
            new_text, n = pattern.subn(REDACTION, new_text)
            count += n
        if new_text != text:
            result[path] = new_text.encode()
    return result, count
