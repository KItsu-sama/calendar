from __future__ import annotations

# Argument parsers and action detection

import argparse
import shlex
import sys
from datetime import date, timedelta
from typing import Optional


from ..models import ActivityRule, CalendarException, RecurrenceType
from ..operations import first_occurrence
from ..parser import PRIMARY_FLAGS, parse_command, parse_dm_date
from ..review import review_args
from ..storage import CalendarState

from .errors import CLIError

_MASTER_ACTIONS = (
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
    "--check",
    "--reset",
)

DIRECT_ACTIONS = {
    *_MASTER_ACTIONS,
    "--batch",
    "batch",
    "today",
    "week",
    "--review",
    "--watch",
    "--export-commands",
    "--profile",
    "--relations",
    "--no-snapshot",
}

QUERY_ACTIONS = {
    "--schedule",
    "--free",
    "--conflicts",
    "--preview",
    "--check",
}

MUTATION_ACTIONS = {
    "--add",
    "--remove",
    "--change",
    "--move",
}

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


def _master_parser() -> argparse.ArgumentParser:
    parser = _global_parser()
    commands = parser.add_subparsers(dest="command")
    for action in _MASTER_ACTIONS:
        command = commands.add_parser(action[2:], add_help=False)
        command.add_argument("tokens", nargs=argparse.REMAINDER)
    return parser


def _master_action_index(argv: list[str]) -> tuple[int, str] | None:
    """Find a direct action after the leading global options and their values."""
    global_parser = _global_parser()
    option_actions = global_parser._option_string_actions
    index = 0
    while index < len(argv):
        token = argv[index]
        if token in _MASTER_ACTIONS:
            return index, token

        option = token.partition("=")[0]
        parsed_action = option_actions.get(option)
        if parsed_action is None and option.startswith("--"):
            matches = [
                action
                for option_string, action in option_actions.items()
                if option_string.startswith(option)
            ]
            if len(matches) == 1:
                parsed_action = matches[0]
        if parsed_action is None:
            return None
        if "=" in token or parsed_action.nargs == 0:
            index += 1
        elif parsed_action.nargs == "?":
            index += 1
            if index < len(argv) and not argv[index].startswith("-"):
                index += 1
        elif isinstance(parsed_action.nargs, int):
            index += 1 + parsed_action.nargs
        else:
            index += 2
    return None


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
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Show diagnostic execution tracing on stderr.",
    )
    parser.add_argument("--load", help="Load calendar state JSON before the action.")
    parser.add_argument("--save", help="Save the resulting calendar state JSON after a mutation.")
    parser.add_argument(
        "--reference-date",
        default=date.today().isoformat(),
        help="Anchor date (YYYY-MM-DD or D/M) for omitted dates and weekday names.",
    )
    parser.add_argument("--on", help="Reference date (YYYY-MM-DD or D/M) for review/week commands.")
    review_args(parser)
    return parser


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


def _parse_cli_date(value: str, reference: date) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        return parse_dm_date(value, reference)


def _source_date(state: CalendarState, args, reference: date) -> date:
    if args.date:
        return _parse_cli_date(args.date, reference)
    return first_occurrence(state.require_rule(args.name), reference)


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