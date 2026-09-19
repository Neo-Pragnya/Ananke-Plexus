"""Safe Markdown → HTML for untrusted artifact docs (spec §131).

Everything is HTML-escaped *first*; only a small allow-list of constructs is then turned
back into tags. Raw HTML in the source can never survive. Links are limited to
http/https/mailto and relative paths; images are rendered as plain links (no automatic
external loads).
"""

from __future__ import annotations

import html
import re

MAX_CHARS = 20_000
_SAFE_SCHEMES = ("http://", "https://", "mailto:")
_LINK = re.compile(r"\[([^\]\n]{1,200})\]\(([^)\s]{1,500})\)")
_CODE = re.compile(r"`([^`\n]+)`")
_BOLD = re.compile(r"\*\*([^*\n]+)\*\*|__([^_\n]+)__")
_ITALIC = re.compile(r"(?<![*\w])\*([^*\n]+)\*(?![*\w])|(?<![_\w])_([^_\n]+)_(?![_\w])")


def _safe_url(url: str) -> str | None:
    raw = html.unescape(url).strip()
    low = raw.lower()
    if low.startswith(_SAFE_SCHEMES):
        return raw
    if (
        ":" in raw.split("/", 1)[0]
        or raw.startswith(("//", "\\"))
        or low.startswith(("javascript", "data", "vbscript"))
    ):
        return None
    return raw


def _inline(text: str) -> str:
    """``text`` is already escaped."""
    codes: list[str] = []

    def stash(m: re.Match[str]) -> str:
        codes.append(f"<code>{m.group(1)}</code>")
        return f"\x00{len(codes) - 1}\x00"

    text = _CODE.sub(stash, text)

    def link(m: re.Match[str]) -> str:
        label, url = m.group(1), m.group(2)
        safe = _safe_url(url)
        if safe is None:
            return label
        external = safe.lower().startswith(("http://", "https://"))
        rel = ' rel="noopener noreferrer nofollow"' if external else ""
        return f'<a href="{html.escape(safe, quote=True)}"{rel}>{label}</a>'

    text = re.sub(r"!\[([^\]\n]*)\]\(", r"[\1](", text)  # images become links
    text = _LINK.sub(link, text)
    text = _BOLD.sub(lambda m: f"<strong>{m.group(1) or m.group(2)}</strong>", text)
    text = _ITALIC.sub(lambda m: f"<em>{m.group(1) or m.group(2)}</em>", text)
    return re.sub(r"\x00(\d+)\x00", lambda m: codes[int(m.group(1))], text)


def render_markdown(source: str) -> str:
    text = source[:MAX_CHARS].replace("\r\n", "\n")
    text = re.sub(r"^---\n.*?\n---\n", "", text, count=1, flags=re.DOTALL)  # front matter
    lines = text.split("\n")
    out: list[str] = []
    i = 0
    para: list[str] = []
    list_kind: str | None = None

    def flush_para() -> None:
        if para:
            out.append("<p>" + _inline(html.escape(" ".join(p.strip() for p in para))) + "</p>")
            para.clear()

    def close_list() -> None:
        nonlocal list_kind
        if list_kind:
            out.append(f"</{list_kind}>")
            list_kind = None

    while i < len(lines):
        line = lines[i]
        fence = re.match(r"^```\s*([A-Za-z0-9_+-]*)\s*$", line)
        if fence:
            flush_para()
            close_list()
            i += 1
            code: list[str] = []
            while i < len(lines) and not lines[i].startswith("```"):
                code.append(lines[i])
                i += 1
            i += 1
            out.append("<pre><code>" + html.escape("\n".join(code)) + "</code></pre>")
            continue
        heading = re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", line)
        if heading:
            flush_para()
            close_list()
            level = min(len(heading.group(1)) + 1, 6)  # page title owns <h1>
            out.append(f"<h{level}>{_inline(html.escape(heading.group(2)))}</h{level}>")
        elif re.match(r"^\s*([-*+])\s+", line) or re.match(r"^\s*\d+[.)]\s+", line):
            flush_para()
            kind = "ol" if re.match(r"^\s*\d+[.)]\s+", line) else "ul"
            if list_kind != kind:
                close_list()
                out.append(f"<{kind}>")
                list_kind = kind
            item = re.sub(r"^\s*(?:[-*+]|\d+[.)])\s+", "", line)
            out.append("<li>" + _inline(html.escape(item)) + "</li>")
        elif re.match(r"^\s*>\s?", line):
            flush_para()
            close_list()
            out.append(
                "<blockquote>"
                + _inline(html.escape(re.sub(r"^\s*>\s?", "", line)))
                + "</blockquote>"
            )
        elif re.match(r"^\s*([-*_])\s*(\1\s*){2,}$", line):
            flush_para()
            close_list()
            out.append("<hr>")
        elif not line.strip():
            flush_para()
            close_list()
        else:
            close_list()
            para.append(line)
        i += 1
    flush_para()
    close_list()
    return "\n".join(out)
