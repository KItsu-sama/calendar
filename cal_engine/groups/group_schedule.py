"""Group / timetable configuration and bridge to the existing cal state.

Design
------
Group : organizational metadata only (``id``, ``name``, ``description``).
        Groups categorize activities; they never carry scheduling policy.
Timetable : scheduling rules (formerly ``schedules``). A timetable holds a set
        of slots/activities plus breaks, active days, default times, a slot
        length and an optional ``place_tag`` so a whole timetable can be
        re-timed or re-placed and swapped out easily.

An activity may belong to several groups (``group_ids``) or to none, and the
same activity can be referenced by several timetables. Deleting a group or a
timetable never deletes the activities themselves, and deleting a timetable
never touches already-exported calendar history.

This is intentionally additive: configuration lives in a separate JSON file so
existing version-1 calendar.json files remain schema-compatible.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ..core.models import (
    ActivityRule, Flexibility, Priority, Recurrence,
    RecurrenceType, TimeConstraints, TimeWindow,
)
from ..core.storage import CalendarState, StorageError

CONFIG_VERSION = 2
GENERATED_PREFIX = "timetable__"
DAYS = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]

def load_config(path: str | Path) -> dict[str, Any]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot load group/schedule configuration: {exc}") from None
    validate_config(data)
    return data

def save_config(config: dict[str, Any], path: str | Path) -> None:
    validate_config(config)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(target.suffix + ".tmp")
    temp.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(target)


def _valid_time(value: str) -> bool:
    return bool(re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", str(value)))


def _check_window(start: str, end: str, label: str) -> None:
    if not _valid_time(start) or not _valid_time(end) or start >= end:
        raise ValueError(f"{label}: start/end must be valid HH:MM times and start must be before end.")


def _valid_group_ids(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(x, str) and x.strip() for x in value):
        raise ValueError(f"{label}: group_ids must be an array of non-empty strings.")
    return [x.strip() for x in value]


def validate_config(config: dict[str, Any]) -> None:
    if not isinstance(config, dict) or config.get("version") != CONFIG_VERSION:
        raise ValueError(f"Configuration must be an object with version {CONFIG_VERSION}.")
    groups, timetables = config.get("groups"), config.get("timetables")
    if not isinstance(groups, list) or not isinstance(timetables, list):
        raise ValueError("Configuration needs 'groups' and 'timetables' arrays.")
    group_ids = [str(g.get("id", "")).strip() for g in groups]
    if any(not x for x in group_ids) or len(group_ids) != len(set(group_ids)):
        raise ValueError("Group IDs must be non-empty and unique.")
    for g in groups:
        # Groups are organizational metadata only: id, name and description.
        # They deliberately carry no scheduling policy (priority, flexibility,
        # override_policy). Scheduling belongs to the timetable.
        if not isinstance(g.get("name", ""), str) or not g.get("name", "").strip():
            raise ValueError(f"Group '{g.get('id')}' needs a non-empty name.")
    timetable_ids = [str(t.get("id", "")).strip() for t in timetables]
    if any(not x for x in timetable_ids) or len(timetable_ids) != len(set(timetable_ids)):
        raise ValueError("Timetable IDs must be non-empty and unique.")
    for t in timetables:
        _check_window(t.get("start_time", ""), t.get("end_time", ""), f"Timetable {t.get('id')}")
        if not isinstance(t.get("period_minutes"), int) or t["period_minutes"] <= 0:
            raise ValueError(f"Timetable '{t.get('id')}' period_minutes must be a positive integer.")
        if not isinstance(t.get("days"), list) or any(d not in DAYS for d in t["days"]):
            raise ValueError(f"Timetable '{t.get('id')}' days must be weekday codes MON..SUN.")
        place_tag = t.get("place_tag")
        if place_tag is not None and not isinstance(place_tag, str):
            raise ValueError(f"Timetable '{t.get('id')}' place_tag must be a string.")
        activities = t.get("activities", [])
        if not isinstance(activities, list):
            raise ValueError(f"Timetable '{t.get('id')}' activities must be an array.")
        seen = set()
        for a in activities:
            aid = str(a.get("id", "")).strip()
            if not aid or aid in seen:
                raise ValueError(f"Activity IDs in timetable '{t.get('id')}' must be unique and non-empty.")
            seen.add(aid)
            if a.get("day") not in DAYS or not str(a.get("name", "")).strip():
                raise ValueError(f"Activity '{aid}' needs a valid day and name.")
            _check_window(a.get("start_time", ""), a.get("end_time", ""), f"Activity {aid}")
            _valid_group_ids(a.get("group_ids", []), f"Activity {aid} group_ids")
            if a.get("priority") is not None and a.get("priority") not in {p.value for p in Priority}:
                raise ValueError(f"Invalid priority for activity {aid}.")
        for b in t.get("breaks", []):
            _check_window(b.get("start_time", ""), b.get("end_time", ""), f"Break {b.get('id', '')}")
            if b.get("priority") is not None and b.get("priority") not in {p.value for p in Priority}:
                raise ValueError("Invalid priority for break.")


def export_to_calendar(config: dict[str, Any], calendar_path: str | Path) -> int:
    """Merge generated timetable rules into a standard version-1 calendar JSON.

    Only rules created by this exporter (the reserved prefix) are replaced.
    Existing unrelated activities and their exceptions are retained.
    Exporting a timetable turns its slots/breaks into weekly core calendar
    rules; the generated rules are independent copies, so deleting or editing a
    timetable later never affects calendar history that was already exported.
    """
    validate_config(config)
    target = Path(calendar_path)
    state = CalendarState.load(target) if target.exists() else CalendarState()
    old_ids = {r.id for r in state.rules if r.id.startswith(GENERATED_PREFIX)}
    state.rules = [r for r in state.rules if r.id not in old_ids]
    state.exceptions = [e for e in state.exceptions if e.rule_id not in old_ids]
    generated: list[ActivityRule] = []
    for timetable in config["timetables"]:
        for activity in timetable.get("activities", []):
            day = activity["day"]
            if day not in timetable["days"]:
                continue
            aid = f"{GENERATED_PREFIX}{timetable['id']}__{activity['id']}"
            generated.append(ActivityRule(
                id=aid,
                name=f"{activity['name']} ({day} P{activity.get('period', '?')})",
                flexibility=Flexibility.FIXED,
                priority=Priority(activity.get("priority", "NORMAL")),
                recurrence=Recurrence(type=RecurrenceType.WEEKLY, days_of_week=[day]),
                time_constraints=TimeConstraints(fixed_interval=TimeWindow(activity["start_time"], activity["end_time"])),
                group_ids=list(activity.get("group_ids", [])),
            ))
        for br in timetable.get("breaks", []):
            for day in timetable["days"]:
                aid = f"{GENERATED_PREFIX}{timetable['id']}__{br.get('id','break')}__{day}"
                generated.append(ActivityRule(
                    id=aid, name=f"{br.get('name','Break')} ({day})",
                    flexibility=Flexibility.FIXED,
                    priority=Priority(br.get("priority", "HIGH")),
                    recurrence=Recurrence(type=RecurrenceType.WEEKLY, days_of_week=[day]),
                    time_constraints=TimeConstraints(fixed_interval=TimeWindow(br["start_time"], br["end_time"])),
                ))
    existing_ids = {r.id for r in state.rules}
    collisions = [r.id for r in generated if r.id in existing_ids]
    if collisions:
        raise StorageError("Generated ID collision: " + ", ".join(collisions))
    state.rules.extend(generated)
    state._validate()
    state.save(target)
    return len(generated)
