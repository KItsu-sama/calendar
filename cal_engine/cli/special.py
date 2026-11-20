# today, week, batch, reset

from datetime import date, timedelta
from pathlib import Path
from typing import Optional

from ..scheduling.batch import import_batch_file
from ..scheduling.change_log import log_change
from ..core.storage import CalendarState
from .output import _print_json


def _run_batch(
    path: str,
    state: CalendarState,
    reference: date,
    save_path: Optional[str],
) -> int:
    updated, added = import_batch_file(
        state,
        path,
        reference,
    )

    result = {
        "status": "ADDED",
        "count": len(added),
        "activities": added,
    }

    _print_json(result)

    if save_path:
        updated.save(save_path)

    log_change(
        "BATCH_IMPORT",
        activities=added,
        details=f"Imported {len(added)} activities from {path}.",
        log_path=(
            Path(save_path).with_name("cal_changes.jsonl")
            if save_path
            else "cal_changes.jsonl"
        ),
    )

    return 0

def _run_today(
    state: CalendarState,
    reference: date,
) -> int:
    schedule = state.resolver().build_actual_schedule(
        reference,
        reference,
    )

    _print_json(
        {
            "date": reference.isoformat(),
            "day": reference.strftime("%A"),
            "schedule": [
                item.to_dict()
                for item in schedule
            ],
        }
    )

    return 0

def _run_week(
    state: CalendarState,
    reference: date,
) -> int:
    start = reference - timedelta(
        days=reference.weekday()
    )

    end = start + timedelta(days=6)

    schedule = state.resolver().build_actual_schedule(
        start,
        end,
    )

    _print_json(
        {
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "schedule": [
                item.to_dict()
                for item in schedule
            ],
        }
    )

    return 0