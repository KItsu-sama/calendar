from datetime import date, datetime

from cal_engine import ExceptionAction, ScheduleResolver, parse_command
from cal_engine.models import CalendarException

REF = date(2026, 9, 21)  # a Monday


def _monday_rules():
    return [
        parse_command("cal --fixed school --daily --time 07:00-11:30 --except sunday", REF),
        parse_command("cal --fixed lunch --daily --time 13:00-14:00", REF),
        parse_command("cal --fixed math-class --wed --time 15:00-16:00", REF),
    ]


def test_monday_free_time_matches_spec_example():
    resolver = ScheduleResolver(rules=_monday_rules(), exceptions=[])
    free = resolver.free_time_for_day(REF)  # REF is a Monday: school + lunch only
    # Free time is computed for the full 00:00-24:00 window (not just the
    # span between named activities) so the engine also knows about
    # early-morning/late-night openings the spec's prose examples omitted.
    expected = [
        (datetime(2026, 9, 21, 0, 0), datetime(2026, 9, 21, 7, 0)),
        (datetime(2026, 9, 21, 11, 30), datetime(2026, 9, 21, 13, 0)),
        (datetime(2026, 9, 21, 14, 0), datetime(2026, 9, 22, 0, 0)),
    ]
    assert [(f.start, f.end) for f in free] == expected


def test_wednesday_free_time_includes_math_gap():
    wednesday = date(2026, 9, 23)
    resolver = ScheduleResolver(rules=_monday_rules(), exceptions=[])
    free = resolver.free_time_for_day(wednesday)
    expected = [
        (datetime(2026, 9, 23, 0, 0), datetime(2026, 9, 23, 7, 0)),
        (datetime(2026, 9, 23, 11, 30), datetime(2026, 9, 23, 13, 0)),
        (datetime(2026, 9, 23, 14, 0), datetime(2026, 9, 23, 15, 0)),
        (datetime(2026, 9, 23, 16, 0), datetime(2026, 9, 24, 0, 0)),
    ]
    assert [(f.start, f.end) for f in free] == expected


def test_sunday_school_exception_via_daily_except():
    sunday = date(2026, 9, 27)
    resolver = ScheduleResolver(rules=_monday_rules(), exceptions=[])
    schedule = resolver.build_actual_schedule(sunday, sunday)
    # school excluded on sunday via --except sunday; only lunch remains
    assert [iv.activity_id for iv in schedule] == ["lunch"]


def test_cancel_exception_removes_instance():
    tuesday = date(2026, 9, 22)
    school_cancel = CalendarException(
        id="school-cancel", rule_id="school", target_date=tuesday.isoformat(), action=ExceptionAction.CANCEL
    )
    resolver = ScheduleResolver(rules=_monday_rules(), exceptions=[school_cancel])
    schedule = resolver.build_actual_schedule(tuesday, tuesday)
    assert "school" not in [iv.activity_id for iv in schedule]
    assert "lunch" in [iv.activity_id for iv in schedule]


def test_move_exception_relocates_instance():
    # move Wednesday's math class to Saturday, keeping its time
    wednesday = date(2026, 9, 23)
    saturday = date(2026, 9, 26)
    move_exc = CalendarException(
        id="math-move",
        rule_id="math-class",
        target_date=wednesday.isoformat(),
        action=ExceptionAction.MOVE,
        new_date=saturday.isoformat(),
    )
    resolver = ScheduleResolver(rules=_monday_rules(), exceptions=[move_exc])

    wed_schedule = resolver.build_actual_schedule(wednesday, wednesday)
    assert "math-class" not in [iv.activity_id for iv in wed_schedule]

    sat_schedule = resolver.build_actual_schedule(saturday, saturday)
    math_on_sat = [iv for iv in sat_schedule if iv.activity_id == "math-class"]
    assert len(math_on_sat) == 1
    assert math_on_sat[0].start.strftime("%H:%M") == "15:00"
    assert math_on_sat[0].end.strftime("%H:%M") == "16:00"


def test_temp_fixed_single_date_only_appears_on_that_date():
    exam = parse_command("cal --temp-fixed exam --date 28/9 --time 08:00-10:00", REF)
    resolver = ScheduleResolver(rules=[exam], exceptions=[])

    on_date = date(2026, 9, 28)
    off_date = date(2026, 9, 29)
    assert [iv.activity_id for iv in resolver.build_actual_schedule(on_date, on_date)] == ["exam"]
    assert resolver.build_actual_schedule(off_date, off_date) == []


def test_temp_fixed_recurring_expires_after_until():
    extra_class = parse_command(
        "cal --temp-fixed extra-class --wed --time 15:00-16:00 --until 30/9", REF
    )
    resolver = ScheduleResolver(rules=[extra_class], exceptions=[])

    before_expiry = date(2026, 9, 23)  # Wed, within window
    after_expiry = date(2026, 10, 7)  # Wed, past --until 30/9
    assert [iv.activity_id for iv in resolver.build_actual_schedule(before_expiry, before_expiry)] == ["extra-class"]
    assert resolver.build_actual_schedule(after_expiry, after_expiry) == []


def test_flexible_only_task_not_materialized():
    # a --todo item has no fixed_interval; the resolver must not place it
    homework = parse_command("cal --todo math-homework --min 30m --max 90m --due fri 21:00", REF)
    resolver = ScheduleResolver(rules=[homework], exceptions=[])
    any_day = date(2026, 9, 25)
    assert resolver.build_actual_schedule(any_day, any_day) == []


def test_free_time_never_stored_only_derived():
    # calling free_time_for_day twice must not mutate or cache into the rules
    resolver = ScheduleResolver(rules=_monday_rules(), exceptions=[])
    first = resolver.free_time_for_day(REF)
    second = resolver.free_time_for_day(REF)
    assert first == second
    assert not any(hasattr(r, "free_time") for r in resolver.rules)
