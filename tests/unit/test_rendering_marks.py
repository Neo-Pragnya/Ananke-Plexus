"""A failure must never be printed with a success mark."""

from __future__ import annotations

from rich.console import Console

from ananke.plexus.cli import rendering


def _capture(monkeypatch, **kwargs) -> str:  # type: ignore[no-untyped-def]
    console = Console(record=True, width=120, color_system=None)
    monkeypatch.setattr(rendering, "console", console)
    rendering.render_result(
        "Spec validation failed.", {"errors": "HASH_MISMATCH:spec.md"}, **kwargs
    )
    return console.export_text()


def test_success_is_marked_with_a_tick(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    assert _capture(monkeypatch).startswith("✓ ")


def test_failure_is_marked_with_a_cross(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    out = _capture(monkeypatch, ok=False)
    assert out.startswith("✗ ") and "✓" not in out
