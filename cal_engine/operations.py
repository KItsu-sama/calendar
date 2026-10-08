"""Conflict-safe direct-manipulation operations for Phase 6."""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import List, Optional

from .conflict import ProposedChange, resolve_conflict_locally
from .models import (
    ActivityRule,
    CalendarException,
    ExceptionAction,
    Flexibility,
    Priority,
    RecurrenceType,
    TimeWindow,
)
from .resolver import Interval, day_code_for
from .storage import CalendarState


class OperationError(ValueError):
    """A requested state change cannot be applied safely."""


def _time_value(day: date, value: str) -> datetime:
    hour, minute = map(int, value.split(":"))
    return datetime.combine(day, datetime.min.time().replace(hour=hour, minute=minute))


def _window_interval(window: TimeWindow, day: date) -> tuple[datetime, datetime]:
    start = _time_value(day, window.start_time)
    end = _time_value(day, window.end_time)
    if end <= start:
        raise OperationError(f"Time range must end after it starts: {window.start_time}-{window.end_time}.")
    return start, end

def _has_concrete_time(rule: ActivityRule) -> bool:
    return (
        rule.time_constraints.fixed_interval is not None
        or bool(rule.time_constraints.day_time_windows)
    )


def _rule_occurs(rule: ActivityRule, target: date) -> bool:
    recurrence = rule.recurrence
    if recurrence.valid_from and target < date.fromisoformat(recurrence.valid_from):
        return False
    if recurrence.valid_until and target > date.fromisoformat(recurrence.valid_until):
        return False
    if recurrence.type == RecurrenceType.DAILY:
        return day_code_for(target) not in recurrence.except_days
    if recurrence.type == RecurrenceType.WEEKLY:
        return day_code_for(target) in recurrence.days_of_week
    return getattr(rule.time_constraints, "date", None) == target.isoformat()


def first_occurrence(
    rule: ActivityRule, reference_date: date, requested_date: Optional[date] = None
) -> date:
    """Find the requested or next occurrence within a deterministic one-year horizon."""
    if requested_date is not None:
        if not _rule_occurs(rule, requested_date):
            raise OperationError(f"'{rule.name}' does not occur on {requested_date.isoformat()}.")
        return requested_date
    for offset in range(367):
        candidate = reference_date + timedelta(days=offset)
        if _rule_occurs(rule, candidate):
            return candidate
    raise OperationError(f"Could not find an occurrence of '{rule.name}' within one year.")


def _interval_on(state: CalendarState, activity_id: str, target: date) -> Interval:
    matches = [
        item
        for item in state.resolver().build_actual_schedule(target, target)
        if item.activity_id == activity_id
    ]
    if len(matches) != 1:
        raise OperationError(
            f"Expected one occurrence of '{activity_id}' on {target.isoformat()}; found {len(matches)}."
        )
    return matches[0]


def _without_instance_exceptions(state: CalendarState, rule_id: str, target: date) -> None:
    target_text = target.isoformat()
    state.exceptions = [
        item
        for item in state.exceptions
        if not (item.rule_id == rule_id and item.target_date == target_text)
    ]


def is_protected(rule: ActivityRule) -> bool:
    return rule.flexibility == Flexibility.FIXED or rule.priority == Priority.CRITICAL


def _validate_rule_constraints(rule: ActivityRule, start: datetime, end: datetime) -> None:
    allowed_days = rule.time_constraints.allowed_days
    if allowed_days and day_code_for(start.date()) not in allowed_days:
        raise OperationError(f"'{rule.id}' is not allowed on {day_code_for(start.date())}.")
    windows = rule.time_constraints.allowed_windows
    if windows and not any(
        _time_value(start.date(), window.start_time) <= start
        and end <= _time_value(start.date(), window.end_time)
        for window in windows
    ):
        raise OperationError(f"New time for '{rule.id}' falls outside its allowed windows.")
    if rule.time_constraints.deadline and end > datetime.fromisoformat(rule.time_constraints.deadline):
        raise OperationError(f"New time for '{rule.id}' violates its deadline.")


def _validate_reschedule(
    rule: ActivityRule,
    original: Interval,
    start: datetime,
    end: datetime,
    preserve_duration: bool = True,
) -> None:
    if end <= start or start.date() != end.date():
        raise OperationError("A rescheduled activity must start and end on the same day in chronological order.")
    if preserve_duration and end - start != original.end - original.start:
        raise OperationError(f"Cannot change the duration of '{rule.id}' implicitly.")
    if start.date() != original.start.date():
        if rule.flexibility == Flexibility.CHANGEABLE and (
            not rule.move_options or day_code_for(start.date()) not in rule.move_options.preferred_days
        ):
            raise OperationError(f"'{rule.id}' cannot move to {day_code_for(start.date())}.")
        if rule.flexibility == Flexibility.FLEXIBLE:
            raise OperationError(f"Flexible activity '{rule.id}' cannot move to another day.")
    _validate_rule_constraints(rule, start, end)


def parse_ai_datetime(value: object, field_name: str) -> datetime:
    if not isinstance(value, str):
        raise OperationError(f"'{field_name}' must be a non-null YYYY-MM-DD HH:MM value for this action.")
    try:
        return datetime.strptime(value.replace("T", " "), "%Y-%m-%d %H:%M")
    except ValueError:
        raise OperationError(f"Invalid {field_name} '{value}'; expected YYYY-MM-DD HH:MM.") from None


def proposed_change_to_dict(change: ProposedChange) -> dict:
    return {
        "activity_id": change.activity_id,
        "action": change.action,
        "new_start": change.new_start.strftime("%Y-%m-%d %H:%M"),
        "new_end": change.new_end.strftime("%Y-%m-%d %H:%M"),
        "reasoning": change.reasoning,
    }


@dataclass
class OperationResult:
    state: CalendarState
    status: str
    action: str
    activity_id: str
    conflicts: List[Interval] = field(default_factory=list)
    changes: List[ProposedChange] = field(default_factory=list)
    reason: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "action": self.action,
            "activity_id": self.activity_id,
            "conflicts": [item.to_dict() for item in self.conflicts],
            "changes": [proposed_change_to_dict(item) for item in self.changes],
            "reason": self.reason,
        }


def _result(
    state: CalendarState,
    status: str,
    action: str,
    activity_id: str,
    local=None,
) -> OperationResult:
    return OperationResult(
        state=state,
        status=status,
        action=action,
        activity_id=activity_id,
        conflicts=list(local.conflicts) if local else [],
        changes=list(local.changes) if local else [],
        reason=local.reason if local else None,
    )


def _apply_one_change(state: CalendarState, change: dict, source_date: date) -> None:
    activity_id = change.get("activity_id")
    action = str(change.get("action", "")).upper()
    if action not in {"MOVE", "SHIFT", "RESCHEDULE", "REMOVE"}:
        raise OperationError(f"Unsupported schedule-change action '{action}'.")
    rule = state.require_rule(str(activity_id))
    original = _interval_on(state, rule.id, source_date)
    if is_protected(rule):
        raise OperationError(
            f"Resolution cannot alter protected activity '{rule.id}' "
            f"({rule.flexibility.value}/{rule.priority.value})."
        )
    if action == "REMOVE":
        if change.get("new_start") is not None or change.get("new_end") is not None:
            raise OperationError("REMOVE changes must have null new_start and new_end.")
        _without_instance_exceptions(state, rule.id, source_date)
        state.add_exception(
            CalendarException(
                id=f"{rule.id}-{source_date.isoformat()}",
                rule_id=rule.id,
                target_date=source_date.isoformat(),
                action=ExceptionAction.CANCEL,
            )
        )
        return

    start = parse_ai_datetime(change.get("new_start"), "new_start")
    end = parse_ai_datetime(change.get("new_end"), "new_end")
    _validate_reschedule(rule, original, start, end)
    _without_instance_exceptions(state, rule.id, source_date)
    if rule.recurrence.type == RecurrenceType.NONE:
        updated = copy.deepcopy(rule)
        updated.time_constraints.fixed_interval = TimeWindow(
            start_time=start.strftime("%H:%M"), end_time=end.strftime("%H:%M")
        )
        if hasattr(updated.time_constraints, "date"):
            updated.time_constraints.date = start.date().isoformat()
        state.replace_rule(updated)
        return
    state.add_exception(
        CalendarException(
            id=f"{rule.id}-{source_date.isoformat()}",
            rule_id=rule.id,
            target_date=source_date.isoformat(),
            action=ExceptionAction.MOVE if start.date() != source_date else ExceptionAction.MODIFY,
            new_date=start.date().isoformat(),
            new_time=TimeWindow(start_time=start.strftime("%H:%M"), end_time=end.strftime("%H:%M")),
        )
    )


def apply_schedule_changes(state: CalendarState, changes, source_date: date) -> CalendarState:
    """Apply validated LLM/manual changes to one requested occurrence date."""
    updated = state.copy()
    for change in changes:
        _apply_one_change(updated, change, source_date)
    return updated


def assert_no_overlaps(state: CalendarState, target_dates) -> None:
    """Reject a candidate state if any requested date contains overlapping intervals."""
    for target in sorted(set(target_dates)):
        intervals = sorted(state.resolver().build_actual_schedule(target, target), key=lambda item: item.start)
        for index, left in enumerate(intervals):
            for right in intervals[index + 1 :]:
                if right.start >= left.end:
                    break
                if left.start < right.end and left.end > right.start:
                    raise OperationError(
                        f"Result overlaps '{left.activity_id}' and '{right.activity_id}' "
                        f"on {target.isoformat()}."
                    )


def _as_change_dict(change: ProposedChange) -> dict:
    return proposed_change_to_dict(change)


def _insert_resolved_activity(
    state: CalendarState,
    rule: ActivityRule,
    on_date: date,
    action: str,
) -> OperationResult:
    if not _has_concrete_time(rule):
        candidate = state.copy()
        candidate.add_rule(rule)
        return _result(candidate, "ADDED_UNPLACED", action, rule.id)
    start, end = _window_interval(rule.time_constraints.fixed_interval, on_date)
    _validate_rule_constraints(rule, start, end)
    local = resolve_conflict_locally(state.resolver(), rule, on_date)
    if local.status == "REQUIRES_AI":
        return _result(state.copy(), local.status, action, rule.id, local)
    candidate = state.copy()
    if local.changes:
        candidate = apply_schedule_changes(
            candidate, [_as_change_dict(change) for change in local.changes], on_date
        )
    candidate.add_rule(rule)
    affected_dates = [on_date] + [change.new_start.date() for change in local.changes]
    assert_no_overlaps(candidate, affected_dates)
    return _result(candidate, local.status, action, rule.id, local)


def add_activity(
    state: CalendarState,
    rule: ActivityRule,
    reference_date: date,
    target_date: Optional[date] = None,
) -> OperationResult:
    if state.get_rule(rule.id) is not None:
        raise OperationError(f"Activity '{rule.id}' already exists.")
    effective_date = target_date or (
        first_occurrence(rule, reference_date)
        if rule.time_constraints.fixed_interval
        else reference_date
    )
    return _insert_resolved_activity(state, rule, effective_date, "ADD")


def change_activity(
    state: CalendarState,
    updated_rule: ActivityRule,
    reference_date: date,
    target_date: Optional[date] = None,
) -> OperationResult:
    state.require_rule(updated_rule.id)
    base = state.copy()
    base.remove_rule(updated_rule.id)
    effective_date = (
        first_occurrence(updated_rule, reference_date, target_date)
        if updated_rule.time_constraints.fixed_interval is not None
        else (target_date or reference_date)
    )
    return _insert_resolved_activity(base, updated_rule, effective_date, "CHANGE")


def remove_activity(state: CalendarState, activity_id: str) -> OperationResult:
    candidate = state.copy()
    candidate.remove_rule(activity_id)
    return _result(candidate, "REMOVED", "REMOVE", activity_id)



def create_exception(
    state: CalendarState,
    activity_id: str,
    action: ExceptionAction,
    source_date: date,
    new_date: Optional[date] = None,
    new_time: Optional[TimeWindow] = None,
) -> OperationResult:
    rule = state.require_rule(activity_id)
    original = _interval_on(state, activity_id, source_date)
    base = state.copy()
    _without_instance_exceptions(base, activity_id, source_date)

    if action == ExceptionAction.CANCEL:
        base.add_exception(
            CalendarException(
                id=f"{activity_id}-{source_date.isoformat()}",
                rule_id=activity_id,
                target_date=source_date.isoformat(),
                action=ExceptionAction.CANCEL,
            )
        )
        assert_no_overlaps(base, [source_date])
        return _result(base, "CANCELLED", "CANCEL", activity_id)

    target_date = new_date or source_date
    window = new_time or TimeWindow(
        start_time=original.start.strftime("%H:%M"), end_time=original.end.strftime("%H:%M")
    )
    start, end = _window_interval(window, target_date)
    _validate_reschedule(rule, original, start, end, preserve_duration=False)

    # Hide the source occurrence while the local solver evaluates the destination.
    temporary = base.copy()
    temporary.add_exception(
        CalendarException(
            id=f"{activity_id}-source",
            rule_id=activity_id,
            target_date=source_date.isoformat(),
            action=ExceptionAction.CANCEL,
        )
    )
    candidate_rule = copy.deepcopy(rule)
    candidate_rule.time_constraints.fixed_interval = window
    local = resolve_conflict_locally(temporary.resolver(), candidate_rule, target_date)
    if local.status == "REQUIRES_AI":
        return _result(state.copy(), local.status, action.value, activity_id, local)

    candidate = base
    if local.changes:
        candidate = apply_schedule_changes(
            candidate, [_as_change_dict(change) for change in local.changes], source_date
        )
    candidate.add_exception(
        CalendarException(
            id=f"{activity_id}-{source_date.isoformat()}",
            rule_id=activity_id,
            target_date=source_date.isoformat(),
            action=ExceptionAction.MODIFY if target_date == source_date else ExceptionAction.MOVE,
            new_date=target_date.isoformat(),
            new_time=window,
        )
    )
    assert_no_overlaps(candidate, [source_date, target_date])
    return _result(candidate, local.status, action.value, activity_id, local)


def preview_activity(state: CalendarState, rule: ActivityRule, on_date: date) -> OperationResult:
    if not _has_concrete_time(rule):
        return _result(state.copy(), "UNPLACED", "PREVIEW", rule.id)
    local = resolve_conflict_locally(state.resolver(), rule, on_date)
    return _result(state.copy(), local.status, "PREVIEW", rule.id, local)


def conflicts_in_schedule(state: CalendarState, start: date, end: date) -> list[dict]:
    intervals = sorted(state.resolver().build_actual_schedule(start, end), key=lambda item: item.start)
    conflicts = []
    for index, left in enumerate(intervals):
        for right in intervals[index + 1 :]:
            if right.start >= left.end:
                break
            if left.start < right.end and left.end > right.start:
                conflicts.append(
                    {
                        "left": left.to_dict(),
                        "right": right.to_dict(),
                        "overlap_start": max(left.start, right.start).isoformat(),
                        "overlap_end": min(left.end, right.end).isoformat(),
                    }
                )
    return conflicts

