from types import SimpleNamespace

import pytest

from cal_engine.ui.helpper import Help
from cal_engine.cli import _helper_calendar_path
from cal_engine.core.parser import parse_command
from cal_engine.core.storage import CalendarState


def test_mutation_uses_active_calendar_and_resolved_entrypoint(tmp_path, monkeypatch):
    calendar = tmp_path / "calendar.json"
    calendar.write_text("{}", encoding="utf-8")
    helper = Help(calendar)
    calls = []

    def run(command):
        calls.append(command)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("cal_engine.ui.helpper.subprocess.run", run)

    assert helper._run_mutation(["--add", "--fixed", "lunch"]) == 0
    assert calls == [[
        helper.python,
        str(helper.entrypoint),
        "--load",
        str(calendar),
        "--save",
        str(calendar),
        "--add",
        "--fixed",
        "lunch",
    ]]


def test_fixed_builder_previews_then_persists_after_confirmation(tmp_path, monkeypatch):
    helper = Help(tmp_path / "calendar.json")
    answers = iter([
        "lunch",
        "1",
        "",
        "12:00-13:00",
        "n",
        "n",
        "",
        "",
        "y",
    ])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))

    assert helper.build_fixed() == 0
    state = CalendarState.load(helper.calendar_path)
    assert [rule.id for rule in state.rules] == ["lunch"]


@pytest.mark.parametrize(
    "value",
    ["25:90-30:00", "25:00-26:00", "abc", "10:00", "10:00-08:00"],
)
def test_time_range_validation_rejects_invalid_ranges(value):
    with pytest.raises(ValueError):
        Help._validate_time_range(value)


def test_invalid_answers_are_retried(monkeypatch):
    helper = Help()
    answers = iter(["10:00-08:00", "10:00-11:00"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))

    assert helper._ask_value("Time: ", helper._validate_time_range) == "10:00-11:00"


def test_activity_selection_edits_and_saves_existing_rule(tmp_path, monkeypatch):
    calendar = tmp_path / "calendar.json"
    original = parse_command("cal --fixed lunch --daily --time 12:00-13:00")
    CalendarState(rules=[original]).save(calendar)
    helper = Help(calendar)
    answers = iter(["1", "1", "10:00-11:00", "y"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))

    assert helper.manage_activities() == 0
    updated = CalendarState.load(calendar).require_rule("lunch")
    assert updated.time_constraints.fixed_interval.start_time == "10:00"
    assert updated.time_constraints.fixed_interval.end_time == "11:00"


def test_cli_error_helper_keeps_load_path_and_uses_save_as_fallback():
    assert _helper_calendar_path(["--load", "data/custom.json", "--change", "school"]) == (
        "data/custom.json"
    )
    assert _helper_calendar_path(["--save=custom.json", "--fixed"]) == "custom.json"
