"""Tests for the coordinated multi-file persistence layer.

The store must validate before committing, snapshot affected files, roll back
on failure, recover interrupted operations, and record every committed file
version. See cal_engine/transactional_persistence.py.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from cal_engine import (
    CalendarState,
    CoordinatedStore,
    ValidationError,
    digest_state,
    parse_command,
)
from cal_engine.core.transactional_persistence import _SNAPSHOT_INDEX_NAME

CALENDAR_PATH = "data/calendar.json"
CONFIG_PATH = "groups_schedules.json"

GROUP_CONFIG = {
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

CALENDAR = {
    "version": 1,
    "rules": [{
        "id": "personal-study",
        "name": "Personal study",
        "flexibility": "FIXED",
        "priority": "NORMAL",
        "recurrence": {"type": "WEEKLY", "days_of_week": ["MON"]},
        "time_constraints": {"fixed_interval": {"start_time": "19:00", "end_time": "20:00"}},
    }],
    "exceptions": [],
}

INVALID_CONFIG = {
    "version": 1,
    "groups": [],
    "schedules": [{
        "id": "s1",
        "group_id": "ghost",
        "name": "x",
        "days": ["MON"],
        "start_time": "09:00",
        "end_time": "10:00",
        "period_minutes": 60,
        "activities": [],
        "breaks": [],
    }],
}


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _read_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def _digest(obj: object) -> str:
    return digest_state(obj)


def test_commit_writes_all_staged_files_and_records_versions(tmp_path):
    _write(tmp_path / CALENDAR_PATH, json.dumps(CALENDAR))
    _write(tmp_path / CONFIG_PATH, json.dumps(GROUP_CONFIG))

    store = CoordinatedStore(tmp_path)
    txn = store.begin(description="timetable edit")
    assert txn.startswith("txn_")

    store.stage(CALENDAR_PATH, CALENDAR)
    store.stage(CONFIG_PATH, GROUP_CONFIG)
    record = store.commit()

    assert record.status == "committed"
    assert set(record.files) == {store._key(CALENDAR_PATH), store._key(CONFIG_PATH)}
    for version in record.files.values():
        assert version.size > 0
        assert version.committed_at

    assert _read_json(tmp_path / CALENDAR_PATH) == CALENDAR
    assert _read_json(tmp_path / CONFIG_PATH) == GROUP_CONFIG

    assert store.current_version(CALENDAR_PATH).digest == _digest(CALENDAR)
    assert store.current_version(CONFIG_PATH).digest == _digest(GROUP_CONFIG)

    assert len(store.file_history(CALENDAR_PATH)) == 1
    assert len(store.file_history(CONFIG_PATH)) == 1


def test_validation_failure_before_commit_leaves_files_unchanged(tmp_path):
    _write(tmp_path / CALENDAR_PATH, json.dumps(CALENDAR))
    _write(tmp_path / CONFIG_PATH, json.dumps(GROUP_CONFIG))

    store = CoordinatedStore(tmp_path)
    store.begin()
    store.stage(CALENDAR_PATH, CALENDAR)
    store.stage_group_config(INVALID_CONFIG, tmp_path / CONFIG_PATH)

    with pytest.raises(ValidationError):
        store.commit()

    assert not store.is_active()
    assert _read_json(tmp_path / CALENDAR_PATH) == CALENDAR
    assert _read_json(tmp_path / CONFIG_PATH) == GROUP_CONFIG

    # Nothing was committed.
    assert store.current_version(CALENDAR_PATH) is None
    assert store.file_history(CONFIG_PATH) == []


def test_rollback_restores_previous_content(tmp_path):
    _write(tmp_path / CALENDAR_PATH, json.dumps(CALENDAR))

    store = CoordinatedStore(tmp_path)
    store.begin()
    store.stage(CALENDAR_PATH, {"version": 1, "rules": [], "exceptions": []})
    store.rollback()

    assert not store.is_active()
    assert _read_json(tmp_path / CALENDAR_PATH) == CALENDAR


def test_context_manager_commits_on_success(tmp_path):
    store = CoordinatedStore(tmp_path)
    with store:
        store.stage(CALENDAR_PATH, CALENDAR)
    assert not store.is_active()
    assert _read_json(tmp_path / CALENDAR_PATH) == CALENDAR
    assert store.current_version(CALENDAR_PATH) is not None


def test_context_manager_rolls_back_on_exception(tmp_path):
    _write(tmp_path / CALENDAR_PATH, json.dumps(CALENDAR))
    store = CoordinatedStore(tmp_path)
    with pytest.raises(ValueError, match="boom"):
        with store:
            store.stage(CALENDAR_PATH, {"version": 1, "rules": [], "exceptions": []})
            raise ValueError("boom")
    assert not store.is_active()
    assert _read_json(tmp_path / CALENDAR_PATH) == CALENDAR


def test_recover_restores_interrupted_transaction(tmp_path):
    # Pre-existing files representing the last good, consistent state.
    _write(tmp_path / CALENDAR_PATH, json.dumps(CALENDAR))
    _write(tmp_path / CONFIG_PATH, json.dumps(GROUP_CONFIG))

    store = CoordinatedStore(tmp_path)
    store.begin()
    store.stage(CALENDAR_PATH, {"version": 1, "rules": [], "exceptions": []})
    store.stage(CONFIG_PATH, {"version": 1, "groups": [], "schedules": []})

    # Simulate a crash mid-commit: snapshots are taken and persisted, then the
    # calendar file is overwritten but the group config is never written, and
    # the commit is never journaled. The two files now diverge (different
    # versions of the timetable).
    store._snapshot_file(CALENDAR_PATH)
    store._snapshot_file(CONFIG_PATH)
    assert store._txn_dir is not None
    (store._txn_dir / _SNAPSHOT_INDEX_NAME).write_text(
        json.dumps(store._snapshot_index, indent=2) + "\n", encoding="utf-8"
    )
    (tmp_path / CALENDAR_PATH).write_text(
        json.dumps({"version": 1, "rules": [], "exceptions": []}), encoding="utf-8"
    )

    # Restart: a fresh store on the same workspace recovers the interruption.
    recovered = CoordinatedStore(tmp_path).recover()
    assert len(recovered) == 1
    assert recovered[0].status == "recovered"

    # Both files restored to their pre-transaction (consistent) state.
    assert _read_json(tmp_path / CALENDAR_PATH) == CALENDAR
    assert _read_json(tmp_path / CONFIG_PATH) == GROUP_CONFIG

    # Nothing was committed; the interrupted transaction is journaled as recovered.
    assert store.current_version(CALENDAR_PATH) is None


def test_current_version_and_file_history(tmp_path):
    store = CoordinatedStore(tmp_path)
    first = dict(CALENDAR)
    second = {
        "version": 1,
        "rules": [{**CALENDAR["rules"][0], "id": "personal-study-2"}],
        "exceptions": [],
    }

    store.begin()
    store.stage(CALENDAR_PATH, first)
    store.commit()
    store.begin()
    store.stage(CALENDAR_PATH, second)
    store.commit()

    current = store.current_version(CALENDAR_PATH)
    assert current.digest == _digest(second)
    history = store.file_history(CALENDAR_PATH)
    assert len(history) == 2
    assert _digest(first) in {v.digest for v in history[0].files.values()}
    assert _digest(second) in {v.digest for v in history[1].files.values()}


def test_no_active_transaction_requires_begin(tmp_path):
    store = CoordinatedStore(tmp_path)
    with pytest.raises(Exception):
        store.stage(CALENDAR_PATH, CALENDAR)
    with pytest.raises(Exception):
        store.validate()
    with pytest.raises(Exception):
        store.commit()
    with pytest.raises(Exception):
        store.rollback()


def test_nested_begin_raises(tmp_path):
    store = CoordinatedStore(tmp_path)
    store.begin()
    with pytest.raises(Exception):
        store.begin()
    store.rollback()


def test_raw_bytes_content(tmp_path):
    store = CoordinatedStore(tmp_path)
    store.begin()
    store.stage("notes.txt", b"raw notes", as_json=False)
    store.commit()
    assert (tmp_path / "notes.txt").read_bytes() == b"raw notes"
    assert store.current_version("notes.txt").digest == hashlib.sha256(b"raw notes").hexdigest()


def test_calendar_state_integration(tmp_path):
    state = CalendarState()
    state.add_rule(parse_command("cal --fixed study --daily --time 19:00-20:00"))
    path = tmp_path / CALENDAR_PATH

    store = CoordinatedStore(tmp_path)
    store.begin()
    store.stage_calendar_state(state, path)
    store.commit()

    loaded = CalendarState.load(path)
    assert loaded.to_dict() == state.to_dict()
    assert store.current_version(path).digest == _digest(state.to_dict())


def test_invalid_calendar_state_rejected(tmp_path):
    _write(tmp_path / CALENDAR_PATH, json.dumps(CALENDAR))

    store = CoordinatedStore(tmp_path)
    store.begin()
    store.stage(
        CALENDAR_PATH,
        {"version": 99, "rules": [], "exceptions": []},
        validator=CalendarState.from_dict,
    )
    with pytest.raises(ValidationError):
        store.commit()
    assert not store.is_active()
    assert _read_json(tmp_path / CALENDAR_PATH) == CALENDAR


def test_group_config_integration(tmp_path):
    path = tmp_path / CONFIG_PATH

    store = CoordinatedStore(tmp_path)
    store.begin()
    store.stage_group_config(GROUP_CONFIG, path)
    store.commit()
    assert _read_json(path) == GROUP_CONFIG

    # Invalid config is rejected before commit; file unchanged.
    store.begin()
    store.stage_group_config(INVALID_CONFIG, path)
    with pytest.raises(ValidationError):
        store.commit()
    assert _read_json(path) == GROUP_CONFIG


def test_multi_file_timetable_scenario_commits_together(tmp_path):
    # The motivating example: edit timetable, save group config, export
    # calendar rules -- all in one transaction boundary.
    _write(tmp_path / CALENDAR_PATH, json.dumps(CALENDAR))
    _write(tmp_path / CONFIG_PATH, json.dumps(GROUP_CONFIG))

    updated_calendar = {
        "version": 1,
        "rules": [*CALENDAR["rules"], {
            "id": "group_schedule__work_week__mon_p1",
            "name": "Work block (MON P1)",
            "flexibility": "FIXED",
            "priority": "HIGH",
            "recurrence": {"type": "WEEKLY", "days_of_week": ["MON"]},
            "time_constraints": {"fixed_interval": {"start_time": "09:00", "end_time": "10:00"}},
        }],
        "exceptions": [],
    }
    updated_config = dict(GROUP_CONFIG)
    updated_config["schedules"][0]["name"] = "Work week (revised)"

    store = CoordinatedStore(tmp_path)
    store.begin(description="revise school timetable")
    store.stage_calendar_state(
        CalendarState.from_dict(updated_calendar), tmp_path / CALENDAR_PATH
    )
    store.stage_group_config(updated_config, tmp_path / CONFIG_PATH)
    record = store.commit()

    # Both files updated atomically within one committed transaction.
    assert _read_json(tmp_path / CALENDAR_PATH) == updated_calendar
    assert _read_json(tmp_path / CONFIG_PATH) == updated_config
    assert store.current_version(CALENDAR_PATH).digest == _digest(updated_calendar)
    assert store.current_version(CONFIG_PATH).digest == _digest(updated_config)

    # A clear record: one commit id covers both files.
    cal_history = store.file_history(CALENDAR_PATH)
    cfg_history = store.file_history(CONFIG_PATH)
    assert [r.transaction_id for r in cal_history] == [record.transaction_id]
    assert [r.transaction_id for r in cfg_history] == [record.transaction_id]
