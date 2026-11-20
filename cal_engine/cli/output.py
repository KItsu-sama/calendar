# Shared output formatting

from __future__ import annotations

import json
from typing import Optional

from rich.console import Console

from ..terminal import Terminal


def _print_json(value: dict) -> None:
    Terminal().console.print_json(json.dumps(value, indent=2))


def _print_table(rows: list[dict]) -> None:
    if not rows:
        Terminal().info("No results")
        return

    # Determine the columns to display
    columns = list(rows[0].keys())

    # Calculate the maximum width for each column
    column_widths = {col: len(col) for col in columns}
    for row in rows:
        for col in columns:
            column_widths[col] = max(column_widths[col], len(str(row[col])))

    # Print the header
    header = " | ".join(f"{col:<{column_widths[col]}}" for col in columns)
    Terminal().console.print(header, style="bold magenta")
    Terminal().console.print("-" * len(header), style="dim")

    # Print each row
    for row in rows:
        line = " | ".join(f"{str(row[col]):<{column_widths[col]}}" for col in columns)
        print(line)


def print_activity(rule, console: Optional[Console] = None) -> None:
    """Print a single activity rule with colorized fields."""
    target = console or Console()
    target.print(f"[highlight]{rule.name}[/highlight] ({rule.id})")
    target.print(f"  priority: {rule.priority.value}")
    target.print(f"  flexibility: {rule.flexibility.value}")
    if rule.recurrence:
        target.print(f"  recurrence: {rule.recurrence}")
    if rule.time_constraints:
        target.print(f"  time: {rule.time_constraints}")


def print_error(message: str, console: Optional[Console] = None) -> None:
    """Print an error message in bold red."""
    target = console or Console()
    target.print(f"[error]Error:[/error] {message}")


