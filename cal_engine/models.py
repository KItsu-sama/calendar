"""
Data model for the `cal` scheduling engine.

Mirrors the JSON Schema supplied for ActivityRule / CalendarException
exactly. Dataclasses are the runtime representation; to_dict()/validate()
round-trip through the schema so any object built by the parser (or loaded
from storage) is guaranteed schema-valid.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional

from jsonschema import validate as _jsonschema_validate
from jsonschema.exceptions import ValidationError  # noqa: F401 - public re-export

DAY_NAMES = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]


class Flexibility(str, Enum):
    FIXED = "FIXED"
    CHANGEABLE = "CHANGEABLE"
    FLEXIBLE = "FLEXIBLE"
    OPTIONAL = "OPTIONAL"


class Priority(str, Enum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    NORMAL = "NORMAL"
    LOW = "LOW"


class RecurrenceType(str, Enum):
    NONE = "NONE"
    DAILY = "DAILY"
    WEEKLY = "WEEKLY"


class ExceptionAction(str, Enum):
    CANCEL = "CANCEL"
    MOVE = "MOVE"
    MODIFY = "MODIFY"


@dataclass
class TimeWindow:
    start_time: str  # "HH:MM"
    end_time: str  # "HH:MM"

    def to_dict(self) -> dict:
        return {"start_time": self.start_time, "end_time": self.end_time}


@dataclass
class Recurrence:
    type: RecurrenceType
    days_of_week: List[str] = field(default_factory=list)
    except_days: List[str] = field(default_factory=list)
    valid_from: Optional[str] = None  # "YYYY-MM-DD"
    valid_until: Optional[str] = None  # "YYYY-MM-DD"

    def to_dict(self) -> dict:
        d = {"type": self.type.value}
        if self.days_of_week:
            d["days_of_week"] = self.days_of_week
        if self.except_days:
            d["except_days"] = self.except_days
        if self.valid_from:
            d["valid_from"] = self.valid_from
        if self.valid_until:
            d["valid_until"] = self.valid_until
        return d


@dataclass
class TimeConstraints:
    fixed_interval: Optional[TimeWindow] = None
    allowed_windows: List[TimeWindow] = field(default_factory=list)
    allowed_days: List[str] = field(default_factory=list)
    min_duration_minutes: Optional[int] = None
    max_duration_minutes: Optional[int] = None
    deadline: Optional[str] = None  # ISO date-time
    date: Optional[str] = None  # one-off date for RecurrenceType.NONE

    def to_dict(self) -> dict:
        d = {}
        if self.fixed_interval:
            d["fixed_interval"] = self.fixed_interval.to_dict()
        if self.allowed_windows:
            d["allowed_windows"] = [w.to_dict() for w in self.allowed_windows]
        if self.allowed_days:
            d["allowed_days"] = self.allowed_days
        if self.min_duration_minutes is not None:
            d["min_duration_minutes"] = self.min_duration_minutes
        if self.max_duration_minutes is not None:
            d["max_duration_minutes"] = self.max_duration_minutes
        if self.deadline:
            d["deadline"] = self.deadline
        if self.date:
            d["date"] = self.date
        return d


@dataclass
class MoveOptions:
    preferred_days: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"preferred_days": self.preferred_days} if self.preferred_days else {}


@dataclass
class ActivityRule:
    id: str
    name: str
    flexibility: Flexibility
    priority: Priority
    recurrence: Recurrence
    time_constraints: TimeConstraints
    move_options: Optional[MoveOptions] = None

    def to_dict(self) -> dict:
        d = {
            "id": self.id,
            "name": self.name,
            "flexibility": self.flexibility.value,
            "priority": self.priority.value,
            "recurrence": self.recurrence.to_dict(),
            "time_constraints": self.time_constraints.to_dict(),
        }
        if self.move_options and self.move_options.preferred_days:
            d["move_options"] = self.move_options.to_dict()
        return d


@dataclass
class CalendarException:
    id: str
    rule_id: str
    target_date: str  # "YYYY-MM-DD"
    action: ExceptionAction
    new_date: Optional[str] = None
    new_time: Optional[TimeWindow] = None

    def to_dict(self) -> dict:
        d = {
            "id": self.id,
            "rule_id": self.rule_id,
            "target_date": self.target_date,
            "action": self.action.value,
        }
        if self.new_date:
            d["new_date"] = self.new_date
        if self.new_time:
            d["new_time"] = self.new_time.to_dict()
        return d




def time_window_from_dict(data: dict) -> TimeWindow:
    """Build a TimeWindow from its schema representation."""
    return TimeWindow(start_time=data["start_time"], end_time=data["end_time"])


def time_constraints_from_dict(data: dict) -> TimeConstraints:
    """Build TimeConstraints, preserving the one-off date used by temp rules."""
    constraints = TimeConstraints(
        fixed_interval=(
            time_window_from_dict(data["fixed_interval"])
            if data.get("fixed_interval")
            else None
        ),
        allowed_windows=[time_window_from_dict(window) for window in data.get("allowed_windows", [])],
        allowed_days=list(data.get("allowed_days", [])),
        min_duration_minutes=data.get("min_duration_minutes"),
        max_duration_minutes=data.get("max_duration_minutes"),
        deadline=data.get("deadline"),
        date=data.get("date"),
    )
    return constraints


def activity_rule_from_dict(data: dict) -> ActivityRule:
    """Rehydrate a schema-valid ActivityRule from JSON-compatible data."""
    recurrence_data = data["recurrence"]
    move_data = data.get("move_options") or {}
    return ActivityRule(
        id=data["id"],
        name=data["name"],
        flexibility=Flexibility(data["flexibility"]),
        priority=Priority(data["priority"]),
        recurrence=Recurrence(
            type=RecurrenceType(recurrence_data["type"]),
            days_of_week=list(recurrence_data.get("days_of_week", [])),
            except_days=list(recurrence_data.get("except_days", [])),
            valid_from=recurrence_data.get("valid_from"),
            valid_until=recurrence_data.get("valid_until"),
        ),
        time_constraints=time_constraints_from_dict(data["time_constraints"]),
        move_options=MoveOptions(preferred_days=list(move_data.get("preferred_days", [])))
        if move_data
        else None,
    )


def calendar_exception_from_dict(data: dict) -> CalendarException:
    """Rehydrate a schema-valid CalendarException from JSON-compatible data."""
    return CalendarException(
        id=data["id"],
        rule_id=data["rule_id"],
        target_date=data["target_date"],
        action=ExceptionAction(data["action"]),
        new_date=data.get("new_date"),
        new_time=time_window_from_dict(data["new_time"]) if data.get("new_time") else None,
    )

# --- JSON Schema (verbatim structure from the spec) -------------------------

_TIME_WINDOW_SCHEMA = {
    "type": "object",
    "properties": {
        "start_time": {"type": "string", "pattern": r"^([01]?[0-9]|2[0-3]):[0-5][0-9]$"},
        "end_time": {"type": "string", "pattern": r"^([01]?[0-9]|2[0-3]):[0-5][0-9]$"},
    },
    "required": ["start_time", "end_time"],
}

ACTIVITY_RULE_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "definitions": {
        "Flexibility": {"type": "string", "enum": [f.value for f in Flexibility]},
        "Priority": {"type": "string", "enum": [p.value for p in Priority]},
        "TimeWindow": _TIME_WINDOW_SCHEMA,
        "ActivityRule": {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "name": {"type": "string"},
                "flexibility": {"$ref": "#/definitions/Flexibility"},
                "priority": {"$ref": "#/definitions/Priority"},
                "recurrence": {
                    "type": "object",
                    "properties": {
                        "type": {"type": "string", "enum": [r.value for r in RecurrenceType]},
                        "days_of_week": {"type": "array", "items": {"type": "string", "enum": DAY_NAMES}},
                        "except_days": {"type": "array", "items": {"type": "string", "enum": DAY_NAMES}},
                        "valid_from": {"type": "string", "format": "date"},
                        "valid_until": {"type": "string", "format": "date"},
                    },
                    "required": ["type"],
                },
                "time_constraints": {
                    "type": "object",
                    "properties": {
                        "fixed_interval": {"$ref": "#/definitions/TimeWindow"},
                        "allowed_windows": {"type": "array", "items": {"$ref": "#/definitions/TimeWindow"}},
                        "allowed_days": {"type": "array", "items": {"type": "string", "enum": DAY_NAMES}},
                        "min_duration_minutes": {"type": "integer"},
                        "max_duration_minutes": {"type": "integer"},
                        "deadline": {"type": "string", "format": "date-time"},
                        "date": {"type": "string", "format": "date"},
                    },
                },
                "move_options": {
                    "type": "object",
                    "properties": {
                        "preferred_days": {"type": "array", "items": {"type": "string", "enum": DAY_NAMES}},
                    },
                },
            },
            "required": ["id", "name", "flexibility", "priority", "recurrence", "time_constraints"],
        },
    },
    "$ref": "#/definitions/ActivityRule",
}

CALENDAR_EXCEPTION_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "definitions": {
        "TimeWindow": _TIME_WINDOW_SCHEMA,
        "CalendarException": {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "rule_id": {"type": "string"},
                "target_date": {"type": "string", "format": "date"},
                "action": {"type": "string", "enum": [a.value for a in ExceptionAction]},
                "new_date": {"type": "string", "format": "date"},
                "new_time": {"$ref": "#/definitions/TimeWindow"},
            },
            "required": ["id", "rule_id", "target_date", "action"],
        },
    },
    "$ref": "#/definitions/CalendarException",
}


def validate_activity_rule(rule: ActivityRule) -> None:
    """Raises jsonschema.ValidationError if the rule doesn't match the schema."""
    _jsonschema_validate(instance=rule.to_dict(), schema=ACTIVITY_RULE_SCHEMA)


def validate_calendar_exception(exc: CalendarException) -> None:
    """Raises jsonschema.ValidationError if the exception doesn't match the schema."""
    _jsonschema_validate(instance=exc.to_dict(), schema=CALENDAR_EXCEPTION_SCHEMA)
