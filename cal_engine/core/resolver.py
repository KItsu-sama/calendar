"""
Schedule resolver: turns (rules + exceptions) into a concrete, dated
schedule, and derives free time from it on demand.

    ACTUAL SCHEDULE = BASE SCHEDULE + RECURRING RULES + TEMPORARY RULES + EXCEPTIONS

Only rules that carry a concrete `fixed_interval` are materialized onto the
calendar here — FLEXIBLE/OPTIONAL/TODO items with only min/max durations
have no fixed slot yet; placing them is the local-solver's job (Phase 3),
not the resolver's. Free time is always computed from the materialized
schedule via interval-gap arithmetic — it is never stored.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple

from .models import ActivityRule, CalendarException, ExceptionAction, Flexibility, RecurrenceType

_WEEKDAY_CODES = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]


def day_code_for(d: date) -> str:
    """Public: 3-letter day code (MON..SUN) for a date."""
    return _WEEKDAY_CODES[d.weekday()]


def next_occurrence(from_date: date, day_code: str, include_from_date: bool = True) -> date:
    """Next date on/after from_date whose weekday matches day_code."""
    target_idx = _WEEKDAY_CODES.index(day_code)
    delta = (target_idx - from_date.weekday()) % 7
    if delta == 0 and not include_from_date:
        delta = 7
    return from_date + timedelta(days=delta)


@dataclass
class Interval:
    start: datetime
    end: datetime
    activity_id: Optional[str] = None
    name: Optional[str] = None
    flexibility: Optional[Flexibility] = None
    priority: Optional[str] = None
    source_date: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "activity_id": self.activity_id,
            "name": self.name,
            "flexibility": self.flexibility.value if self.flexibility else None,
            "priority": self.priority,
        }


def _day_code(d: date) -> str:
    return _WEEKDAY_CODES[d.weekday()]


def _in_validity_window(rule: ActivityRule, current_date: date) -> bool:
    rec = rule.recurrence
    if rec.valid_from and current_date < date.fromisoformat(rec.valid_from):
        return False
    if rec.valid_until and current_date > date.fromisoformat(rec.valid_until):
        return False
    return True


def _rule_applies_on(rule: ActivityRule, current_date: date, day_code: str) -> bool:
    rec = rule.recurrence
    if not _in_validity_window(rule, current_date):
        return False
    if rec.type == RecurrenceType.DAILY:
        return day_code not in rec.except_days
    if rec.type == RecurrenceType.WEEKLY:
        return day_code in rec.days_of_week
    if rec.type == RecurrenceType.NONE:
        single_date = getattr(rule.time_constraints, "date", None)
        return single_date == current_date.isoformat()
    return False


def _window_end(start: datetime, start_time: str, end_time: str) -> datetime:
    s_h, s_m = map(int, start_time.split(":"))
    e_h, e_m = map(int, end_time.split(":"))
    end = start.replace(hour=e_h, minute=e_m)
    if (e_h, e_m) <= (s_h, s_m):
        end += timedelta(days=1)
    return end


def _find_exception(
    exceptions: List[CalendarException], rule_id: str, current_date: date
) -> Optional[CalendarException]:
    target = current_date.isoformat()
    for exc in exceptions:
        if exc.rule_id == rule_id and exc.target_date == target:
            return exc
    return None


class ScheduleResolver:
    def __init__(self, rules: List[ActivityRule], exceptions: List[CalendarException]):
        self.rules = rules
        self.exceptions = exceptions
        self._rules_by_id = {r.id: r for r in rules}
        self._exceptions_by_target: Dict[Tuple[str, str], CalendarException] = {}
        self._moves_to: Dict[str, List[Tuple[str, CalendarException]]] = defaultdict(list)
        self._reindex_exceptions()

    def _reindex_exceptions(self) -> None:
        self._exceptions_by_target.clear()
        self._moves_to.clear()
        for exc in self.exceptions:
            key = (exc.rule_id, exc.target_date)
            # Keep first to avoid silent overwrite; operations/storage should prevent duplicates.
            self._exceptions_by_target.setdefault(key, exc)
            if exc.action == ExceptionAction.MOVE and exc.new_date:
                self._moves_to[exc.new_date].append((exc.target_date, exc))

    def get_rule(self, rule_id: str) -> Optional[ActivityRule]:
        return self._rules_by_id.get(rule_id)

    def build_actual_schedule(self, start_date: date, end_date: date) -> List[Interval]:
        concrete: List[Interval] = []
        probe_start = start_date - timedelta(days=1)
        probe_end = end_date + timedelta(days=1)
        window_start = datetime.combine(start_date, datetime.min.time())
        window_end = datetime.combine(end_date + timedelta(days=1), datetime.min.time())
        current_date = probe_start

        while current_date <= probe_end:
            day_code = _day_code(current_date)

            for rule in self.rules:
                if not _rule_applies_on(rule, current_date, day_code):
                    continue

                exc = self._exceptions_by_target.get((rule.id, current_date.isoformat()))
                if exc:
                    if exc.action == ExceptionAction.CANCEL:
                        continue
                    if exc.action == ExceptionAction.MOVE:
                        # Materialized separately, on its new_date, below.
                        continue
                    if exc.action == ExceptionAction.MODIFY:
                        interval = self._interval_for(rule, current_date, source_date=current_date, override_time=exc.new_time)
                        if interval:
                            concrete.append(interval)
                        continue

                interval = self._interval_for(rule, current_date, source_date=current_date)
                if interval:
                    concrete.append(interval)

            # Exceptions that moved an instance TO this date.
            for source_target, exc in self._moves_to.get(current_date.isoformat(), []):
                if source_target == current_date.isoformat():
                    continue
                parent = self._rules_by_id.get(exc.rule_id)
                if parent is None:
                    continue
                # Destination must be inside the parent rule validity window.
                if not _in_validity_window(parent, current_date):
                    continue
                interval = self._interval_for(parent, current_date, source_date=date.fromisoformat(source_target), override_time=exc.new_time)
                if interval:
                    concrete.append(interval)

            current_date += timedelta(days=1)

        concrete = [iv for iv in concrete if iv.end > window_start and iv.start < window_end]
        concrete.sort(key=lambda i: (i.start, i.end, i.activity_id or ""))
        return concrete

    def _interval_for(
        self,
        rule: ActivityRule,
        on_date: date,
        source_date: Optional[date] = None,
        override_time=None,
    ) -> Optional[Interval]:

        if override_time is not None:
            window = override_time
        else:
            day_code = _day_code(on_date)

            window = rule.time_constraints.day_time_windows.get(day_code)

            if window is None:
                window = rule.time_constraints.fixed_interval

        if window is None:
            return None

        s_h, s_m = map(int, window.start_time.split(":"))

        start = datetime.combine(
            on_date,
            datetime.min.time().replace(
                hour=s_h,
                minute=s_m,
            ),
        )

        end = _window_end(start, window.start_time, window.end_time)

        return Interval(
            start=start,
            end=end,
            activity_id=rule.id,
            name=rule.name,
            flexibility=rule.flexibility,
            priority=rule.priority.value if rule.priority else None,
            source_date=(source_date or on_date).isoformat(),
        )

    @staticmethod
    def calculate_free_time(
        actual_schedule: List[Interval], start_window: datetime, end_window: datetime
    ) -> List[Interval]:
        """Free time is always derived, never stored. Only intervals that
        overlap [start_window, end_window) are considered."""
        relevant = sorted(
            (iv for iv in actual_schedule if iv.end > start_window and iv.start < end_window),
            key=lambda i: i.start,
        )
        free: List[Interval] = []
        cursor = start_window
        for item in relevant:
            item_start = max(item.start, start_window)
            item_end = min(item.end, end_window)
            if item_start > cursor:
                free.append(Interval(start=cursor, end=item_start, activity_id="FREE"))
            cursor = max(cursor, item_end)
        if cursor < end_window:
            free.append(Interval(start=cursor, end=end_window, activity_id="FREE"))
        return free

    def free_time_for_day(self, day: date) -> List[Interval]:
        """Convenience: free time for a single calendar day (00:00-24:00)."""
        schedule = self.build_actual_schedule(day, day)
        start_window = datetime.combine(day, datetime.min.time())
        end_window = start_window + timedelta(days=1)
        return self.calculate_free_time(schedule, start_window, end_window)
