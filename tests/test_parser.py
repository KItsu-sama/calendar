from datetime import date

import pytest

from cal_engine import (
    ActivityRule,
    CalendarException,
    CommandParseError,
    Flexibility,
    Priority,
    RecurrenceType,
    parse_command,
    validate_activity_rule,
    validate_calendar_exception,
)

REF = date(2026, 9, 23)  # a Wednesday, matches "current date" in this conversation


def test_daily_fixed_school():
    rule = parse_command(
        "cal --fixed school --daily --time 07:00-11:30 --except sunday", REF
    )
    assert isinstance(rule, ActivityRule)
    validate_activity_rule(rule)
    assert rule.flexibility == Flexibility.FIXED
    assert rule.recurrence.type == RecurrenceType.DAILY
    assert rule.recurrence.except_days == ["SUN"]
    assert rule.time_constraints.fixed_interval.start_time == "07:00"
    assert rule.time_constraints.fixed_interval.end_time == "11:30"


def test_math_class_changeable_override():
    rule = parse_command(
        "cal --fixed math-class --wed --time 15:00-16:00 --changeable --move-to sat",
        REF,
    )
    validate_activity_rule(rule)
    # --changeable overrides the --fixed default
    assert rule.flexibility == Flexibility.CHANGEABLE
    assert rule.recurrence.type == RecurrenceType.WEEKLY
    assert rule.recurrence.days_of_week == ["WED"]
    assert rule.move_options.preferred_days == ["SAT"]


def test_todo_with_deadline():
    rule = parse_command(
        "cal --todo math-homework --min 30m --max 90m --due fri 21:00", REF
    )
    validate_activity_rule(rule)
    assert rule.flexibility == Flexibility.FLEXIBLE
    assert rule.time_constraints.min_duration_minutes == 30
    assert rule.time_constraints.max_duration_minutes == 90
    # REF is Wed 2026-09-23 -> next Friday is 2026-09-25
    assert rule.time_constraints.deadline == "2026-09-25T21:00:00"


def test_optional_gaming():
    rule = parse_command("cal --optional gaming --min 30m --max 2h", REF)
    validate_activity_rule(rule)
    assert rule.flexibility == Flexibility.OPTIONAL
    assert rule.time_constraints.min_duration_minutes == 30
    assert rule.time_constraints.max_duration_minutes == 120


def test_temp_fixed_single_date_exam():
    rule = parse_command(
        "cal --temp-fixed exam --date 28/9 --time 08:00-10:00", REF
    )
    validate_activity_rule(rule)
    assert rule.flexibility == Flexibility.FIXED
    assert rule.recurrence.type == RecurrenceType.NONE
    assert rule.time_constraints.to_dict()["date"] == "2026-09-28"
    assert rule.time_constraints.fixed_interval.start_time == "08:00"


def test_temp_fixed_recurring_with_until():
    rule = parse_command(
        "cal --temp-fixed extra-class --wed --time 15:00-16:00 --until 15/10", REF
    )
    validate_activity_rule(rule)
    assert rule.recurrence.type == RecurrenceType.WEEKLY
    assert rule.recurrence.days_of_week == ["WED"]
    assert rule.recurrence.valid_until == "2026-10-15"


def test_exception_cancel():
    exc = parse_command("cal --exception school --date 30/9 --cancel", REF)
    assert isinstance(exc, CalendarException)
    validate_calendar_exception(exc)
    assert exc.rule_id == "school"
    assert exc.target_date == "2026-09-30"
    assert exc.action.value == "CANCEL"


def test_exception_move():
    exc = parse_command("cal --exception coding --date 28/9 --move sat", REF)
    validate_calendar_exception(exc)
    assert exc.action.value == "MOVE"
    # REF 2026-09-23 (Wed) -> next Saturday is 2026-09-26
    assert exc.new_date == "2026-09-26"


def test_priority_flag_overrides_default():
    rule = parse_command("cal --fixed school --daily --time 07:00-11:30 --priority critical", REF)
    assert rule.priority == Priority.CRITICAL


# --- malformed command error handling ---------------------------------

def test_missing_name_raises():
    with pytest.raises(CommandParseError):
        parse_command("cal --fixed --daily --time 07:00-11:30", REF)


def test_unknown_flag_raises():
    with pytest.raises(CommandParseError):
        parse_command("cal --fixed school --bogus-flag", REF)


def test_bad_primary_flag_raises():
    with pytest.raises(CommandParseError):
        parse_command("cal --sometimes school --time 07:00-11:30", REF)


def test_bad_time_format_raises():
    with pytest.raises(CommandParseError):
        parse_command("cal --fixed school --time 7am-11am", REF)


def test_bad_duration_format_raises():
    with pytest.raises(CommandParseError):
        parse_command("cal --optional gaming --min thirty-minutes", REF)


def test_exception_without_cancel_or_move_raises():
    with pytest.raises(CommandParseError):
        parse_command("cal --exception school --date 30/9", REF)


def test_exception_both_cancel_and_move_raises():
    with pytest.raises(CommandParseError):
        parse_command("cal --exception school --date 30/9 --cancel --move sat", REF)


def test_bad_day_name_raises():
    with pytest.raises(CommandParseError):
        parse_command("cal --fixed school --daily --time 07:00-11:30 --except funday", REF)


def test_empty_command_raises():
    with pytest.raises(CommandParseError):
        parse_command("", REF)
