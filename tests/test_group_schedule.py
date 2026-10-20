from __future__ import annotations

import json

from cal_engine.group_schedule import (
    GENERATED_PREFIX,
    export_to_calendar,
    load_config,
    save_config,
    validate_config,
)
from cal_engine.storage import CalendarState


def _explicit_test_config():
    # Test fixture only: the application itself does not supply a default timetable.
    return {
        "version": 1,
        "groups": [{
            "id": "work",
            "name": "Work",
            "priority": "HIGH",
            "flexibility": "FIXED",
            "override_policy": "overrideable",
            "description": "",
        }],
        "schedules": [{
            "id": "work_week",
            "name": "Work week",
            "group_id": "work",
            "days": ["MON"],
            "start_time": "09:00",
            "end_time": "10:00",
            "period_minutes": 60,
            "breaks": [],
            "activities": [{
                "id": "mon_p1",
                "day": "MON",
                "period": 1,
                "name": "Work block",
                "start_time": "09:00",
                "end_time": "10:00",
            }],
        }],
    }


def test_empty_configuration_is_valid_and_has_no_implicit_timetable():
    config = {"version": 1, "groups": [], "schedules": []}
    validate_config(config)
    assert config["groups"] == []
    assert config["schedules"] == []
    assert GENERATED_PREFIX == "group_schedule__"


def test_config_round_trip_and_export_preserves_existing_calendar_rules(tmp_path):
    config_path = tmp_path / "groups_schedules.json"
    calendar_path = tmp_path / "calendar.json"

    config = _explicit_test_config()
    save_config(config, config_path)
    loaded = load_config(config_path)
    assert loaded == config

    from cal_engine.parser import parse_command

    existing_rule = parse_command(
        "cal --fixed personal-study --daily --time 19:00-20:00"
    )
    state = CalendarState(rules=[existing_rule])
    state.save(calendar_path)

    exported_count = export_to_calendar(loaded, calendar_path)
    result = CalendarState.load(calendar_path)

    generated = [rule for rule in result.rules if rule.id.startswith(GENERATED_PREFIX)]
    assert exported_count == 1
    assert len(generated) == 1
    assert any(rule.id == "personal-study" for rule in result.rules)
    assert json.loads(calendar_path.read_text(encoding="utf-8"))["version"] == 1
