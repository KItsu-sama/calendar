"""
Phase 3 — Conflict Detection & Local Solver.

Mirrors the reference `resolve_conflict_locally` from the spec, ported onto
the typed ActivityRule/ScheduleResolver models:

    - No overlap                                         -> DIRECT_ADD
    - Any overlapping item is FIXED                       -> REQUIRES_AI
    - CHANGEABLE item with a defined move_options
      destination that has room (same time-of-day
      preferred, else first fitting slot)                 -> MOVE (local)
    - FLEXIBLE item with same-day room after the new
      activity is placed                                  -> SHIFT (local)
    - Anything else (OPTIONAL, CHANGEABLE with no usable
      destination, multi-hop reordering, ...)              -> REQUIRES_AI

If ANY conflicting item can't be solved locally, the whole result is
REQUIRES_AI — the engine never partially applies a local fix and leaves the
rest to guesswork.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import List, Optional

from .models import ActivityRule, Flexibility, Priority
from .resolver import Interval, ScheduleResolver, next_occurrence


def find_overlapping_intervals(schedule: List[Interval], start: datetime, end: datetime) -> List[Interval]:
    """Strict overlap: touching endpoints (end == start) do not count."""
    return [iv for iv in schedule if iv.start < end and iv.end > start]


@dataclass
class ProposedChange:
    activity_id: str
    action: str  # "MOVE" | "SHIFT"
    new_start: datetime
    new_end: datetime
    reasoning: str = ""


@dataclass
class LocalSolveResult:
    status: str  # "DIRECT_ADD" | "SOLVED_LOCALLY" | "REQUIRES_AI"
    changes: List[ProposedChange] = field(default_factory=list)
    conflicts: List[Interval] = field(default_factory=list)
    reason: Optional[str] = None


def _fixed_window_on(rule: ActivityRule, on_date: date):
    window = rule.time_constraints.fixed_interval
    if window is None:
        return None, None
    s_h, s_m = map(int, window.start_time.split(":"))
    e_h, e_m = map(int, window.end_time.split(":"))
    start = datetime.combine(on_date, datetime.min.time().replace(hour=s_h, minute=s_m))
    end = datetime.combine(on_date, datetime.min.time().replace(hour=e_h, minute=e_m))
    return start, end


def _first_fitting_slot(free_slots: List[Interval], duration: timedelta) -> Optional[Interval]:
    for slot in free_slots:
        if (slot.end - slot.start) >= duration:
            return slot
    return None


def _slot_contains(free_slots: List[Interval], start: datetime, end: datetime) -> bool:
    return any(slot.start <= start and slot.end >= end for slot in free_slots)


def _try_move_changeable(resolver: ScheduleResolver, rule: ActivityRule, conflict: Interval) -> Optional[ProposedChange]:
    if not rule.move_options or not rule.move_options.preferred_days:
        return None
    duration = conflict.end - conflict.start
    for day_code in rule.move_options.preferred_days:
        target_date = next_occurrence(conflict.start.date() + timedelta(days=1), day_code)
        target_free = resolver.free_time_for_day(target_date)

        # Prefer keeping the activity's usual time-of-day if it's open there.
        same_time_start = datetime.combine(target_date, conflict.start.time())
        same_time_end = same_time_start + duration
        if _slot_contains(target_free, same_time_start, same_time_end):
            return ProposedChange(
                activity_id=rule.id, action="MOVE",
                new_start=same_time_start, new_end=same_time_end,
                reasoning=f"Moved to allowed {day_code.title()} window (original time available).",
            )

        slot = _first_fitting_slot(target_free, duration)
        if slot:
            return ProposedChange(
                activity_id=rule.id, action="MOVE",
                new_start=slot.start, new_end=slot.start + duration,
                reasoning=f"Moved to allowed {day_code.title()} window (first available slot).",
            )
    return None


def _try_shift_flexible(
    resolver: ScheduleResolver, rule: ActivityRule, conflict: Interval, new_start: datetime, new_end: datetime
) -> Optional[ProposedChange]:
    on_date = conflict.start.date()
    duration = conflict.end - conflict.start
    day_schedule = resolver.build_actual_schedule(on_date, on_date)
    # Remove the item we're relocating, add the incoming new activity, then
    # see what's left to shift it into.
    remaining = [iv for iv in day_schedule if iv.activity_id != rule.id]
    remaining.append(Interval(start=new_start, end=new_end, activity_id="__new__"))
    day_start = datetime.combine(on_date, datetime.min.time())
    day_end = day_start + timedelta(days=1)
    free_after = ScheduleResolver.calculate_free_time(remaining, day_start, day_end)

    # Only shift forward in the day (never earlier than the activity's own
    # original start) -- reaching back into early-morning/pre-day hours is a
    # judgment call left to the AI escalation path, not the deterministic
    # local solver.
    forward_slots = [s for s in free_after if s.start >= conflict.start]
    slot = _first_fitting_slot(forward_slots, duration)
    if slot:
        return ProposedChange(
            activity_id=rule.id, action="SHIFT",
            new_start=slot.start, new_end=slot.start + duration,
            reasoning="Shifted into same-day free block after the new activity was placed.",
        )
    return None


def resolve_conflict_locally(resolver: ScheduleResolver, new_rule: ActivityRule, on_date: date) -> LocalSolveResult:
    new_start, new_end = _fixed_window_on(new_rule, on_date)
    if new_start is None:
        raise ValueError("resolve_conflict_locally requires a new_rule with a concrete fixed_interval.")

    day_schedule = resolver.build_actual_schedule(on_date, on_date)
    conflicts = find_overlapping_intervals(day_schedule, new_start, new_end)

    if not conflicts:
        return LocalSolveResult(status="DIRECT_ADD")

    protected_conflict = next(
        (
            item
            for item in conflicts
            if item.flexibility == Flexibility.FIXED or item.priority == Priority.CRITICAL.value
        ),
        None,
    )
    if protected_conflict is not None:
        protection = "FIXED" if protected_conflict.flexibility == Flexibility.FIXED else "CRITICAL"
        return LocalSolveResult(
            status="REQUIRES_AI",
            conflicts=conflicts,
            reason=f"Conflict with {protection} item",
        )

    changes: List[ProposedChange] = []
    for conflict in conflicts:
        rule = resolver.get_rule(conflict.activity_id)
        if rule is None:
            return LocalSolveResult(status="REQUIRES_AI", conflicts=conflicts, reason=f"Unknown activity '{conflict.activity_id}'")

        if rule.flexibility == Flexibility.CHANGEABLE:
            change = _try_move_changeable(resolver, rule, conflict)
            if change is None:
                return LocalSolveResult(
                    status="REQUIRES_AI", conflicts=conflicts,
                    reason=f"No space on any allowed destination day for '{rule.id}'",
                )
            changes.append(change)
        elif rule.flexibility == Flexibility.FLEXIBLE:
            change = _try_shift_flexible(resolver, rule, conflict, new_start, new_end)
            if change is None:
                return LocalSolveResult(
                    status="REQUIRES_AI", conflicts=conflicts,
                    reason=f"No same-day space to shift FLEXIBLE activity '{rule.id}'",
                )
            changes.append(change)
        else:
            return LocalSolveResult(
                status="REQUIRES_AI", conflicts=conflicts,
                reason=f"'{rule.id}' ({rule.flexibility.value}) requires dropping or multi-hop reordering",
            )

    return LocalSolveResult(status="SOLVED_LOCALLY", changes=changes, conflicts=conflicts)
