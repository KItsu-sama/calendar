"""Deterministic, coordinated placement for generated flexible activities.

This is intentionally a small greedy scheduler, not an optimizer. It chooses
one candidate per activity, reserves the chosen interval, and never overlaps
proposed activities. Existing fixed occurrences are already excluded from the
usable blocks supplied by the caller.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from .derive import candidate_slots

_PRIORITY = {"CRITICAL": 0, "HIGH": 1, "NORMAL": 2, "LOW": 3}
_FLEXIBILITY = {"FIXED": 0, "CHANGEABLE": 1, "FLEXIBLE": 2, "OPTIONAL": 3}


def _rank(inst: dict) -> tuple:
    """Stable scheduling order: priority, flexibility, then identity."""
    return (
        _PRIORITY.get(str(inst.get("priority", "NORMAL")).upper(), 2),
        _FLEXIBILITY.get(str(inst.get("flexibility", "FLEXIBLE")).upper(), 2),
        str(inst.get("instance_id", "")),
    )


def _candidate_rank(inst: dict, slot: tuple[datetime, datetime]) -> tuple:
    start, end = slot
    # BEFORE activities should finish as late as possible before the anchor;
    # AFTER activities should begin as early as possible after the anchor.
    if inst.get("near_edge") == "end":
        return (abs((inst["near_hi"] - end).total_seconds()), start)
    return (abs((start - inst["near_lo"]).total_seconds()), start)


def _overlaps(a: tuple[datetime, datetime], b: tuple[datetime, datetime],
              buffer_min: int = 0) -> bool:
    pad = timedelta(minutes=max(0, buffer_min))
    return a[0] < b[1] + pad and b[0] < a[1] + pad


def place_activities(
    instances: list[dict],
    usable_by_date: dict[date, list[tuple[int, int]]],
    *,
    step: int = 15,
    buffer_min: int = 0,
) -> dict:
    """Return a deterministic proposed plan and reasons for unplaced items.

    Output shape:
      {"placed": [{"instance": ..., "start": datetime, "end": datetime}, ...],
       "unplaced": [{"instance": ..., "reason": str}, ...]}

    A task that had legal candidates but lost all of them to higher-ranked
    placements is reported as a conflict, not as having no availability.
    """
    reserved: list[tuple[datetime, datetime]] = []
    placed: list[dict] = []
    unplaced: list[dict] = []

    for inst in sorted(instances, key=_rank):
        candidates = candidate_slots(inst, usable_by_date, step=step)
        if not candidates:
            unplaced.append({"instance": inst, "reason": "no candidate slot"})
            continue
        chosen = next(
            (slot for slot in sorted(candidates, key=lambda x: _candidate_rank(inst, x))
             if not any(_overlaps(slot, taken, buffer_min) for taken in reserved)),
            None,
        )
        if chosen is None:
            unplaced.append({"instance": inst, "reason": "conflicts with higher-ranked proposed activities"})
            continue
        reserved.append(chosen)
        placed.append({"instance": inst, "start": chosen[0], "end": chosen[1]})

    placed.sort(key=lambda item: (item["start"], item["end"], _rank(item["instance"])))
    unplaced.sort(key=lambda item: _rank(item["instance"]))
    return {"placed": placed, "unplaced": unplaced}
