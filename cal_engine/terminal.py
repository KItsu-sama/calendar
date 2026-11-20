"""Rich-based terminal output for the calendar CLI.

Centralizes styled terminal output so the CLI, the interactive helper, and
other modules can print colored messages, tables, and structured data through
a single helper instead of scattering ``print()`` calls across the codebase.
"""

from __future__ import annotations

import json
from typing import Optional

from rich.console import Console
from rich.table import Table
from rich.theme import Theme


_THEME = Theme(
    {
        "success": "green",
        "error": "bold red",
        "warning": "yellow",
        "info": "cyan",
        "highlight": "bold magenta",
        "muted": "dim",
    }
)


class Terminal:
    """Handle styled terminal output using Rich.

    Parameters
    ----------
    console:
        Optional Rich ``Console`` to write through. When omitted a default
        console with the application theme is created. Injecting a console
        makes it straightforward to capture or stub output in tests.
    """

    def __init__(self, console: Optional[Console] = None) -> None:
        self.console = console or Console(theme=_THEME)

    # ------------------------------------------------------------------
    # Styled messages
    # ------------------------------------------------------------------

    def success(self, message: str) -> None:
        """Print a success message in green."""
        self.console.print(f"[success]✓[/success] {message}")

    def error(self, message: str) -> None:
        """Print an error message in bold red."""
        self.console.print(f"[error]✗[/error] {message}")

    def warning(self, message: str) -> None:
        """Print a warning message in yellow."""
        self.console.print(f"[warning]![/warning] {message}")

    def info(self, message: str) -> None:
        """Print an informational message in cyan."""
        self.console.print(f"[info]->[/info] {message}")

    def print(self, message: str, style: Optional[str] = None) -> None:
        """Print a message with an optional Rich style."""
        self.console.print(message, style=style)

    def rule(self, title: Optional[str] = None, style: str = "muted") -> None:
        """Print a horizontal rule, optionally with a centered title."""
        self.console.rule(title, style=style)

    # ------------------------------------------------------------------
    # Structured output
    # ------------------------------------------------------------------

    def table(
        self,
        rows: list[dict],
        columns: Optional[list[str]] = None,
        title: Optional[str] = None,
    ) -> None:
        """Print a colorized table from a list of dictionaries."""
        if not rows:
            self.info("No results")
            return

        columns = columns or list(rows[0].keys())
        table = Table(title=title, header_style="bold magenta")
        for column in columns:
            table.add_column(column, style="dim", no_wrap=True)
        for row in rows:
            table.add_row(*[str(row.get(column, "")) for column in columns])
        self.console.print(table)

    def json(self, value: object, title: Optional[str] = None) -> None:
        """Print a JSON-serializable value with syntax highlighting."""
        if title:
            self.rule(title)
        self.console.print_json(json.dumps(value, indent=2, default=str))


# Shared default terminal instance used across the application.
terminal = Terminal()
