from __future__ import annotations

import json

from cal_engine.group_schedule import (
    GENERATED_PREFIX,
    default_config,
    export_to_calendar,
    load_config,
    save_config,
    validate_config,
)
from cal_engine.storage import CalendarState


def test_default_config_is_built_and_validated_from_calendar_commands():
    config = default_config()

    validate_config(config)
    schedule = config["schedules"][0]

    assert len(schedule["activities"]) == 25
    assert schedule["days"] == ["MON", "TUE", "WED", "THU", "FRI"]
    assert schedule["activities"][0] == {
        "id": "mon_p1",
        "day": "MON",
        "period": 1,
        "name": "CHAOCO",
        "start_time": "07:00",
        "end_time": "07:45",
    }
    assert GENERATED_PREFIX == "group_schedule__"


def test_config_round_trip_and_export_preserves_existing_calendar_rules(tmp_path):
    config_path = tmp_path / "groups_schedules.json"
    calendar_path = tmp_path / "calendar.json"

    config = default_config()
    save_config(config, config_path)
    loaded = load_config(config_path)
    assert loaded == config

    # A pre-existing ordinary rule must remain after timetable export.
    from cal_engine.parser import parse_command

    existing_rule = parse_command(
        "cal --fixed personal-study --daily --time 19:00-20:00"
    )
    state = CalendarState(rules=[existing_rule])
    state.save(calendar_path)

    exported_count = export_to_calendar(loaded, calendar_path)
    result = CalendarState.load(calendar_path)

    generated = [rule for rule in result.rules if rule.id.startswith(GENERATED_PREFIX)]
    assert exported_count == 25
    assert len(generated) == 25
    assert any(rule.id == "personal-study" for rule in result.rules)
    assert json.loads(calendar_path.read_text(encoding="utf-8"))["version"] == 1
