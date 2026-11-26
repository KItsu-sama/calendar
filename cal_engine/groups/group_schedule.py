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

import copy
import json
import re
from pathlib import Path
from typing import Any

from ..core.models import (
    ActivityRule, CalendarException, Flexibility, Priority, Recurrence,
    RecurrenceType, TimeConstraints, TimeWindow,
)
from ..core.storage import CalendarState, StorageError

CONFIG_VERSION = 2
GENERATED_PREFIX = "timetable__"
LEGACY_PREFIX = "group_schedule__"
DAYS = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]
_EXPORTED_NAME_SUFFIX = re.compile(r" \([A-Z]{3}(?: P[^)]+)?\)$")

def upgrade_config(data: Any) -> Any:
    """Return a version-2 copy of a version-1 configuration (``schedules``).

    Version 1 stored scheduling policy on the group and linked each schedule to
    one group through ``group_id``. Version 2 keeps groups as metadata only, so:

    * ``schedules`` become ``timetables``;
    * the schedule's ``group_id`` becomes membership (``group_ids``) on each of
      its activities;
    * a group's old ``priority`` is copied onto activities that had none of
      their own, so a timetable keeps the colour it had before the upgrade;
    * ``flexibility`` and ``override_policy`` on groups are dropped (they were
      never enforced).

    Anything that is not a version-1 object is returned unchanged.
    """
    if not isinstance(data, dict) or data.get("version") != 1 or "schedules" not in data:
        return data
    groups = [dict(g) for g in data.get("groups", []) if isinstance(g, dict)]
    by_id = {str(g.get("id", "")): g for g in groups}
    timetables = []
    for sched in data.get("schedules", []):
        if not isinstance(sched, dict):
            continue
        gid = str(sched.get("group_id", "")).strip()
        default_priority = (by_id.get(gid) or {}).get("priority")
        activities = []
        for act in sched.get("activities", []):
            act = dict(act)
            ids = [str(x) for x in act.get("group_ids", [])]
            if gid and gid in by_id and gid not in ids:
                ids.append(gid)
            act["group_ids"] = ids
            if default_priority and not act.get("priority"):
                act["priority"] = default_priority
            activities.append(act)
        timetables.append({
            "id": sched.get("id", ""),
            "name": sched.get("name", sched.get("id", "")),
            "description": sched.get("description", ""),
            "place_tag": sched.get("place_tag", ""),
            "days": list(sched.get("days", [])),
            "start_time": sched.get("start_time", ""),
            "end_time": sched.get("end_time", ""),
            "period_minutes": sched.get("period_minutes", 45),
            "breaks": [dict(b) for b in sched.get("breaks", [])],
            "activities": activities,
        })
    return {
        "version": CONFIG_VERSION,
        "groups": [
            {"id": g.get("id", ""), "name": g.get("name", g.get("id", "")), "description": g.get("description", "")}
            for g in groups
        ],
        "timetables": timetables,
    }


def load_config(path: str | Path) -> dict[str, Any]:
    """Load and validate a configuration, upgrading a version-1 file in memory."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot load group/schedule configuration: {exc}") from None
    data = upgrade_config(data)
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


def is_generated_rule_id(rule_id: str) -> bool:
    """True for current and legacy timetable-exporter IDs."""
    return rule_id.startswith(GENERATED_PREFIX) or rule_id.startswith(LEGACY_PREFIX)


def generated_rule_id(timetable_id: str, source_id: str) -> str:
    """Stable calendar rule ID: timetable identity plus the canonical source id."""
    return f"{GENERATED_PREFIX}{timetable_id}__{source_id}"


def _strip_generated_prefix(rule_id: str) -> str | None:
    for prefix in (GENERATED_PREFIX, LEGACY_PREFIX):
        if rule_id.startswith(prefix):
            return rule_id[len(prefix):]
    return None


def _activity_merge_key(activity: dict[str, Any]) -> tuple:
    """Slots that share this key become one weekly rule with several weekdays."""
    groups = tuple(sorted(str(g) for g in activity.get("group_ids") or []))
    return (
        str(activity.get("name", "")).strip(),
        activity.get("start_time"),
        activity.get("end_time"),
        activity.get("priority") or "NORMAL",
        groups,
        activity.get("valid_from"),
        activity.get("valid_until"),
        tuple(activity.get("except_days") or []),
        activity.get("flexibility") or Flexibility.FIXED.value,
    )


def _canonical_activity(group: list[dict[str, Any]]) -> dict[str, Any]:
    return min(group, key=lambda a: (DAYS.index(a["day"]) if a.get("day") in DAYS else 99, a["id"]))


def _ordered_days(days: list[str]) -> list[str]:
    return [d for d in DAYS if d in days]


def _bare_exported_name(name: str) -> str:
    return _EXPORTED_NAME_SUFFIX.sub("", name)


def _rule_fingerprint(rule: ActivityRule) -> tuple:
    interval = rule.time_constraints.fixed_interval
    start = interval.start_time if interval else ""
    end = interval.end_time if interval else ""
    return (
        _bare_exported_name(rule.name),
        rule.flexibility.value,
        rule.priority.value,
        start,
        end,
        tuple(rule.group_ids),
    )


def _activity_rule(
    *,
    rule_id: str,
    name: str,
    priority: str,
    days: list[str],
    start: str,
    end: str,
    group_ids: list[str] | None = None,
    valid_from: str | None = None,
    valid_until: str | None = None,
    except_days: list[str] | None = None,
    flexibility: str | None = None,
) -> ActivityRule:
    return ActivityRule(
        id=rule_id,
        name=name,
        flexibility=Flexibility(flexibility or Flexibility.FIXED.value),
        priority=Priority(priority),
        recurrence=Recurrence(
            type=RecurrenceType.WEEKLY,
            days_of_week=_ordered_days(days),
            except_days=list(except_days or []),
            valid_from=valid_from,
            valid_until=valid_until,
        ),
        time_constraints=TimeConstraints(fixed_interval=TimeWindow(start, end)),
        group_ids=list(group_ids or []),
    )


def _alias_ids(timetable_id: str, source_id: str, extra_days: list[str] | None = None) -> list[str]:
    aliases = [
        generated_rule_id(timetable_id, source_id),
        f"{LEGACY_PREFIX}{timetable_id}__{source_id}",
    ]
    for day in extra_days or []:
        aliases.append(generated_rule_id(timetable_id, f"{source_id}__{day}"))
        aliases.append(f"{LEGACY_PREFIX}{timetable_id}__{source_id}__{day}")
    return aliases


def build_timetable_rules(config: dict[str, Any]) -> tuple[list[ActivityRule], dict[str, str]]:
    """Compile timetable slots/breaks into weekly rules and an old-ID → new-ID map.

    Slots that share identity, name, times, priority, groups and validity are
    one WEEKLY rule with every matching weekday. Different times never merge.
    Rule IDs are ``timetable__{timetable_id}__{canonical_activity_or_break_id}``.
    """
    generated: list[ActivityRule] = []
    aliases: dict[str, str] = {}
    seen: set[str] = set()

    def _register(rule: ActivityRule, source_ids: list[str], timetable_id: str, day_suffixes: bool) -> None:
        if rule.id in seen:
            raise StorageError(f"Generated ID collision: {rule.id}")
        seen.add(rule.id)
        generated.append(rule)
        extra = list(rule.recurrence.days_of_week) if day_suffixes else []
        for source_id in source_ids:
            for alias in _alias_ids(timetable_id, source_id, extra):
                aliases.setdefault(alias, rule.id)

    for timetable in config["timetables"]:
        tid = timetable["id"]
        active = set(timetable["days"])
        buckets: dict[tuple, list[dict[str, Any]]] = {}
        for activity in timetable.get("activities", []):
            if activity.get("day") not in active:
                continue
            buckets.setdefault(_activity_merge_key(activity), []).append(activity)
        for key, group in buckets.items():
            canonical = _canonical_activity(group)
            name, start, end, priority, groups, valid_from, valid_until, except_days, flexibility = key
            rule = _activity_rule(
                rule_id=generated_rule_id(tid, canonical["id"]),
                name=name,
                priority=priority,
                days=[a["day"] for a in group],
                start=start,
                end=end,
                group_ids=list(groups),
                valid_from=valid_from,
                valid_until=valid_until,
                except_days=list(except_days),
                flexibility=flexibility,
            )
            _register(rule, [a["id"] for a in group], tid, day_suffixes=True)
        for br in timetable.get("breaks", []):
            bid = str(br.get("id") or "break")
            rule = _activity_rule(
                rule_id=generated_rule_id(tid, bid),
                name=str(br.get("name") or "Break"),
                priority=br.get("priority") or "HIGH",
                days=list(timetable["days"]),
                start=br["start_time"],
                end=br["end_time"],
            )
            _register(rule, [bid], tid, day_suffixes=True)
    return generated, aliases


def lookup_generated_block(
    rule_id: str,
    config: dict[str, Any],
    weekday: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any] | None, dict[str, Any] | None] | None:
    """Map a generated rule (and optional occurrence weekday) back to config.

    For a merged weekly rule the weekday selects the slot that actually falls
    on that day, so editing Wednesday does not rewrite Monday's activity id.
    """
    rest = _strip_generated_prefix(rule_id)
    if rest is None:
        return None
    for tt in sorted(config.get("timetables", []), key=lambda t: -len(str(t.get("id", "")))):
        head = str(tt.get("id", "")) + "__"
        if not rest.startswith(head):
            continue
        tail = rest[len(head):]
        for br in tt.get("breaks", []):
            bid = str(br.get("id") or "break")
            if tail == bid or tail.startswith(bid + "__"):
                return tt, None, br
        act = next((a for a in tt.get("activities", []) if a.get("id") == tail), None)
        if act is None:
            return None
        if weekday and weekday != act.get("day"):
            key = _activity_merge_key(act)
            sibling = next(
                (a for a in tt.get("activities", [])
                 if a.get("day") == weekday and _activity_merge_key(a) == key),
                None,
            )
            if sibling:
                act = sibling
        return tt, act, None
    return None


def _remap_exceptions(
    exceptions: list[CalendarException],
    old_ids: set[str],
    id_map: dict[str, str],
) -> list[CalendarException]:
    kept: list[CalendarException] = []
    for item in exceptions:
        if item.rule_id not in old_ids:
            kept.append(item)
            continue
        mapped = id_map.get(item.rule_id)
        if mapped is None:
            continue
        if mapped == item.rule_id:
            kept.append(item)
            continue
        updated = copy.deepcopy(item)
        updated.rule_id = mapped
        kept.append(updated)
    return kept


def apply_timetables(config: dict[str, Any], state: CalendarState) -> int:
    """Replace generated timetable rules inside ``state`` (in memory only).

    Only exporter-owned IDs (``timetable__`` and legacy ``group_schedule__``)
    are replaced. Manual rules stay. Exceptions on still-generated rules are
    remapped onto the stable IDs; exceptions for deleted slots are dropped.
    """
    validate_config(config)
    generated, aliases = build_timetable_rules(config)
    old_rules = [r for r in state.rules if is_generated_rule_id(r.id)]
    old_ids = {r.id for r in old_rules}

    by_fingerprint: dict[tuple, list[str]] = {}
    for rule in generated:
        by_fingerprint.setdefault(_rule_fingerprint(rule), []).append(rule.id)

    id_map: dict[str, str] = {}
    new_ids = {r.id for r in generated}
    for old in old_rules:
        if old.id in aliases:
            id_map[old.id] = aliases[old.id]
        elif old.id in new_ids:
            id_map[old.id] = old.id
        else:
            matches = by_fingerprint.get(_rule_fingerprint(old), [])
            if len(matches) == 1:
                id_map[old.id] = matches[0]

    state.rules = [r for r in state.rules if r.id not in old_ids]
    collisions = [r.id for r in generated if any(existing.id == r.id for existing in state.rules)]
    if collisions:
        raise StorageError("Generated ID collision: " + ", ".join(collisions))
    state.exceptions = _remap_exceptions(state.exceptions, old_ids, id_map)
    state.rules.extend(generated)
    state._validate()
    return len(generated)


def export_to_calendar(config: dict[str, Any], calendar_path: str | Path) -> int:
    """Merge generated timetable rules into a standard version-1 calendar JSON.

    Only rules created by this exporter (the reserved prefix) are replaced.
    Existing unrelated activities and their exceptions are retained.
    Exporting a timetable turns its slots/breaks into weekly core calendar
    rules; the generated rules are independent copies, so deleting or editing a
    timetable later never affects calendar history that was already exported.
    """
    target = Path(calendar_path)
    state = CalendarState.load(target) if target.exists() else CalendarState()
    count = apply_timetables(config, state)
    state.save(target)
    return count
