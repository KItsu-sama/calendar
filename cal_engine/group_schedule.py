"""Editable group/schedule configuration and bridge to the existing cal state.

This is intentionally additive: group metadata lives in a separate JSON file so
existing version-1 calendar.json files remain schema-compatible.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .models import (
    ActivityRule, Flexibility, Priority, Recurrence,
    RecurrenceType, TimeConstraints, TimeWindow,
)
from .storage import CalendarState, StorageError

CONFIG_VERSION = 1
GENERATED_PREFIX = "group_schedule__"
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


def validate_config(config: dict[str, Any]) -> None:
    if not isinstance(config, dict) or config.get("version") != CONFIG_VERSION:
        raise ValueError("Configuration must be an object with version 1.")
    groups, schedules = config.get("groups"), config.get("schedules")
    if not isinstance(groups, list) or not isinstance(schedules, list):
        raise ValueError("Configuration needs 'groups' and 'schedules' arrays.")
    group_ids = [str(g.get("id", "")).strip() for g in groups]
    if any(not x for x in group_ids) or len(group_ids) != len(set(group_ids)):
        raise ValueError("Group IDs must be non-empty and unique.")
    for g in groups:
        if g.get("priority", "NORMAL") not in {p.value for p in Priority}:
            raise ValueError(f"Invalid priority in group {g.get('id')}.")
        if g.get("flexibility", "FIXED") not in {f.value for f in Flexibility}:
            raise ValueError(f"Invalid flexibility in group {g.get('id')}.")
        if g.get("override_policy", "never") not in {"never", "overrideable", "always"}:
            raise ValueError(f"Invalid override_policy in group {g.get('id')}.")
    schedule_ids = [str(s.get("id", "")).strip() for s in schedules]
    if any(not x for x in schedule_ids) or len(schedule_ids) != len(set(schedule_ids)):
        raise ValueError("Schedule IDs must be non-empty and unique.")
    for s in schedules:
        if s.get("group_id") not in group_ids:
            raise ValueError(f"Schedule '{s.get('id')}' refers to an unknown group.")
        _check_window(s.get("start_time", ""), s.get("end_time", ""), f"Schedule {s.get('id')}")
        if not isinstance(s.get("period_minutes"), int) or s["period_minutes"] <= 0:
            raise ValueError(f"Schedule '{s.get('id')}' period_minutes must be a positive integer.")
        if not isinstance(s.get("days"), list) or any(d not in DAYS for d in s["days"]):
            raise ValueError(f"Schedule '{s.get('id')}' days must be weekday codes MON..SUN.")
        activities = s.get("activities", [])
        if not isinstance(activities, list):
            raise ValueError(f"Schedule '{s.get('id')}' activities must be an array.")
        seen = set()
        for a in activities:
            aid = str(a.get("id", "")).strip()
            if not aid or aid in seen:
                raise ValueError(f"Activity IDs in schedule '{s.get('id')}' must be unique and non-empty.")
            seen.add(aid)
            if a.get("day") not in DAYS or not str(a.get("name", "")).strip():
                raise ValueError(f"Activity '{aid}' needs a valid day and name.")
            _check_window(a.get("start_time", ""), a.get("end_time", ""), f"Activity {aid}")
        for b in s.get("breaks", []):
            _check_window(b.get("start_time", ""), b.get("end_time", ""), f"Break {b.get('id', '')}")
            if b.get("override_policy", "overrideable") not in {"never", "overrideable", "always"}:
                raise ValueError("Invalid override policy for break.")
            if b.get("priority", "HIGH") not in {p.value for p in Priority}:
                raise ValueError("Invalid priority for break.")


def export_to_calendar(config: dict[str, Any], calendar_path: str | Path) -> int:
    """Merge generated timetable rules into a standard version-1 calendar JSON.

    Only rules created by this exporter (the reserved prefix) are replaced.
    Existing unrelated activities and their exceptions are retained.
    """
    validate_config(config)
    target = Path(calendar_path)
    state = CalendarState.load(target) if target.exists() else CalendarState()
    old_ids = {r.id for r in state.rules if r.id.startswith(GENERATED_PREFIX)}
    state.rules = [r for r in state.rules if r.id not in old_ids]
    state.exceptions = [e for e in state.exceptions if e.rule_id not in old_ids]
    groups = {g["id"]: g for g in config["groups"]}
    generated: list[ActivityRule] = []
    for schedule in config["schedules"]:
        group = groups[schedule["group_id"]]
        for activity in schedule.get("activities", []):
            day = activity["day"]
            if day not in schedule["days"]:
                continue
            aid = f"{GENERATED_PREFIX}{schedule['id']}__{activity['id']}"
            generated.append(ActivityRule(
                id=aid,
                name=f"{activity['name']} ({day} P{activity.get('period', '?')})",
                flexibility=Flexibility(group.get("flexibility", "FIXED")),
                priority=Priority(activity.get("priority", group.get("priority", "HIGH"))),
                recurrence=Recurrence(type=RecurrenceType.WEEKLY, days_of_week=[day]),
                time_constraints=TimeConstraints(fixed_interval=TimeWindow(activity["start_time"], activity["end_time"])),
            ))
        for br in schedule.get("breaks", []):
            for day in schedule["days"]:
                aid = f"{GENERATED_PREFIX}{schedule['id']}__{br.get('id','break')}__{day}"
                generated.append(ActivityRule(
                    id=aid, name=f"{br.get('name','Break')} ({day})",
                    # The core engine has no explicit override_policy field. CHANGEABLE
                    # communicates that this high-priority block may be displaced.
                    flexibility=Flexibility.CHANGEABLE if br.get("override_policy", "overrideable") != "never" else Flexibility.FIXED,
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
