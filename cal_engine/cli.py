"""Command-line interface for parser definitions and Phase 6 operations."""
from __future__ import annotations

import argparse
import copy
import json
import shlex
import sys
from datetime import date, datetime, timedelta
from typing import Optional

from .ai_escalation import build_conflict_prompt
from .duration import parse_duration_minutes
from .models import (
    ActivityRule,
    CalendarException,
    ExceptionAction,
    Flexibility,
    MoveOptions,
    Priority,
    Recurrence,
    RecurrenceType,
)
from .operations import (
    OperationError,
    add_activity,
    change_activity,
    conflicts_in_schedule,
    create_exception,
    first_occurrence,
    preview_activity,
    remove_activity,
)
from .parser import (
    PRIMARY_FLAGS,
    CommandParseError,
    parse_command,
    parse_day_date,
    parse_dm_date,
    parse_time_range,
)
from .storage import CalendarState, StorageError

DIRECT_ACTIONS = {
    "--add",
    "--remove",
    "--change",
    "--move",
    "--cancel",
    "--exception",
    "--schedule",
    "--free",
    "--conflicts",
    "--preview",
    "--generate-prompt",
}


class CLIError(ValueError):
    """The command line is valid shell syntax but invalid cal usage."""


def _print_json(value: dict) -> None:
    print(json.dumps(value, indent=2))


def _parse_cli_date(value: str, reference: date) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        return parse_dm_date(value, reference)


def _global_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python main.py",
        description="cal scheduling engine: definitions, direct manipulation, and schedule inspection.",
        epilog=(
            "Global --load/--save/--reference-date options must precede the action. "
            "Examples:\n"
            "  python main.py --save state.json --add --fixed lunch --daily --time 13:00-14:00\n"
            "  python main.py --load state.json --move math --to sat\n"
            "  python main.py --load state.json --change school --time 07:30-12:00\n"
            "  python main.py --load state.json --schedule --from 21/9 --to 27/9"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--load", help="Load calendar state JSON before the action.")
    parser.add_argument("--save", help="Save the resulting calendar state JSON after a mutation.")
    parser.add_argument(
        "--reference-date",
        default=date.today().isoformat(),
        help="Anchor date (YYYY-MM-DD or D/M) for omitted dates and weekday names.",
    )
    return parser


def _direct_parser(action: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=f"python main.py {action}")
    parser.add_argument("name", help="Activity ID")
    parser.add_argument("--date", help="Source occurrence date (YYYY-MM-DD or D/M).")
    if action == "--move":
        parser.add_argument("--to", required=True, help="Destination weekday.")
    if action == "--exception":
        parser.add_argument("--cancel", action="store_true", help="Cancel this occurrence.")
        parser.add_argument("--move", dest="destination", help="Move to this weekday.")
        parser.add_argument("--time", help="Replacement time range for MODIFY/MOVE.")
    if action == "--change":
        parser.add_argument("--time")
        parser.add_argument("--duration")
        parser.add_argument("--min")
        parser.add_argument("--max")
        parser.add_argument("--between")
        parser.add_argument("--due", nargs=2, metavar=("DAY", "HH:MM"))
        parser.add_argument("--daily", action="store_true")
        parser.add_argument("--days", nargs="+")
        parser.add_argument("--until")
        parser.add_argument("--changeable", action="store_true")
        parser.add_argument("--flexible", action="store_true")
        parser.add_argument("--priority")
        parser.add_argument("--move-to")
    return parser


def _query_parser(action: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=f"python main.py {action}")
    parser.add_argument("--date")
    parser.add_argument("--from", dest="start")
    parser.add_argument("--to", dest="end")
    parser.add_argument("--on", dest="target")
    parser.add_argument("--time", help="Existing conflict window for --conflicts.")
    return parser


def _updated_rule(original: ActivityRule, args, reference: date) -> ActivityRule:
    updated = copy.deepcopy(original)
    changed = False
    if args.time:
        updated.time_constraints.fixed_interval = parse_time_range(args.time)
        changed = True
    if args.duration:
        minutes = parse_duration_minutes(args.duration)
        updated.time_constraints.min_duration_minutes = minutes
        updated.time_constraints.max_duration_minutes = minutes
        changed = True
    if args.min:
        updated.time_constraints.min_duration_minutes = parse_duration_minutes(args.min)
        changed = True
    if args.max:
        updated.time_constraints.max_duration_minutes = parse_duration_minutes(args.max)
        changed = True
    if args.between:
        updated.time_constraints.allowed_windows = [parse_time_range(args.between)]
        changed = True
    if args.due:
        due_day = parse_day_date(args.due[0], reference)
        due_time = parse_time_range(f"{args.due[1]}-23:59").start_time
        updated.time_constraints.deadline = f"{due_day.isoformat()}T{due_time}:00"
        changed = True
    if args.daily and args.days:
        raise CLIError("--change cannot combine --daily and --days.")
    if args.daily:
        updated.recurrence = Recurrence(type=RecurrenceType.DAILY)
        changed = True
    if args.days:
        updated.recurrence = Recurrence(
            type=RecurrenceType.WEEKLY,
            days_of_week=[parse_day_date(day, reference).strftime("%a").upper() for day in args.days],
        )
        changed = True
    if args.until:
        updated.recurrence.valid_until = parse_dm_date(args.until, reference).isoformat()
        changed = True
    if args.changeable and args.flexible:
        raise CLIError("--change cannot combine --changeable and --flexible.")
    if args.changeable:
        updated.flexibility = Flexibility.CHANGEABLE
        changed = True
    if args.flexible:
        updated.flexibility = Flexibility.FLEXIBLE
        changed = True
    if args.priority:
        try:
            updated.priority = Priority(args.priority.upper())
        except ValueError:
            raise CLIError(f"Invalid priority '{args.priority}'.") from None
        changed = True
    if args.move_to:
        updated.move_options = MoveOptions(
            preferred_days=[parse_day_date(args.move_to, reference).strftime("%a").upper()]
        )
        changed = True
    if not changed:
        raise CLIError("--change requires at least one field to update.")
    return updated


def _source_date(state: CalendarState, args, reference: date) -> date:
    if args.date:
        return _parse_cli_date(args.date, reference)
    return first_occurrence(state.require_rule(args.name), reference)


def _query_range(args, reference: date) -> tuple[date, date]:
    start_raw = args.start or args.date
    start = _parse_cli_date(start_raw, reference) if start_raw else reference
    end = _parse_cli_date(args.end, reference) if args.end else start
    if end < start:
        raise CLIError("Query end date must not precede its start date.")
    return start, end


def _pop_value(tokens: list[str], flag: str) -> tuple[list[str], Optional[str]]:
    remaining = list(tokens)
    value = None
    index = 0
    while index < len(remaining):
        if remaining[index] == flag:
            if index + 1 >= len(remaining):
                raise CLIError(f"{flag} requires a value.")
            value = remaining[index + 1]
            del remaining[index : index + 2]
        else:
            index += 1
    return remaining, value


def _activity_from_tokens(tokens: list[str], reference: date) -> tuple[ActivityRule, date]:
    tokens, on_value = _pop_value(tokens, "--on")
    if not tokens or tokens[0] not in PRIMARY_FLAGS:
        raise CLIError("Expected an activity command such as --fixed NAME --time HH:MM-HH:MM.")
    parsed = parse_command("cal " + shlex.join(tokens), reference)
    if isinstance(parsed, CalendarException):
        raise CLIError("Preview/prompt input must be an activity, not an exception.")
    target = _parse_cli_date(on_value, reference) if on_value else None
    if target and parsed.recurrence.type == RecurrenceType.NONE and not hasattr(parsed.time_constraints, "date"):
        parsed.time_constraints.date = target.isoformat()
    effective = (
        first_occurrence(parsed, reference, target)
        if parsed.time_constraints.fixed_interval is not None
        else (target or reference)
    )
    return parsed, effective



def _complete(result, save_path: Optional[str]) -> int:
    _print_json(result.to_dict())
    if result.status == "REQUIRES_AI":
        return 2
    if save_path:
        result.state.save(save_path)
    return 0


def _run_mutation(
    action: str,
    tokens: list[str],
    state: CalendarState,
    reference: date,
    save_path: Optional[str],
) -> int:
    if action == "--add":
        if not tokens:
            raise CLIError("--add requires an activity command.")
        rule, _ = _activity_from_tokens(tokens, reference)
        return _complete(add_activity(state, rule, reference), save_path)

    args = _direct_parser(action).parse_args(tokens)
    source = _source_date(state, args, reference) if action in {"--move", "--cancel", "--exception"} else None
    if action == "--remove":
        return _complete(remove_activity(state, args.name), save_path)
    if action == "--change":
        original = state.require_rule(args.name)
        updated = _updated_rule(original, args, reference)
        target = _parse_cli_date(args.date, reference) if args.date else None
        return _complete(change_activity(state, updated, reference, target), save_path)
    if action == "--move":
        destination = parse_day_date(args.to, source)
        return _complete(
            create_exception(state, args.name, ExceptionAction.MOVE, source, destination),
            save_path,
        )
    if action == "--cancel":
        return _complete(
            create_exception(state, args.name, ExceptionAction.CANCEL, source), save_path
        )

    if args.cancel and args.destination:
        raise CLIError("--exception cannot combine --cancel and --move DAY.")
    if args.cancel:
        if args.time:
            raise CLIError("--exception --cancel cannot include --time.")
        return _complete(
            create_exception(state, args.name, ExceptionAction.CANCEL, source), save_path
        )
    if args.destination:
        destination = parse_day_date(args.destination, source)
        action = ExceptionAction.MOVE
    else:
        if not args.time:
            raise CLIError("--exception requires --move DAY or --time HH:MM-HH:MM.")
        destination = source
        action = ExceptionAction.MODIFY
    window = parse_time_range(args.time) if args.time else None
    return _complete(
        create_exception(
            state, args.name, action, source, destination, window
        ),
        save_path,
    )


def _run_query(
    action: str,
    tokens: list[str],
    state: CalendarState,
    reference: date,
) -> int:
    has_activity = bool(tokens and tokens[0] in PRIMARY_FLAGS)
    if action in {"--preview", "--generate-prompt"} or (action == "--conflicts" and has_activity):
        rule, target = _activity_from_tokens(tokens, reference)
        result = preview_activity(state, rule, target)
        if action == "--preview":
            _print_json(result.to_dict())
            return 0
        if action == "--conflicts":
            _print_json(result.to_dict())
            return 0
        if result.status != "REQUIRES_AI":
            raise CLIError("This activity can be resolved deterministically; no AI prompt is needed.")
        task_pool = [item for item in state.rules if item.time_constraints.fixed_interval is None]
        print(build_conflict_prompt(state.resolver(), rule, target, result.conflicts, task_pool=task_pool))
        return 0

    args = _query_parser(action).parse_args(tokens)
    start, end = _query_range(args, reference)
    if action == "--schedule":
        schedule = state.resolver().build_actual_schedule(start, end)
        _print_json(
            {
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
                "schedule": [item.to_dict() for item in schedule],
            }
        )
        return 0
    if action == "--free":
        window_start = datetime.combine(start, datetime.min.time())
        window_end = datetime.combine(end + timedelta(days=1), datetime.min.time())
        free = state.resolver().calculate_free_time(
            state.resolver().build_actual_schedule(start, end), window_start, window_end
        )
        _print_json(
            {
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
                "free_time": [item.to_dict() for item in free],
            }
        )
        return 0

    conflicts = conflicts_in_schedule(state, start, end)
    if args.time:
        window = parse_time_range(args.time)
        window_start = datetime.combine(start, datetime.min.time().replace(
            hour=int(window.start_time.split(":")[0]), minute=int(window.start_time.split(":")[1])
        ))
        window_end = datetime.combine(start, datetime.min.time().replace(
            hour=int(window.end_time.split(":")[0]), minute=int(window.end_time.split(":")[1])
        ))
        conflicts = [
            item
            for item in conflicts
            if datetime.fromisoformat(item["overlap_start"]) < window_end
            and datetime.fromisoformat(item["overlap_end"]) > window_start
        ]
    _print_json({"start_date": start.isoformat(), "end_date": end.isoformat(), "conflicts": conflicts})
    return 0



def _print_sample() -> None:
    reference = date(2026, 9, 21)
    state = CalendarState(
        rules=[
            parse_command("cal --fixed school --daily --time 07:00-11:30 --except sunday", reference),
            parse_command("cal --fixed lunch --daily --time 13:00-14:00", reference),
            parse_command("cal --fixed math-class --wed --time 15:00-16:00", reference),
        ]
    )
    print("cal scheduling engine - sample week")
    print(f"{reference.isoformat()} to {(reference + timedelta(days=6)).isoformat()}\n")
    for offset in range(7):
        day = reference + timedelta(days=offset)
        schedule = state.resolver().build_actual_schedule(day, day)
        print(f"{day:%a %Y-%m-%d}")
        for item in schedule:
            print(f"  {item.name:<10} {item.start:%H:%M}-{item.end:%H:%M}")
        if not schedule:
            print("  No scheduled activities")
        print("  Free time:")
        for free in state.resolver().free_time_for_day(day):
            print(f"    {free.start:%H:%M}-{free.end:%H:%M}")
        print()


def _execute(argv: list[str]) -> int:
    if argv and argv[0] == "cal":
        argv = argv[1:]
    if not argv:
        _print_sample()
        return 0
    if argv in (["-h"], ["--help"]):
        _global_parser().parse_args(argv)
        return 0

    action = next((token for token in argv if token in DIRECT_ACTIONS), None)
    if action is None:
        parsed = parse_command("cal " + shlex.join(argv))
        _print_json(parsed.to_dict())
        return 0

    action_index = argv.index(action)
    global_args = _global_parser().parse_args(argv[:action_index])
    reference = _parse_cli_date(global_args.reference_date, date.today())
    state = CalendarState.load(global_args.load) if global_args.load else CalendarState()
    tokens = argv[action_index + 1 :]
    if action in {"--add", "--remove", "--change", "--move", "--cancel", "--exception"}:
        return _run_mutation(action, tokens, state, reference, global_args.save)
    if global_args.save:
        raise CLIError("--save is only valid with a mutating action.")
    return _run_query(action, tokens, state, reference)


def main(argv: Optional[list[str]] = None) -> int:
    try:
        return _execute(list(sys.argv[1:] if argv is None else argv))
    except (CLIError, CommandParseError, OperationError, StorageError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2

