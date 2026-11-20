"""
Batch schedule import/export helpers.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from .models import ActivityRule
from .parser import parse_batch_schedule
from .storage import CalendarState, StorageError


def load_batch_file(
    path: str | Path,
    reference_date: date,
) -> list[ActivityRule]:
    source = Path(path)

    try:
        text = source.read_text(encoding="utf-8")
    except OSError as error:
        raise StorageError(
            f"Could not read batch schedule '{source}': {error}"
        ) from None

    return parse_batch_schedule(
        text,
        reference_date=reference_date,
    )


def import_batch_file(
    state: CalendarState,
    path: str | Path,
    reference_date: date,
) -> tuple[CalendarState, list[str]]:
    rules = load_batch_file(
        path,
        reference_date,
    )

    updated = state.copy()
    added: list[str] = []

    for rule in rules:
        if updated.get_rule(rule.id) is not None:
            raise StorageError(
                f"Activity '{rule.id}' already exists."
            )

        updated.add_rule(rule)
        added.append(rule.id)

    return updated, added