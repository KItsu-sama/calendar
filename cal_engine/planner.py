
"""Joint placement of derived activities.

The input activities are generated elsewhere. This module chooses compatible
slots without changing the source rules or storing the generated plan.

Current scope:
- Reuses the existing candidate_slots() relation-window logic.
- Never overlaps selected activities with each other.
- Respects fixed events and protected time already removed from availability.
- Uses priority, candidate scarcity, and relation-window proximity to rank work.
- Explains activities that could not be placed.

This is a deterministic heuristic, not a globally optimal solver.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any

from .derive import candidate_slots


_PRIORITY = {
    "CRITICAL": 0,
    "HIGH": 1,
    "NORMAL": 2,
    "LOW": 3,
}


def _priority(instance: dict[str, Any]) -> int:
    return _PRIORITY.get(str(instance.get("priority", "NORMAL")).upper(), 2)


def _subtract_interval(
    blocks: list[tuple[int, int]],
    cut_start: int,
    cut_end: int,
) -> list[tuple[int, int]]:
    """Remove [cut_start, cut_end) from minute-of-day availability blocks."""
    result = []
    for start, end in blocks:
        if cut_end <= start or cut_start >= end:
            result.append((start, end))
            continue

        if start < cut_start:
            result.append((start, cut_start))
        if cut_end < end:
            result.append((cut_end, end))

    return result


def _edge(instance: dict[str, Any], start: datetime, end: datetime) -> datetime:
    return end if instance.get("near_edge") == "end" else start


def _slot_score(
    instance: dict[str, Any],
    start: datetime,
    end: datetime,
) -> tuple:
    """Lower scores are preferred; all tie-breaking is deterministic."""
    edge = _edge(instance, start, end)
    low = instance.get("near_lo")
    high = instance.get("near_hi")

    # Candidate generation should already enforce the relation window.
    # This score prefers a slot near the middle of that window.
    if low is not None and high is not None:
        midpoint = low + (high - low) / 2
        distance = abs((edge - midpoint).total_seconds())
    else:
        distance = 0

    return (
        distance,
        start,
        end,
        str(instance.get("instance_id", "")),
    )


def plan_activities(
    instances: list[dict[str, Any]],
    usable_by_date: dict[date, list[tuple[int, int]]],
    *,
    step: int = 15,
    max_minutes_per_day: int | None = None,
) -> dict[str, Any]:
    """Choose compatible slots for derived activities.

    `usable_by_date` contains free minute-of-day blocks after fixed events,
    protected time, and buffers have been applied.

    Returns:
        {
            "placements": [{"instance": ..., "start": ..., "end": ...}, ...],
            "unscheduled": [{"instance": ..., "reason": ...}, ...],
            "remaining_by_date": {date: [(start_minute, end_minute), ...]},
        }

    Activities with fewer available candidates are handled earlier within
    the same priority tier. This reduces the chance that a highly constrained
    activity is left without a slot because a flexible one took its only slot.
    """
    if step <= 0:
        raise ValueError("step must be a positive number of minutes")

    remaining = {
        day: sorted(list(blocks))
        for day, blocks in usable_by_date.items()
    }
    planned_minutes: dict[date, int] = defaultdict(int)

    # Count candidates against the original availability to estimate scarcity.
    candidate_map: dict[str, list[tuple[datetime, datetime]]] = {}
    for index, instance in enumerate(instances):
        key = str(instance.get("instance_id", f"instance-{index}"))
        candidates = candidate_slots(instance, usable_by_date, step=step)
        candidate_map[key] = sorted(
            candidates,
            key=lambda pair: _slot_score(instance, pair[0], pair[1]),
        )

    indexed = list(enumerate(instances))

    def order_key(item: tuple[int, dict[str, Any]]) -> tuple:
        index, instance = item
        key = str(instance.get("instance_id", f"instance-{index}"))
        return (
            _priority(instance),
            len(candidate_map[key]),
            str(instance.get("near_hi", "")),
            key,
            index,
        )

    indexed.sort(key=order_key)

    placements: list[dict[str, Any]] = []
    unscheduled: list[dict[str, Any]] = []

    for index, instance in indexed:
        key = str(instance.get("instance_id", f"instance-{index}"))
        original_candidates = candidate_map[key]
        duration = instance.get("duration") or {}
        min_duration = int(duration.get("min", 15))

        if not original_candidates:
            unscheduled.append({
                "instance": instance,
                "reason": "no_candidate_slot_in_available_time",
            })
            continue

        chosen = None

        # Re-check candidates against availability as earlier placements
        # may have occupied a slot since the candidate list was created.
        for start, end in original_candidates:
            day = start.date()
            if day not in remaining:
                continue

            start_min = start.hour * 60 + start.minute
            end_min = start_min + int((end - start).total_seconds() // 60)

            if max_minutes_per_day is not None:
                duration_min = end_min - start_min
                if planned_minutes[day] + duration_min > max_minutes_per_day:
                    continue

            if any(
                block_start <= start_min and end_min <= block_end
                for block_start, block_end in remaining[day]
            ):
                chosen = (start, end, start_min, end_min)
                break

        if chosen is None:
            unscheduled.append({
                "instance": instance,
                "reason": (
                    "candidate_slots_conflicted_with_other_suggestions_or_daily_limit"
                ),
            })
            continue

        start, end, start_min, end_min = chosen
        day = start.date()

        placements.append({
            "instance": instance,
            "start": start,
            "end": end,
            "duration_minutes": end_min - start_min,
        })

        remaining[day] = _subtract_interval(
            remaining[day], start_min, end_min
        )
        planned_minutes[day] += end_min - start_min

    placements.sort(key=lambda item: (item["start"], item["end"]))
    unscheduled.sort(
        key=lambda item: (
            _priority(item["instance"]),
            str(item["instance"].get("instance_id", "")),
        )
    )

    return {
        "placements": placements,
        "unscheduled": unscheduled,
        "remaining_by_date": remaining,
    }
