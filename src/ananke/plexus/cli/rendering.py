"""Terminal rendering helpers."""

from typing import Any

from rich.console import Console
from rich.table import Table

console = Console()


def render_result(summary: str, details: dict[str, Any]) -> None:
    console.print(f"[bold green]✓[/bold green] {summary}")
    if not details:
        return
    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Field")
    table.add_column("Value")
    for key, value in details.items():
        table.add_row(str(key), str(value))
    console.print(table)
