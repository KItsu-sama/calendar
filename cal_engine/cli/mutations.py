# Commands that modify calendar state

import copy
from typing import Optional
from datetime import date, timedelta

from . import (
    _activity_from_tokens,
    _direct_parser,
    _print_json,
    _source_date,
    log_change,
    parse_duration_minutes,
    add_activity,
    change_activity,
    remove_activity,
    create_exception,
    _parse_cli_date,
    parse_day_date,
    parse_time_range,  
    parse_dm_date,
    CLIError,
    CalendarState,
    ExceptionAction,
    RecurrenceType,
    Recurrence,
    Priority,
    Flexibility,
    MoveOptions,
)

def _complete(result, save_path: Optional[str]) -> int:
    _print_json(result.to_dict())

    if result.status == "REQUIRES_AI":
        return 2

    if save_path:
        result.state.save(save_path)

    log_change(
        result.action,
        activities=[result.activity_id],
        details=f"status={result.status}",
        log_path=(
            __import__("pathlib").Path(save_path).with_name("cal_changes.jsonl")
            if save_path
            else "cal_changes.jsonl"
        ),
    )

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