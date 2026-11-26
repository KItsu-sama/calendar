from __future__ import annotations

import copy
import json

from cal_engine.core.models import (
    ActivityRule,
    CalendarException,
    ExceptionAction,
    Flexibility,
    Priority,
    Recurrence,
    RecurrenceType,
    TimeConstraints,
    TimeWindow,
)
from cal_engine.core.parser import parse_command
from cal_engine.core.storage import CalendarState
from cal_engine.groups.group_schedule import (
    GENERATED_PREFIX,
    LEGACY_PREFIX,
    apply_timetables,
    build_timetable_rules,
    export_to_calendar,
    generated_rule_id,
    load_config,
    lookup_generated_block,
    save_config,
    upgrade_config,
    validate_config,
)


def _activity(aid, day, name, start, end, period=1, priority=None, groups=None, **extra):
    item = {
        "id": aid,
        "day": day,
        "period": period,
        "name": name,
        "start_time": start,
        "end_time": end,
        "group_ids": list(groups or []),
    }
    if priority:
        item["priority"] = priority
    item.update(extra)
    return item


def _config(activities, breaks=None, days=None):
    return {
        "version": 2,
        "groups": [{"id": "school", "name": "School", "description": ""}],
        "timetables": [{
            "id": "school_week",
            "name": "School week",
            "description": "",
            "place_tag": "",
            "days": days or ["MON", "TUE", "WED", "THU", "FRI"],
            "start_time": "09:00",
            "end_time": "12:00",
            "period_minutes": 60,
            "breaks": breaks or [],
            "activities": activities,
        }],
    }


def _manual_rule():
    return parse_command("cal --fixed personal-study --daily --time 19:00-20:00")


def test_empty_configuration_is_valid_and_has_no_implicit_timetable():
    config = {"version": 2, "groups": [], "timetables": []}
    validate_config(config)
    assert config["timetables"] == []
    assert GENERATED_PREFIX == "timetable__"


def test_v1_config_upgrades_on_load(tmp_path):
    path = tmp_path / "groups_schedules.json"
    path.write_text(json.dumps({
        "version": 1,
        "groups": [{"id": "work", "name": "Work", "priority": "HIGH",
                    "flexibility": "FIXED", "override_policy": "overrideable", "description": ""}],
        "schedules": [{
            "id": "work_week",
            "name": "Work week",
            "group_id": "work",
            "days": ["MON"],
            "start_time": "09:00",
            "end_time": "10:00",
            "period_minutes": 60,
            "breaks": [],
            "activities": [_activity("mon_p1", "MON", "Work block", "09:00", "10:00")],
        }],
    }), encoding="utf-8")
    loaded = load_config(path)
    assert loaded["version"] == 2
    assert loaded["timetables"][0]["id"] == "work_week"
    assert loaded["timetables"][0]["activities"][0]["group_ids"] == ["work"]
    save_config(loaded, path)
    assert json.loads(path.read_text(encoding="utf-8"))["version"] == 2


def test_config_round_trip_and_export_preserves_existing_calendar_rules(tmp_path):
    config_path = tmp_path / "groups_schedules.json"
    calendar_path = tmp_path / "data" / "calendar.json"
    config = _config([_activity("mon_p1", "MON", "Work block", "09:00", "10:00")])
    save_config(config, config_path)
    loaded = load_config(config_path)
    assert loaded == config

    state = CalendarState(rules=[_manual_rule()])
    state.save(calendar_path)
    count = export_to_calendar(loaded, calendar_path)
    result = CalendarState.load(calendar_path)
    generated = [rule for rule in result.rules if rule.id.startswith(GENERATED_PREFIX)]
    assert count == 1
    assert len(generated) == 1
    assert any(rule.id == "personal-study" for rule in result.rules)
    assert json.loads(calendar_path.read_text(encoding="utf-8"))["version"] == 1


def test_identical_weekday_slots_merge_into_one_weekly_rule():
    config = _config([
        _activity("mon_p1", "MON", "Math", "09:00", "10:00", groups=["school"]),
        _activity("wed_p1", "WED", "Math", "09:00", "10:00", groups=["school"]),
        _activity("fri_p1", "FRI", "Math", "09:00", "10:00", groups=["school"]),
    ])
    rules, _ = build_timetable_rules(config)
    math = [r for r in rules if r.name == "Math"]
    assert len(math) == 1
    assert math[0].id == generated_rule_id("school_week", "mon_p1")
    assert math[0].recurrence.days_of_week == ["MON", "WED", "FRI"]
    assert math[0].time_constraints.fixed_interval.start_time == "09:00"


def test_same_subject_different_times_stay_separate():
    config = _config([
        _activity("mon_p1", "MON", "Math", "09:00", "10:00"),
        _activity("wed_p1", "WED", "Math", "11:00", "12:00"),
    ])
    rules, _ = build_timetable_rules(config)
    math = [r for r in rules if r.name == "Math"]
    assert len(math) == 2
    assert {tuple(r.recurrence.days_of_week) for r in math} == {("MON",), ("WED",)}
    starts = {r.time_constraints.fixed_interval.start_time for r in math}
    assert starts == {"09:00", "11:00"}


def test_repeated_export_is_idempotent(tmp_path):
    calendar_path = tmp_path / "calendar.json"
    config = _config([
        _activity("mon_p1", "MON", "Math", "09:00", "10:00"),
        _activity("wed_p1", "WED", "Math", "09:00", "10:00"),
    ])
    CalendarState(rules=[_manual_rule()]).save(calendar_path)
    export_to_calendar(config, calendar_path)
    first = CalendarState.load(calendar_path).to_dict()
    export_to_calendar(config, calendar_path)
    export_to_calendar(config, calendar_path)
    assert CalendarState.load(calendar_path).to_dict() == first
    ids = [rule["id"] for rule in first["rules"]]
    assert len(ids) == len(set(ids))


def test_timetable_edits_and_deletions_update_generated_rules():
    config = _config([
        _activity("mon_p1", "MON", "Math", "09:00", "10:00"),
        _activity("wed_p1", "WED", "Math", "09:00", "10:00"),
    ])
    state = CalendarState()
    apply_timetables(config, state)
    math_id = generated_rule_id("school_week", "mon_p1")
    assert state.get_rule(math_id).time_constraints.fixed_interval.end_time == "10:00"

    edited = copy.deepcopy(config)
    for act in edited["timetables"][0]["activities"]:
        act["end_time"] = "10:30"
    apply_timetables(edited, state)
    assert state.get_rule(math_id).id == math_id
    assert state.get_rule(math_id).time_constraints.fixed_interval.end_time == "10:30"

    edited["timetables"][0]["activities"] = [
        a for a in edited["timetables"][0]["activities"] if a["id"] != "wed_p1"
    ]
    apply_timetables(edited, state)
    assert state.get_rule(math_id).recurrence.days_of_week == ["MON"]

    apply_timetables({"version": 2, "groups": [], "timetables": []}, state)
    assert state.rules == []


def test_manual_events_survive_timetable_export():
    config = _config([_activity("mon_p1", "MON", "Math", "09:00", "10:00")])
    state = CalendarState(rules=[_manual_rule()])
    apply_timetables(config, state)
    apply_timetables(config, state)
    assert state.get_rule("personal-study") is not None
    assert len([r for r in state.rules if r.id == "personal-study"]) == 1


def test_exceptions_survive_unrelated_exports():
    config = _config([
        _activity("mon_p1", "MON", "Math", "09:00", "10:00"),
        _activity("wed_p1", "WED", "Math", "09:00", "10:00"),
    ])
    math_id = generated_rule_id("school_week", "mon_p1")
    state = CalendarState(
        rules=[_manual_rule()],
        exceptions=[
            CalendarException(id="ex-study", rule_id="personal-study",
                              target_date="2026-11-09", action=ExceptionAction.CANCEL),
        ],
    )
    apply_timetables(config, state)
    state.add_exception(CalendarException(
        id="ex-math", rule_id=math_id, target_date="2026-11-11", action=ExceptionAction.CANCEL,
    ))
    apply_timetables(config, state)
    assert {e.id for e in state.exceptions} == {"ex-study", "ex-math"}
    assert state.get_rule("personal-study") is not None
    math_exc = next(e for e in state.exceptions if e.id == "ex-math")
    assert math_exc.rule_id == math_id


def test_existing_calendar_migration_rewrites_legacy_and_per_day_ids():
    config = _config(
        [
            _activity("mon_p1", "MON", "Math", "09:00", "10:00"),
            _activity("wed_p1", "WED", "Math", "09:00", "10:00"),
        ],
        breaks=[{
            "id": "break_1", "name": "Break", "start_time": "10:00", "end_time": "10:15",
            "priority": "HIGH",
        }],
        days=["MON", "WED"],
    )
    legacy_mon = f"{LEGACY_PREFIX}school_week__mon_p1"
    legacy_wed = f"{GENERATED_PREFIX}school_week__wed_p1"
    legacy_break = f"{GENERATED_PREFIX}school_week__break_1__MON"
    state = CalendarState(
        rules=[
            _manual_rule(),
            ActivityRule(
                id=legacy_mon, name="Math (MON P1)", flexibility=Flexibility.FIXED,
                priority=Priority.NORMAL,
                recurrence=Recurrence(type=RecurrenceType.WEEKLY, days_of_week=["MON"]),
                time_constraints=TimeConstraints(fixed_interval=TimeWindow("09:00", "10:00")),
            ),
            ActivityRule(
                id=legacy_wed, name="Math (WED P1)", flexibility=Flexibility.FIXED,
                priority=Priority.NORMAL,
                recurrence=Recurrence(type=RecurrenceType.WEEKLY, days_of_week=["WED"]),
                time_constraints=TimeConstraints(fixed_interval=TimeWindow("09:00", "10:00")),
            ),
            ActivityRule(
                id=legacy_break, name="Break (MON)", flexibility=Flexibility.FIXED,
                priority=Priority.HIGH,
                recurrence=Recurrence(type=RecurrenceType.WEEKLY, days_of_week=["MON"]),
                time_constraints=TimeConstraints(fixed_interval=TimeWindow("10:00", "10:15")),
            ),
        ],
        exceptions=[
            CalendarException(id="ex-wed", rule_id=legacy_wed,
                              target_date="2026-11-11", action=ExceptionAction.CANCEL),
            CalendarException(id="ex-break", rule_id=legacy_break,
                              target_date="2026-11-09", action=ExceptionAction.CANCEL),
            CalendarException(id="ex-study", rule_id="personal-study",
                              target_date="2026-11-10", action=ExceptionAction.CANCEL),
        ],
    )
    apply_timetables(config, state)
    math_id = generated_rule_id("school_week", "mon_p1")
    break_id = generated_rule_id("school_week", "break_1")
    assert {r.id for r in state.rules} == {"personal-study", math_id, break_id}
    assert state.get_rule(math_id).recurrence.days_of_week == ["MON", "WED"]
    assert state.get_rule(break_id).recurrence.days_of_week == ["MON", "WED"]
    by_id = {e.id: e for e in state.exceptions}
    assert by_id["ex-wed"].rule_id == math_id
    assert by_id["ex-break"].rule_id == break_id
    assert by_id["ex-study"].rule_id == "personal-study"
    assert legacy_mon not in {r.id for r in state.rules}


def test_deleted_slot_drops_only_its_generated_exception():
    config = _config([
        _activity("mon_p1", "MON", "Math", "09:00", "10:00"),
        _activity("tue_p1", "TUE", "History", "11:00", "12:00"),
    ])
    state = CalendarState()
    apply_timetables(config, state)
    math_id = generated_rule_id("school_week", "mon_p1")
    history_id = generated_rule_id("school_week", "tue_p1")
    state.add_exception(CalendarException(
        id="ex-math", rule_id=math_id, target_date="2026-11-09", action=ExceptionAction.CANCEL,
    ))
    state.add_exception(CalendarException(
        id="ex-hist", rule_id=history_id, target_date="2026-11-10", action=ExceptionAction.CANCEL,
    ))
    config["timetables"][0]["activities"] = [
        a for a in config["timetables"][0]["activities"] if a["id"] != "mon_p1"
    ]
    apply_timetables(config, state)
    assert state.get_rule(math_id) is None
    assert state.get_rule(history_id) is not None
    assert [e.id for e in state.exceptions] == ["ex-hist"]


def test_duplicate_generated_ids_are_not_created():
    config = _config([
        _activity("mon_p1", "MON", "Math", "09:00", "10:00"),
        _activity("wed_p1", "WED", "Math", "09:00", "10:00"),
    ])
    state = CalendarState()
    apply_timetables(config, state)
    apply_timetables(config, state)
    ids = [r.id for r in state.rules]
    assert len(ids) == len(set(ids))
    assert ids.count(generated_rule_id("school_week", "mon_p1")) == 1


def test_lookup_merged_rule_selects_the_weekday_slot():
    config = _config([
        _activity("mon_p1", "MON", "Math", "09:00", "10:00"),
        _activity("wed_p1", "WED", "Math", "09:00", "10:00"),
    ])
    rule_id = generated_rule_id("school_week", "mon_p1")
    _, monday, _ = lookup_generated_block(rule_id, config, "MON")
    _, wednesday, _ = lookup_generated_block(rule_id, config, "WED")
    assert monday["id"] == "mon_p1"
    assert wednesday["id"] == "wed_p1"


def test_upgrade_helper_leaves_v2_unchanged():
    config = _config([_activity("mon_p1", "MON", "Math", "09:00", "10:00")])
    assert upgrade_config(config) is config
