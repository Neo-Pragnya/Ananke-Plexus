"""Terminal rendering helpers."""

from typing import Any

from rich.console import Console
from rich.table import Table

console = Console()


def render_result(summary: str, details: dict[str, Any], *, ok: bool = True) -> None:
    """Print a one-line outcome (✓ on success, ✗ on failure) and an optional field/value table."""
    mark = "[bold green]✓[/bold green]" if ok else "[bold red]✗[/bold red]"
    console.print(f"{mark} {summary}")
    if not details:
        return
    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Field")
    table.add_column("Value")
    for key, value in details.items():
        table.add_row(str(key), str(value))
    console.print(table)
