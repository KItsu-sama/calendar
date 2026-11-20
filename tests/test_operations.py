import pytest
from datetime import date

from cal_engine import (
    CalendarState,
    Flexibility,
    OperationError,
    add_activity,
    apply_schedule_changes,
    change_activity,
    create_exception,
    parse_command,
    resolve_conflict_locally,
)
from cal_engine.core.models import ExceptionAction

REF = date(2026, 9, 21)
WED = date(2026, 9, 23)
SAT = date(2026, 9, 26)


def _state():
    state = CalendarState()
    for command in [
        "cal --fixed school --daily --time 07:00-11:30 --except sunday",
        "cal --fixed lunch --daily --time 13:00-14:00",
        "cal --fixed math --wed --time 15:00-16:00 --changeable --move-to sat",
    ]:
        state.add_rule(parse_command(command, REF))
    return state


def test_state_json_round_trip_preserves_one_off_date(tmp_path):
    state = CalendarState()
    state.add_rule(parse_command("cal --temp-fixed exam --date 28/9 --time 08:00-10:00", REF))
    path = tmp_path / "state.json"
    state.save(path)
    loaded = CalendarState.load(path)
    assert loaded.to_dict() == state.to_dict()
    assert loaded.rules[0].time_constraints.date == "2026-09-28"


def test_move_math_to_saturday_recalculates_schedule():
    result = create_exception(_state(), "math", ExceptionAction.MOVE, WED, SAT)
    assert result.status == "DIRECT_ADD"
    assert [item.activity_id for item in result.state.resolver().build_actual_schedule(WED, WED)] == [
        "school",
        "lunch",
    ]
    saturday = result.state.resolver().build_actual_schedule(SAT, SAT)
    assert [(item.activity_id, item.start.strftime("%H:%M")) for item in saturday] == [
        ("school", "07:00"),
        ("lunch", "13:00"),
        ("math", "15:00"),
    ]


def test_change_school_recalculates_free_time():
    updated = parse_command("cal --fixed school --daily --time 07:30-12:00", REF)
    result = change_activity(_state(), updated, REF, REF)
    free = result.state.resolver().free_time_for_day(REF)
    assert [(item.start.strftime("%H:%M"), item.end.strftime("%H:%M")) for item in free] == [
        ("00:00", "07:30"),
        ("12:00", "13:00"),
        ("14:00", "00:00"),
    ]


def test_cancel_and_remove_recalculate_state():
    cancelled = create_exception(_state(), "math", ExceptionAction.CANCEL, WED)
    assert cancelled.status == "CANCELLED"
    assert "math" not in [item.activity_id for item in cancelled.state.resolver().build_actual_schedule(WED, WED)]
    removed = create_exception(_state(), "lunch", ExceptionAction.CANCEL, REF)
    assert removed.state.resolver().free_time_for_day(REF)


def test_critical_activity_escalates_even_when_flexible():
    state = CalendarState()
    critical = parse_command(
        "cal --todo critical-task --wed --time 10:00-11:00 --priority critical", REF
    )
    state.add_rule(critical)
    assert critical.flexibility == Flexibility.FLEXIBLE
    incoming = parse_command("cal --fixed meeting --wed --time 10:30-11:30", REF)
    result = resolve_conflict_locally(state.resolver(), incoming, WED)
    assert result.status == "REQUIRES_AI"
    assert "CRITICAL" in result.reason


def test_resolution_change_cannot_remove_fixed_activity():
    change = {
        "activity_id": "school",
        "action": "REMOVE",
        "new_start": None,
        "new_end": None,
        "reasoning": "invalid",
    }
    with pytest.raises(OperationError, match="protected"):
        apply_schedule_changes(_state(), [change], REF)


def test_complex_add_requires_ai_without_mutating_input():
    state = _state()
    state.add_rule(parse_command("cal --optional gaming --wed --time 16:00-17:00", REF))
    before = state.to_dict()
    incoming = parse_command("cal --fixed exam-review --wed --time 15:00-18:00", REF)
    result = add_activity(state, incoming, REF, WED)
    assert result.status == "REQUIRES_AI"
    assert result.state.to_dict() == before


def test_duplicate_exception_for_same_source_is_rejected():
    state = _state()
    cancelled = create_exception(state, "math", ExceptionAction.CANCEL, WED)
    assert cancelled.status == "CANCELLED"
    with pytest.raises(OperationError, match="already exists"):
        create_exception(cancelled.state, "math", ExceptionAction.MOVE, WED, SAT)


def test_move_outside_validity_rejected():
    state = CalendarState()
    state.add_rule(parse_command("cal --temp-fixed extra --wed --time 15:00-16:00 --until 30/9", REF))
    after = date(2026, 10, 7)
    with pytest.raises(OperationError, match="validity"):
        create_exception(state, "extra", ExceptionAction.MOVE, WED, after)


def test_move_duplicate_same_rule_occurrence_rejected():
    state = CalendarState()
    state.add_rule(parse_command("cal --fixed class --daily --time 15:00-16:00", REF))
    tuesday = date(2026, 9, 22)
    # Move Wednesday's class onto Tuesday at same time -> duplicates Tuesday's class instance
    with pytest.raises(OperationError, match="duplicate"):
        create_exception(state, "class", ExceptionAction.MOVE, WED, tuesday)


def test_two_source_instances_moved_to_same_destination_duplicate_rejected():
    state = CalendarState()
    state.add_rule(parse_command("cal --fixed class --days wed thu --time 15:00-16:00", REF))
    fri = date(2026, 9, 25)
    thu = date(2026, 9, 24)
    first = create_exception(state, "class", ExceptionAction.MOVE, WED, fri)
    assert first.status in {"DIRECT_ADD", "SOLVED_LOCALLY"}
    with pytest.raises(OperationError, match="duplicate"):
        create_exception(first.state, "class", ExceptionAction.MOVE, thu, fri)


def test_overnight_modify_preserved():
    state = CalendarState()
    state.add_rule(parse_command("cal --fixed shift --wed --time 22:00-23:00", REF))
    from cal_engine.core.models import TimeWindow
    result = create_exception(state, "shift", ExceptionAction.MODIFY, WED, WED, TimeWindow("22:00", "02:00"))
    assert result.status == "DIRECT_ADD"
    hits = [iv for iv in result.state.resolver().build_actual_schedule(WED, WED) if iv.activity_id == "shift"]
    assert len(hits) == 1
    assert hits[0].end.isoformat() == "2026-09-24T02:00:00"

