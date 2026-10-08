"""
Command parser for the `cal` grammar.

    cal --fixed school --daily --time 07:00-11:30 --except sunday
    cal --todo math-homework --min 30m --max 90m --due fri 21:00
    cal --exception coding --date 28/9 --move sat

No LLM involved anywhere in this module — pure deterministic parsing.

Design decisions made to resolve ambiguity in the spec (documented here,
also summarized in STATE.md):

1. `--fixed`/`--todo`/`--optional`/`--temp-fixed`/`--exception` are the five
   *primary* flags; exactly one must be the first token and it determines
   the command's `kind` and consumes the following token as `name`.
2. `--changeable` / `--flexible` are *override* modifiers: they let a
   command started with `--fixed` end up with a different `flexibility`
   value (this is exactly what the spec's own
   `--fixed math-class ... --changeable --move-to sat` example does).
3. Day names are accepted as either a dedicated flag (`--wed`) or as a
   value to `--days`/`--except` (`--days wed sat`). Both paths normalize to
   the same 3-letter uppercase codes used by the schema.
4. Relative day names (`--due fri 21:00`, `--date 28/9`, `--move sat`) are
   resolved against a `reference_date` (defaults to today) into absolute
   dates, since the schema stores absolute ISO dates, not weekday names.
   `reference_date` is an explicit parameter precisely so parsing stays
   deterministic and testable.
"""
from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from datetime import date, timedelta
from typing import List, Optional, Tuple, Union

from .duration import DurationParseError, parse_duration_minutes
from .models import (
    ActivityRule,
    CalendarException,
    ExceptionAction,
    Flexibility,
    MoveOptions,
    Priority,
    Recurrence,
    RecurrenceType,
    TimeConstraints,
    TimeWindow,
)

PRIMARY_FLAGS = {"--fixed", "--todo", "--optional", "--temp-fixed", "--exception"}

_DAY_ALIASES = {
    "mon": "MON", "monday": "MON",
    "tue": "TUE", "tues": "TUE", "tuesday": "TUE",
    "wed": "WED", "wednesday": "WED",
    "thu": "THU", "thur": "THU", "thurs": "THU", "thursday": "THU",
    "fri": "FRI", "friday": "FRI",
    "sat": "SAT", "saturday": "SAT",
    "sun": "SUN", "sunday": "SUN",
}
_WEEKDAY_INDEX = {"MON": 0, "TUE": 1, "WED": 2, "THU": 3, "FRI": 4, "SAT": 5, "SUN": 6}

# flag -> arity: "bool" (0 args), "single" (1 arg), "multi" (>=1 args, greedy
# until next --flag), "due" (2 args: day + time)
_FLAG_ARITY = {
    "--daily": "bool",
    "--time": "single",
    "--duration": "single",
    "--min": "single",
    "--max": "single",
    "--except": "multi",
    "--changeable": "bool",
    "--flexible": "bool",
    "--move-to": "single",
    "--due": "due",
    "--date": "single",
    "--until": "single",
    "--between": "single",
    "--days": "multi",
    "--move": "single",
    "--cancel": "bool",
    "--priority": "single",
}
for _day in _DAY_ALIASES:
    if len(_day) == 3:  # register the 3-letter forms as bare boolean flags (--wed, --sat, ...)
        _FLAG_ARITY[f"--{_day}"] = "bool"

_TIME_RANGE_RE = re.compile(r"^([0-2]?\d:[0-5]\d)-([0-2]?\d:[0-5]\d)$")
_DATE_DM_RE = re.compile(r"^(\d{1,2})/(\d{1,2})$")


class CommandParseError(ValueError):
    """Raised for any malformed `cal` command, with a human-readable reason."""


def _normalize_day(token: str) -> str:
    key = token.strip().lower()
    if key not in _DAY_ALIASES:
        raise CommandParseError(
            f"'{token}' is not a recognized day name (expected mon/tue/wed/.../sun or full names)."
        )
    return _DAY_ALIASES[key]


def _parse_time_range(token: str) -> TimeWindow:
    m = _TIME_RANGE_RE.match(token.strip())
    if not m:
        raise CommandParseError(f"Invalid --time value '{token}'. Expected 'HH:MM-HH:MM'.")
    start, end = m.groups()
    return TimeWindow(start_time=_pad_time(start), end_time=_pad_time(end))


def _pad_time(t: str) -> str:
    h, m = t.split(":")
    return f"{int(h):02d}:{m}"


def _resolve_day_to_date(day_code: str, reference_date: date) -> date:
    target_idx = _WEEKDAY_INDEX[day_code]
    delta = (target_idx - reference_date.weekday()) % 7
    return reference_date + timedelta(days=delta)


def _resolve_dm_to_date(token: str, reference_date: date) -> date:
    m = _DATE_DM_RE.match(token.strip())
    if not m:
        raise CommandParseError(f"Invalid date '{token}'. Expected 'D/M' (e.g. '28/9').")
    day, month = int(m.group(1)), int(m.group(2))
    try:
        candidate = date(reference_date.year, month, day)
    except ValueError as e:
        raise CommandParseError(f"Invalid date '{token}': {e}") from None
    if candidate < reference_date:
        candidate = date(reference_date.year + 1, month, day)
    return candidate



def parse_time_range(token: str) -> TimeWindow:
    """Public parser for direct-manipulation time ranges such as 07:30-12:00."""
    return _parse_time_range(token)


def parse_day_date(token: str, reference_date: date) -> date:
    """Resolve a weekday token to its next occurrence on/after reference_date."""
    return _resolve_day_to_date(_normalize_day(token), reference_date)


def parse_dm_date(token: str, reference_date: date) -> date:
    """Resolve a D/M token to an absolute date, rolling into next year if needed."""
    return _resolve_dm_to_date(token, reference_date)


@dataclass
class _ParsedTokens:
    kind: str
    name: str
    mods: dict
    day_flags: List[str]


def _tokenize(command: str) -> List[str]:
    tokens = shlex.split(command)
    if tokens and tokens[0] == "cal":
        tokens = tokens[1:]
    return tokens


def _scan(tokens: List[str]) -> _ParsedTokens:
    if not tokens:
        raise CommandParseError("Empty command.")
    primary = tokens[0]
    if primary not in PRIMARY_FLAGS:
        raise CommandParseError(
            f"Command must start with one of {sorted(PRIMARY_FLAGS)}, got '{primary}'."
        )
    if len(tokens) < 2 or tokens[1].startswith("--"):
        raise CommandParseError(f"'{primary}' requires a name, e.g. '{primary} my-activity'.")

    name = tokens[1]
    kind = primary[2:]  # "fixed" / "todo" / "optional" / "temp-fixed" / "exception"
    rest = tokens[2:]

    mods: dict = {}
    day_flags: List[str] = []
    i = 0
    while i < len(rest):
        tok = rest[i]
        if not tok.startswith("--"):
            raise CommandParseError(f"Unexpected token '{tok}' (expected a '--flag').")
        if tok not in _FLAG_ARITY:
            raise CommandParseError(f"Unknown flag '{tok}'.")
        arity = _FLAG_ARITY[tok]
        bare_day = tok[2:]
        if arity == "bool" and bare_day in _DAY_ALIASES and len(bare_day) == 3:
            day_flags.append(_normalize_day(bare_day))
            i += 1
            continue
        if arity == "bool":
            mods[tok] = True
            i += 1
        elif arity == "single":
            if i + 1 >= len(rest) or rest[i + 1].startswith("--"):
                raise CommandParseError(f"'{tok}' requires a value.")
            mods[tok] = rest[i + 1]
            i += 2
        elif arity == "multi":
            j = i + 1
            values = []
            while j < len(rest) and not rest[j].startswith("--"):
                values.append(rest[j])
                j += 1
            if not values:
                raise CommandParseError(f"'{tok}' requires at least one value.")
            mods[tok] = values
            i = j
        elif arity == "due":
            if i + 2 >= len(rest):
                raise CommandParseError(f"'{tok}' requires a day and a time, e.g. '--due fri 21:00'.")
            mods[tok] = (rest[i + 1], rest[i + 2])
            i += 3
        else:  # pragma: no cover - defensive
            raise CommandParseError(f"Internal: unhandled arity for '{tok}'.")

    return _ParsedTokens(kind=kind, name=name, mods=mods, day_flags=day_flags)


def _priority(mods: dict, default: Priority) -> Priority:
    if "--priority" not in mods:
        return default
    raw = mods["--priority"].strip().upper()
    try:
        return Priority(raw)
    except ValueError:
        raise CommandParseError(
            f"Invalid --priority '{mods['--priority']}'. Expected one of {[p.value for p in Priority]}."
        ) from None


def _duration_constraints(mods: dict) -> Tuple[Optional[int], Optional[int]]:
    try:
        if "--duration" in mods:
            minutes = parse_duration_minutes(mods["--duration"])
            return minutes, minutes
        min_m = parse_duration_minutes(mods["--min"]) if "--min" in mods else None
        max_m = parse_duration_minutes(mods["--max"]) if "--max" in mods else None
        return min_m, max_m
    except DurationParseError as e:
        raise CommandParseError(str(e)) from None


def parse_command(command: str, reference_date: Optional[date] = None) -> Union[ActivityRule, CalendarException]:
    """
    Parse a single `cal ...` command into an ActivityRule or CalendarException.

    `reference_date` anchors relative day names (--due, --date, --move,
    --until) to absolute dates. Defaults to today.
    """
    ref = reference_date or date.today()
    tokens = _tokenize(command)
    parsed = _scan(tokens)

    if parsed.kind == "exception":
        return _build_exception(parsed, ref)
    return _build_activity_rule(parsed, ref)


def _build_activity_rule(parsed: _ParsedTokens, ref: date) -> ActivityRule:
    mods = parsed.mods
    kind = parsed.kind

    # --- flexibility -------------------------------------------------
    default_flex = {
        "fixed": Flexibility.FIXED,
        "temp-fixed": Flexibility.FIXED,
        "todo": Flexibility.FLEXIBLE,
        "optional": Flexibility.OPTIONAL,
    }[kind]
    flexibility = default_flex
    if mods.get("--changeable"):
        flexibility = Flexibility.CHANGEABLE
    elif mods.get("--flexible"):
        flexibility = Flexibility.FLEXIBLE

    # --- priority ------------------------------------------------------
    default_priority = {
        "fixed": Priority.HIGH,
        "temp-fixed": Priority.HIGH,
        "todo": Priority.NORMAL,
        "optional": Priority.LOW,
    }[kind]
    priority = _priority(mods, default_priority)

    # --- recurrence ------------------------------------------------------
    if kind == "temp-fixed" and "--date" in mods:
        recurrence = Recurrence(type=RecurrenceType.NONE)
        temp_date = _resolve_dm_to_date(mods["--date"], ref)
    else:
        temp_date = None
        if mods.get("--daily"):
            except_days = [_normalize_day(d) for d in mods.get("--except", [])]
            recurrence = Recurrence(type=RecurrenceType.DAILY, except_days=except_days)
        elif parsed.day_flags or "--days" in mods:
            days = list(parsed.day_flags)
            if "--days" in mods:
                days.extend(_normalize_day(d) for d in mods["--days"])
            valid_until = _resolve_dm_to_date(mods["--until"], ref).isoformat() if "--until" in mods else None
            recurrence = Recurrence(type=RecurrenceType.WEEKLY, days_of_week=days, valid_until=valid_until)
        else:
            recurrence = Recurrence(type=RecurrenceType.NONE)

    # --- time constraints ------------------------------------------------
    fixed_interval = _parse_time_range(mods["--time"]) if "--time" in mods else None
    min_m, max_m = _duration_constraints(mods)
    deadline = None
    if "--due" in mods:
        day_tok, time_tok = mods["--due"]
        due_date = _resolve_day_to_date(_normalize_day(day_tok), ref)
        deadline = f"{due_date.isoformat()}T{_pad_time(time_tok)}:00"
    allowed_windows = [_parse_time_range(mods["--between"])] if "--between" in mods else []

    tc = TimeConstraints(
        fixed_interval=fixed_interval,
        allowed_windows=allowed_windows,
        min_duration_minutes=min_m,
        max_duration_minutes=max_m,
        deadline=deadline,
    )
    if temp_date is not None:
        tc.date = temp_date.isoformat()

    move_options = None
    if "--move-to" in mods:
        move_options = MoveOptions(preferred_days=[_normalize_day(mods["--move-to"])])

    return ActivityRule(
        id=parsed.name,
        name=parsed.name,
        flexibility=flexibility,
        priority=priority,
        recurrence=recurrence,
        time_constraints=tc,
        move_options=move_options,
    )


def _build_exception(parsed: _ParsedTokens, ref: date) -> CalendarException:
    mods = parsed.mods
    if "--date" not in mods:
        raise CommandParseError("--exception requires --date.")
    target_date = _resolve_dm_to_date(mods["--date"], ref)

    if mods.get("--cancel") and "--move" in mods:
        raise CommandParseError("--exception cannot combine --cancel and --move.")
    if mods.get("--cancel"):
        action = ExceptionAction.CANCEL
        new_date = None
        new_time = None
    elif "--move" in mods:
        action = ExceptionAction.MOVE
        new_date = _resolve_day_to_date(_normalize_day(mods["--move"]), ref).isoformat()
        new_time = _parse_time_range(mods["--time"]) if "--time" in mods else None
    else:
        raise CommandParseError("--exception requires either --cancel or --move.")

    return CalendarException(
        id=f"{parsed.name}-{target_date.isoformat()}",
        rule_id=parsed.name,
        target_date=target_date.isoformat(),
        action=action,
        new_date=new_date,
        new_time=new_time,
    )


def parse_batch_schedule(
    text: str,
    reference_date: Optional[date] = None,
) -> List[ActivityRule]:
    """
    Parse a batch schedule file.

    Example:

        event "Math"
          mon 07:00-08:30
          tue 09:00-10:30
          wed 07:00-08:30

        event "English"
          mon 14:00-15:00
          wed 14:00-15:00

        event "Lunch"
          daily 12:00-13:00
          except sat sun
    """

    ref = reference_date or date.today()

    rules: List[ActivityRule] = []

    current_name: Optional[str] = None
    current_lines: List[str] = []

    def flush_event() -> None:
        nonlocal current_name, current_lines

        if current_name is None:
            return

        rule = _parse_batch_event(
            current_name,
            current_lines,
            ref,
        )

        rules.append(rule)

        current_name = None
        current_lines = []

    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()

        # Empty lines
        if not line:
            continue

        # Comments
        if line.startswith("#"):
            continue

        if line.lower().startswith("event "):
            flush_event()

            raw_name = line[6:].strip()

            if (
                len(raw_name) >= 2
                and raw_name[0] == '"'
                and raw_name[-1] == '"'
            ):
                raw_name = raw_name[1:-1]

            if not raw_name:
                raise CommandParseError(
                    f"Batch line {line_number}: event requires a name."
                )

            current_name = raw_name
            continue

        if current_name is None:
            raise CommandParseError(
                f"Batch line {line_number}: expected 'event NAME'."
            )

        current_lines.append(line)

    flush_event()

    return rules


def _parse_batch_event(
    name: str,
    lines: List[str],
    ref: date,
) -> ActivityRule:
    """
    Parse one event from a batch schedule.
    """

    if not lines:
        raise CommandParseError(
            f"Batch event '{name}' has no schedule."
        )

    day_time_windows: dict[str, TimeWindow] = {}

    daily_window: Optional[TimeWindow] = None
    except_days: List[str] = []

    recurrence_days: List[str] = []

    for line in lines:
        parts = shlex.split(line)

        if not parts:
            continue

        command = parts[0].lower()

        if command == "daily":
            if len(parts) != 2:
                raise CommandParseError(
                    f"Batch event '{name}': "
                    f"'daily' requires TIME."
                )

            daily_window = _parse_time_range(parts[1])
            continue

        if command == "except":
            if len(parts) < 2:
                raise CommandParseError(
                    f"Batch event '{name}': "
                    f"'except' requires at least one day."
                )

            except_days.extend(
                _normalize_day(day)
                for day in parts[1:]
            )
            continue

        if command in _DAY_ALIASES:
            if len(parts) != 2:
                raise CommandParseError(
                    f"Batch event '{name}': "
                    f"'{command}' requires TIME."
                )

            day = _normalize_day(command)

            if day in day_time_windows:
                raise CommandParseError(
                    f"Batch event '{name}' defines "
                    f"{day} more than once."
                )

            day_time_windows[day] = _parse_time_range(parts[1])
            recurrence_days.append(day)
            continue

        raise CommandParseError(
            f"Batch event '{name}': unknown schedule "
            f"directive '{parts[0]}'."
        )

    # Daily schedule
    if daily_window is not None:
        recurrence = Recurrence(
            type=RecurrenceType.DAILY,
            except_days=sorted(set(except_days)),
        )

        return ActivityRule(
            id=name,
            name=name,
            flexibility=Flexibility.FIXED,
            priority=Priority.HIGH,
            recurrence=recurrence,
            time_constraints=TimeConstraints(
                fixed_interval=daily_window,
            ),
        )

    # Per-weekday schedule
    if day_time_windows:
        recurrence = Recurrence(
            type=RecurrenceType.WEEKLY,
            days_of_week=recurrence_days,
        )

        return ActivityRule(
            id=name,
            name=name,
            flexibility=Flexibility.FIXED,
            priority=Priority.HIGH,
            recurrence=recurrence,
            time_constraints=TimeConstraints(
                day_time_windows=day_time_windows,
            ),
        )

    raise CommandParseError(
        f"Batch event '{name}' has no valid schedule."
    )