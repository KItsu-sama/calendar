"""Interactive command builder for the calendar CLI."""

from __future__ import annotations

import shlex
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable

from jsonschema.exceptions import ValidationError

from .terminal import Terminal

from ..scheduling.duration import parse_duration_minutes
from ..core.models import (
    ActivityRule,
    CalendarException,
    validate_activity_rule,
    validate_calendar_exception,
)
from ..core.parser import (
    parse_command,
    parse_day_date,
    parse_dm_date,
    parse_time_range,
)
from ..core.storage import CalendarState


class _InteractionCancelled(Exception):
    pass


class Help:
    def __init__(
        self,
        calendar_path: str | Path | None = None,
        debug_enabled: bool = False,
    ) -> None:
        self.python = sys.executable
        self.entrypoint = Path(__file__).resolve().parents[2] / "main.py"
        self.calendar_path = Path(calendar_path) if calendar_path else self._default_calendar_path()
        self.terminal = Terminal()
        self.debug_enabled = debug_enabled

    @staticmethod
    def _calendar_candidates() -> list[Path]:
        roots = [Path.cwd(), Path(__file__).resolve().parents[1]]
        candidates = []
        seen = set()
        for root in roots:
            standard = (root / "data" / "calendar.json", root / "calendar.json")
            extras = [
                path
                for path in sorted(root.glob("*.json")) + sorted((root / "data").glob("*.json"))
                if "calendar" in path.name.lower()
            ]
            for candidate in (*standard, *extras):
                resolved = candidate.resolve()
                if resolved not in seen:
                    candidates.append(candidate)
                    seen.add(resolved)
        return candidates

    @classmethod
    def _default_calendar_path(cls) -> Path:
        for candidate in cls._calendar_candidates():
            if candidate.is_file():
                return candidate
        return Path.cwd() / "calendar.json"

    def _available_calendars(self) -> list[Path]:
        candidates = self._calendar_candidates()
        return [path for path in candidates if path.is_file()]

    @staticmethod
    def _input(prompt: str) -> str:
        try:
            return input(prompt).strip()
        except (EOFError, KeyboardInterrupt):
            raise _InteractionCancelled from None

    def _run(self, args: list[str]) -> int:
        if self.debug_enabled and "--debug" not in args:
            args = [*args, "--debug"]
        result = subprocess.run([self.python, str(self.entrypoint), *args])
        return result.returncode

    def _run_query(self, args: list[str]) -> int:
        prefix = ["--load", str(self.calendar_path)] if self.calendar_path.is_file() else []
        return self._run([*prefix, *args])

    def _run_mutation(self, args: list[str]) -> int:
        prefix = ["--load", str(self.calendar_path)] if self.calendar_path.is_file() else []
        return self._run([*prefix, "--save", str(self.calendar_path), *args])

    def _ask_choice(self, prompt: str, choices: set[str]) -> str:
        while True:
            answer = self._input(prompt)
            if answer in choices:
                return answer
            self.terminal.warning(f"Choose one of: {', '.join(sorted(choices))}.")

    def _ask_yes_no(self, prompt: str) -> bool:
        while True:
            answer = self._input(prompt).lower()
            if answer in {"y", "yes"}:
                return True
            if answer in {"n", "no"}:
                return False
            self.terminal.warning("Enter y or n.")

    def _ask_value(
        self,
        prompt: str,
        validator: Callable[[str], None],
        optional: bool = False,
    ) -> str:
        while True:
            value = self._input(prompt)
            if optional and not value:
                return ""
            try:
                validator(value)
                return value
            except ValueError as error:
                self.terminal.error(f"Invalid value: {error}")

    @staticmethod
    def _validate_clock(value: str) -> None:
        datetime.strptime(value, "%H:%M")

    @classmethod
    def _validate_time_range(cls, value: str) -> None:
        window = parse_time_range(value)
        start = datetime.strptime(window.start_time, "%H:%M")
        end = datetime.strptime(window.end_time, "%H:%M")
        if start >= end:
            raise ValueError("the end time must be later than the start time.")

    @staticmethod
    def _validate_duration(value: str) -> None:
        if parse_duration_minutes(value) <= 0:
            raise ValueError("duration must be greater than zero.")

    @staticmethod
    def _validate_date(value: str) -> None:
        parse_dm_date(value, date.today())

    @staticmethod
    def _validate_day(value: str) -> None:
        parse_day_date(value, date.today())

    @classmethod
    def _validate_days(cls, value: str) -> None:
        days = value.split()
        if not days:
            raise ValueError("enter at least one weekday.")
        for day in days:
            cls._validate_day(day)

    @staticmethod
    def _validate_definition(tokens: list[str]) -> None:
        parsed = parse_command("cal " + shlex.join(tokens))
        if isinstance(parsed, ActivityRule):
            validate_activity_rule(parsed)
        elif isinstance(parsed, CalendarException):
            validate_calendar_exception(parsed)

    def _display_calendars(self) -> None:
        calendars = self._available_calendars()
        self.terminal.rule("Calendars")
        if calendars:
            self.terminal.info("Available calendar files:")
            for path in calendars:
                print(f"  {path}")
        else:
            self.terminal.info(
                "No calendar files found; a new calendar will be created at:"
            )
            print(f"  {self.calendar_path}")
        self.terminal.info(f"Active calendar: {self.calendar_path}")

    def _select_calendar(self) -> int:
        calendars = self._available_calendars()
        if not calendars:
            self.terminal.warning("No existing calendar files were found.")
            path = self._input("Calendar file path for the new calendar: ")
            if not path:
                self.terminal.error("Calendar path required.")
                return 2
            self.calendar_path = Path(path)
            return 0

        self.terminal.info("Available calendar files:")
        for index, path in enumerate(calendars, start=1):
            marker = " (active)" if path.resolve() == self.calendar_path.resolve() else ""
            print(f"{index}. {path}{marker}")
        self.terminal.print("Enter a number or a path to another existing calendar.")
        while True:
            selected = self._input("> ")
            if selected.isdigit() and 1 <= int(selected) <= len(calendars):
                candidate = calendars[int(selected) - 1]
            else:
                candidate = Path(selected).expanduser()
            if candidate.is_file():
                try:
                    CalendarState.load(candidate)
                except ValueError as error:
                    self.terminal.error(f"Could not use calendar: {error}")
                    continue
                self.calendar_path = candidate
                return 0
            self.terminal.warning("Choose a listed file or enter the path to an existing calendar.")

    def assist(self, error: str | None = None) -> int:
        """Interactive helper for CLI parse/usage errors."""
        try:
            return self._assist(error)
        except _InteractionCancelled:
            print()
            return 2

    def _assist(self, error: str | None = None) -> int:
        if error:
            self.terminal.console.print(f"[error]Error:[/error] {error}", file=sys.stderr)
            print(file=sys.stderr)
        self._display_calendars()
        while True:
            self.terminal.rule("What do you want to do?")
            print("1. Show command help")
            print("2. View today's schedule")
            print("3. View this week's schedule")
            print("4. Create an activity")
            print("5. Change or remove activities")
            print("6. Select a calendar file")
            print("7. Exit")
            choice = self._ask_choice("> ", {"1", "2", "3", "4", "5", "6", "7"})
            if choice == "1":
                return self._run(["-h"])
            if choice == "2":
                return self._run_query(["--schedule", "--date", date.today().isoformat()])
            if choice == "3":
                today = date.today()
                monday = today - timedelta(days=today.weekday())
                sunday = monday + timedelta(days=6)
                return self._run_query([
                    "--schedule", "--from", monday.isoformat(), "--to", sunday.isoformat()
                ])
            if choice == "4":
                return self.create_activity()
            if choice == "5":
                return self.manage_activities()
            if choice == "6":
                self._select_calendar()
                self._display_calendars()
            if choice == "7":
                return 0

    def help(self) -> int:
        """Backward-compatible entry point."""
        return self.assist()

    def create_activity(self) -> int:
        """Interactive helper for creating an activity."""
        try:
            return self._create_activity()
        except _InteractionCancelled:
            print()
            return 2

    def _create_activity(self) -> int:
        self.terminal.rule("Create activity")
        print("1. Fixed activity (scheduled at specific times)")
        print("2. Todo activity (flexible duration, with deadline)")
        print("3. Optional activity (low priority, flexible scheduling)")
        print("4. Temporary fixed activity (one-time fixed schedule)")
        print("5. Exception (cancel or move an occurrence)")
        print("6. Back")
        print("7. Exit")
        choice = self._ask_choice("> ", {"1", "2", "3", "4", "5", "6", "7"})
        builders = {
            "1": self.build_fixed,
            "2": self.build_todo,
            "3": self.build_optional,
            "4": self.build_temp_fixed,
            "5": self.build_exception,
            "6": self._assist,
        }
        return builders[choice]() if choice in builders else 0

    def _ask_name(self) -> str:
        while True:
            name = self._input("Enter activity name: ")
            if name:
                return name
            self.terminal.error("Name required.")

    def _ask_recurrence(self, allow_once: bool = True) -> list[str]:
        self.terminal.rule("Recurrence")
        print("1. Daily (--daily)")
        print("2. Weekly on specific days (--days)")
        if allow_once:
            print("3. Once (no recurrence)")
        choices = {"1", "2", "3"} if allow_once else {"1", "2"}
        choice = self._ask_choice("> ", choices)
        if choice == "1":
            args = ["--daily"]
            excluded = self._ask_value(
                "Except days (e.g. sat sun), or Enter to skip: ",
                self._validate_days,
                optional=True,
            )
            if excluded:
                args.extend(["--except", *excluded.split()])
            return args
        if choice == "2":
            days = self._ask_value("Enter days (e.g. mon wed fri): ", self._validate_days)
            args = ["--days", *days.split()]
            until = self._ask_value(
                "Until date (D/M, or Enter to skip): ",
                self._validate_date,
                optional=True,
            )
            if until:
                args.extend(["--until", until])
            return args
        return []

    def _ask_priority(self) -> list[str]:
        while True:
            priority = self._input("Priority (HIGH/NORMAL/LOW), or Enter to skip: ").upper()
            if not priority:
                return []
            if priority in {"HIGH", "NORMAL", "LOW"}:
                return ["--priority", priority]
            print("Choose HIGH, NORMAL, or LOW.")

    def _ask_modifiers(self) -> list[str]:
        args = []
        if self._ask_yes_no("Changeable? (y/n): "):
            args.append("--changeable")
        if self._ask_yes_no("Flexible? (y/n): "):
            args.append("--flexible")
        move_to = self._ask_value(
            "Preferred move-to day, or Enter to skip: ",
            self._validate_day,
            optional=True,
        )
        if move_to:
            args.extend(["--move-to", move_to])
        args.extend(self._ask_priority())
        return args

    def _ask_duration_constraints(self) -> list[str]:
        print("Duration:")
        print("1. Exact duration (--duration)")
        print("2. Minimum and maximum (--min/--max)")
        print("3. Skip duration")
        choice = self._ask_choice("> ", {"1", "2", "3"})
        if choice == "1":
            duration = self._ask_value("Duration (e.g. 2h, 90m): ", self._validate_duration)
            return ["--duration", duration]
        if choice == "2":
            minimum = self._ask_value(
                "Minimum duration (e.g. 30m): ", self._validate_duration
            )
            maximum = self._ask_value(
                "Maximum duration (e.g. 2h): ", self._validate_duration
            )
            while parse_duration_minutes(maximum) < parse_duration_minutes(minimum):
                print("Maximum duration must be at least the minimum.")
                maximum = self._ask_value(
                    "Maximum duration (e.g. 2h): ", self._validate_duration
                )
            return ["--min", minimum, "--max", maximum]
        return []

    def _confirm_and_add(self, definition: list[str]) -> int:
        try:
            self._validate_definition(definition)
        except (ValueError, ValidationError) as error:
            self.terminal.error(f"Invalid activity: {error}")
            return 2
        print(f"Preview: {' '.join(shlex.quote(token) for token in definition)}")
        result = self._run_query(["--preview", *definition])
        if result:
            return result
        if not self._ask_yes_no("Add this activity to the active calendar? (y/n): "):
            self.terminal.info("No changes made.")
            return 0
        return self._run_mutation(["--add", *definition])

    def build_fixed(self) -> int:
        definition = ["--fixed", self._ask_name(), *self._ask_recurrence()]
        definition.extend([
            "--time",
            self._ask_value("Time range (HH:MM-HH:MM): ", self._validate_time_range),
        ])
        if self._ask_yes_no("Changeable? (y/n): "):
            definition.append("--changeable")
        if self._ask_yes_no("Flexible? (y/n): "):
            definition.append("--flexible")
        move_to = self._ask_value(
            "Preferred move-to day, or Enter to skip: ",
            self._validate_day,
            optional=True,
        )
        if move_to:
            definition.extend(["--move-to", move_to])
        definition.extend(self._ask_priority())
        return self._confirm_and_add(definition)

    def build_todo(self) -> int:
        definition = ["--todo", self._ask_name()]
        definition.extend(self._ask_duration_constraints())
        print("Due date:")
        due_day = self._ask_value("Day (mon/tue/.../sun): ", self._validate_day)
        due_time = self._ask_value("Time (HH:MM): ", self._validate_clock)
        definition.extend(["--due", due_day, due_time])
        between = self._ask_value(
            "Allowed time window, or Enter to skip: ",
            self._validate_time_range,
            optional=True,
        )
        if between:
            definition.extend(["--between", between])
        definition.extend(self._ask_priority())
        return self._confirm_and_add(definition)

    def build_optional(self) -> int:
        definition = ["--optional", self._ask_name(), *self._ask_recurrence()]
        time_range = self._ask_value(
            "Time range (HH:MM-HH:MM), or Enter to skip: ",
            self._validate_time_range,
            optional=True,
        )
        if time_range:
            definition.extend(["--time", time_range])
        definition.extend(self._ask_duration_constraints())
        between = self._ask_value(
            "Allowed time window, or Enter to skip: ",
            self._validate_time_range,
            optional=True,
        )
        if between:
            definition.extend(["--between", between])
        if self._ask_yes_no("Add a due day and time? (y/n): "):
            due_day = self._ask_value("Due day (mon/tue/.../sun): ", self._validate_day)
            due_time = self._ask_value("Due time (HH:MM): ", self._validate_clock)
            definition.extend(["--due", due_day, due_time])
        definition.extend(self._ask_priority())
        return self._confirm_and_add(definition)

    def build_temp_fixed(self) -> int:
        definition = ["--temp-fixed", self._ask_name()]
        date_value = self._ask_value("Date (D/M): ", self._validate_date)
        definition.extend(["--date", date_value])
        time_range = self._ask_value(
            "Time range (HH:MM-HH:MM): ", self._validate_time_range
        )
        definition.extend(["--time", time_range])
        definition.extend(self._ask_modifiers())
        return self._confirm_and_add(definition)

    def build_exception(self) -> int:
        name = self._ask_name()
        date_value = self._ask_value(
            "Date of occurrence (D/M): ", self._validate_date
        )
        self.terminal.rule("Action")
        print("1. Cancel this occurrence")
        print("2. Move this occurrence")
        action = self._ask_choice("> ", {"1", "2"})
        definition = ["--exception", name, "--date", date_value]
        if action == "1":
            definition.append("--cancel")
        else:
            move_day = self._ask_value("Move to day (mon/tue/.../sun): ", self._validate_day)
            definition.extend(["--move", move_day])
            time_range = self._ask_value(
                "New time range, or Enter to skip: ",
                self._validate_time_range,
                optional=True,
            )
            if time_range:
                definition.extend(["--time", time_range])
        try:
            self._validate_definition(definition)
        except (ValueError, ValidationError) as error:
            self.terminal.error(f"Invalid exception: {error}")
            return 2
        print(f"Preview: {' '.join(shlex.quote(token) for token in definition)}")
        if not self._ask_yes_no("Apply this exception to the active calendar? (y/n): "):
            self.terminal.info("No changes made.")
            return 0
        return self._run_mutation(definition)

    def manage_activities(self) -> int:
        try:
            return self._manage_activities()
        except _InteractionCancelled:
            print()
            return 2

    def _manage_activities(self) -> int:
        if not self.calendar_path.is_file():
            self.terminal.error(f"Calendar file does not exist: {self.calendar_path}")
            return 2
        try:
            state = CalendarState.load(self.calendar_path)
        except ValueError as error:
            self.terminal.error(f"Could not load calendar: {error}")
            return 2
        if not state.rules:
            self.terminal.info("The active calendar has no activities.")
            return 0

        self.terminal.rule("Activities")
        for index, rule in enumerate(state.rules, start=1):
            print(f"{index}. {rule.name} ({rule.id})")
        while True:
            selection = self._input("Select an activity number, or 0 to go back: ")
            if selection == "0":
                return 0
            if selection.isdigit() and 1 <= int(selection) <= len(state.rules):
                rule = state.rules[int(selection) - 1]
                break
            self.terminal.warning("Select a listed activity number.")

        self.terminal.rule("What would you like to change?")
        print("1. Change time")
        print("2. Change priority")
        print("3. Change recurrence")
        print("4. Remove activity")
        print("5. Back")
        action = self._ask_choice("> ", {"1", "2", "3", "4", "5"})
        if action == "5":
            return 0
        if action == "4":
            print(f"Preview: remove activity {rule.id}")
            if not self._ask_yes_no("Remove this activity? (y/n): "):
                self.terminal.info("No changes made.")
                return 0
            return self._run_mutation(["--remove", rule.id])

        if action == "1":
            value = self._ask_value(
                "New time range (HH:MM-HH:MM): ", self._validate_time_range
            )
            change = ["--time", value]
        elif action == "2":
            options = self._ask_priority()
            if not options:
                self.terminal.info("Priority was not changed.")
                return 0
            change = options
        else:
            recurrence = self._ask_recurrence(allow_once=False)
            change = recurrence
        command = ["--change", rule.id, *change]
        print(f"Preview: {' '.join(shlex.quote(token) for token in command)}")
        if not self._ask_yes_no("Apply this change? (y/n): "):
            self.terminal.info("No changes made.")
            return 0
        return self._run_mutation(command)
