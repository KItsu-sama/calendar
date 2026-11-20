"""Calendar-state persistence for direct-manipulation commands.

The CLI is stateless by default. Callers explicitly load/save this JSON document;
within an invocation all calculations use the same immutable-by-convention state.
"""
from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional
from jsonschema.exceptions import ValidationError

from .models import (
    ActivityRule,
    CalendarException,
    activity_rule_from_dict,
    calendar_exception_from_dict,
    validate_activity_rule,
    validate_calendar_exception,
)
from .resolver import ScheduleResolver


class StorageError(ValueError):
    """The calendar state cannot be loaded, saved, or validated."""


@dataclass
class CalendarState:
    """In-memory calendar database with explicit JSON persistence."""

    rules: List[ActivityRule] = field(default_factory=list)
    exceptions: List[CalendarException] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._validate()

    def _validate(self) -> None:
        identifiers = [rule.id for rule in self.rules]
        if len(identifiers) != len(set(identifiers)):
            raise StorageError("Activity rule IDs must be unique.")
        for rule in self.rules:
            try:
                validate_activity_rule(rule)
            except ValidationError as error:
                raise StorageError(f"Invalid activity rule '{rule.id}': {error.message}") from None
        for exception in self.exceptions:
            try:
                validate_calendar_exception(exception)
            except ValidationError as error:
                raise StorageError(f"Invalid exception '{exception.id}': {error.message}") from None
            if self.get_rule(exception.rule_id) is None:
                raise StorageError(
                    f"Exception '{exception.id}' refers to unknown rule '{exception.rule_id}'."
                )

    def copy(self) -> "CalendarState":
        return CalendarState(
            rules=copy.deepcopy(self.rules),
            exceptions=copy.deepcopy(self.exceptions),
        )

    def resolver(self) -> ScheduleResolver:
        return ScheduleResolver(rules=self.rules, exceptions=self.exceptions)

    def get_rule(self, rule_id: str) -> Optional[ActivityRule]:
        return next((rule for rule in self.rules if rule.id == rule_id), None)

    def require_rule(self, rule_id: str) -> ActivityRule:
        rule = self.get_rule(rule_id)
        if rule is None:
            raise StorageError(f"Unknown activity '{rule_id}'.")
        return rule

    def add_rule(self, rule: ActivityRule) -> None:
        if self.get_rule(rule.id) is not None:
            raise StorageError(f"Activity '{rule.id}' already exists.")
        self.rules.append(copy.deepcopy(rule))
        self._validate()

    def replace_rule(self, rule: ActivityRule) -> None:
        index = next((i for i, item in enumerate(self.rules) if item.id == rule.id), None)
        if index is None:
            raise StorageError(f"Unknown activity '{rule.id}'.")
        self.rules[index] = copy.deepcopy(rule)
        self._validate()

    def remove_rule(self, rule_id: str) -> None:
        self.require_rule(rule_id)
        self.rules = [rule for rule in self.rules if rule.id != rule_id]
        self.exceptions = [item for item in self.exceptions if item.rule_id != rule_id]
        self._validate()

    def add_exception(self, exception: CalendarException) -> None:
        if any(item.id == exception.id for item in self.exceptions):
            raise StorageError(f"Exception '{exception.id}' already exists.")
        self.exceptions.append(copy.deepcopy(exception))
        self._validate()

    def to_dict(self) -> dict:
        return {
            "version": 1,
            "rules": [rule.to_dict() for rule in self.rules],
            "exceptions": [exception.to_dict() for exception in self.exceptions],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "CalendarState":
        if not isinstance(data, dict) or data.get("version") != 1:
            raise StorageError("Calendar state must be an object with version 1.")
        if not isinstance(data.get("rules"), list) or not isinstance(data.get("exceptions"), list):
            raise StorageError("Calendar state must contain 'rules' and 'exceptions' arrays.")
        try:
            return cls(
                rules=[activity_rule_from_dict(item) for item in data["rules"]],
                exceptions=[calendar_exception_from_dict(item) for item in data["exceptions"]],
            )
        except (KeyError, TypeError, ValueError) as error:
            raise StorageError(f"Invalid calendar state: {error}") from None

    @classmethod
    def load(cls, path: str | Path) -> "CalendarState":
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise StorageError(f"Could not load calendar state '{path}': {error}") from None
        return cls.from_dict(data)

    def save(self, path: str | Path) -> None:
        destination = Path(path)
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_suffix(destination.suffix + ".tmp")
            temporary.write_text(json.dumps(self.to_dict(), indent=2) + "\n", encoding="utf-8")
            temporary.replace(destination)
        except OSError as error:
            raise StorageError(f"Could not save calendar state '{path}': {error}") from None
