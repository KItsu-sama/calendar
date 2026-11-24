# cal_engine/ui/commands.py
"""The GUI's command bar: the CLI grammar, run against an in-memory calendar.

Nothing here touches Tk or the disk. ``run_command`` takes the current
``CalendarState`` and returns a ``CommandResult``; when the command changed the
calendar, ``result.state`` is the new state and the caller decides how to save
it (the window saves it atomically, with undo).

It reuses the CLI's own parsers and the engine's operations, so a command
behaves exactly as it does on the command line:

    add --fixed gym --days mon wed --time 17:00-18:00
    add --fixed lunch --daily --time 12:00-13:00 --except sunday
    add --temp-fixed dentist --date 18/11 --time 07:00-08:00
    add --todo homework --days mon wed fri --min 30m --max 90m --due fri 21:00
    move gym --to sat --date 2026-11-23
    cancel gym --date 2026-11-18
    exception gym --date 2026-11-25 --time 18:00-19:00
    change gym --time 18:00-19:00
    remove gym
    schedule --from 2026-11-16 --to 2026-11-22
    free --date 2026-11-18
    conflicts --from 2026-11-16 --to 2026-11-22
    preview --fixed gym --days wed --time 17:00-18:00 --on 2026-11-18

A leading ``cal``/``--`` is optional. An activity may be named by its ID or, if
that is unambiguous, by its name.
"""
from __future__ import annotations

import contextlib
import io
import shlex
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from ..cli.arguments import _activity_from_tokens, _direct_parser, _parse_cli_date, _query_parser, _source_date
from ..cli.errors import CLIError
from ..cli.mutations import _updated_rule
from ..cli.queries import _query_range
from ..core.models import ExceptionAction
from ..core.operations import (
    OperationError,
    add_activity,
    change_activity,
    conflicts_in_schedule,
    create_exception,
    preview_activity,
    remove_activity,
)
from ..core.parser import CommandParseError, parse_day_date, parse_time_range
from ..core.storage import CalendarState, StorageError
from ..groups.group_schedule import GENERATED_PREFIX

MUTATIONS = {"add", "remove", "change", "move", "cancel", "exception"}
QUERIES = {"schedule", "free", "conflicts", "check", "preview"}
PRIMARY = {"fixed", "todo", "optional", "temp-fixed"}

HELP = """Commands (a leading "cal" or "--" is optional)

Add
  add --fixed gym --days mon wed --time 17:00-18:00
  add --fixed lunch --daily --time 12:00-13:00 --except sunday
  add --temp-fixed dentist --date 18/11 --time 07:00-08:00
  add --todo homework --days mon wed fri --min 30m --max 90m --due fri 21:00

Change one day only
  cancel gym --date 2026-11-18
  move gym --to sat --date 2026-11-23
  exception gym --date 2026-11-25 --time 18:00-19:00

Change the rule
  change gym --time 18:00-19:00 --days tue thu
  remove gym

Look
  schedule --from 2026-11-16 --to 2026-11-22
  free --date 2026-11-18
  conflicts --from 2026-11-16 --to 2026-11-22
  preview --fixed gym --days wed --time 17:00-18:00 --on 2026-11-18

Name an activity by its ID or by its name. Timetable slots are edited on the grid."""


@dataclass
class CommandResult:
    ok: bool
    text: str
    state: CalendarState | None = None   # the new calendar, when the command changed it
    action: str = ""
    activity_id: str = ""
    focus: date | None = None            # a date worth showing afterwards

    @property
    def changed(self) -> bool:
        return self.state is not None


def _fail(message: str) -> CommandResult:
    return CommandResult(False, message)


def _when(item) -> str:
    start, end = item.start, item.end
    stop = "24:00" if end.date() > start.date() and end.hour == 0 and end.minute == 0 else f"{end:%H:%M}"
    return f"{start:%a %d %b}  {start:%H:%M}–{stop}"


def _tokens(text: str) -> list[str]:
    tokens = shlex.split(text.strip())
    while tokens and tokens[0].lower() in {"cal", "python", "main.py", "python3"}:
        tokens.pop(0)
    if not tokens:
        raise CLIError("Type a command, or ? for examples.")
    verb = tokens[0].lstrip("-").lower()
    if verb not in MUTATIONS | QUERIES:
        raise CLIError(f"“{tokens[0]}” isn’t a command. Try add, cancel, move, change, remove, schedule, free, conflicts or preview.")
    rest = tokens[1:]
    if rest and rest[0].lstrip("-").lower() in PRIMARY:
        rest[0] = "--" + rest[0].lstrip("-").lower()
    return ["--" + verb] + rest


def _resolve(state: CalendarState, token: str) -> str:
    """An ID, or a name that matches exactly one activity."""
    if state.get_rule(token):
        return token
    matches = [r for r in state.rules if r.name.casefold() == token.casefold()]
    if len(matches) == 1:
        return matches[0].id
    if len(matches) > 1:
        raise CLIError(f"More than one activity is called “{token}”. Use its ID: " + ", ".join(r.id for r in matches[:4]))
    raise CLIError(f"No activity called “{token}”.")


def _guard(rule_id: str) -> None:
    if rule_id.startswith(GENERATED_PREFIX):
        raise CLIError("That belongs to a timetable. Edit it from its slot on the grid.")


def _summary(result) -> str:
    lines = [f"{result.status}: {result.action.lower()} {result.activity_id}"]
    if result.reason:
        lines.append(result.reason)
    for change in result.changes:
        lines.append(f"  moves {change.activity_id} to {change.new_start:%a %d %b %H:%M}–{change.new_end:%H:%M}")
    for conflict in result.conflicts:
        lines.append(f"  conflicts with {conflict.name}  {_when(conflict)}")
    return "\n".join(lines)


def _mutate(action: str, rest: list[str], state: CalendarState, reference: date) -> CommandResult:
    if action == "--add":
        if not rest:
            raise CLIError("add needs an activity, e.g. add --fixed gym --weekly --days mon wed --time 17:00-18:00")
        rule, _ = _activity_from_tokens(rest, reference)
        result = add_activity(state, rule, reference)
    else:
        if not rest or rest[0].startswith("--"):
            raise CLIError(f"{action[2:]} needs the activity’s name or ID first.")
        rest = [_resolve(state, rest[0])] + rest[1:]
        _guard(rest[0])
        args = _direct_parser(action).parse_args(rest)
        if action == "--remove":
            result = remove_activity(state, args.name)
        elif action == "--change":
            updated = _updated_rule(state.require_rule(args.name), args, reference)
            target = _parse_cli_date(args.date, reference) if args.date else None
            result = change_activity(state, updated, reference, target)
        else:
            source = _source_date(state, args, reference)
            if action == "--move":
                result = create_exception(state, args.name, ExceptionAction.MOVE, source, parse_day_date(args.to, source))
            elif action == "--cancel":
                result = create_exception(state, args.name, ExceptionAction.CANCEL, source)
            else:
                if args.cancel and args.destination:
                    raise CLIError("exception can’t combine --cancel and --move.")
                if args.cancel:
                    result = create_exception(state, args.name, ExceptionAction.CANCEL, source)
                elif args.destination:
                    window = parse_time_range(args.time) if args.time else None
                    result = create_exception(state, args.name, ExceptionAction.MOVE, source,
                                              parse_day_date(args.destination, source), window)
                elif args.time:
                    result = create_exception(state, args.name, ExceptionAction.MODIFY, source, source,
                                              parse_time_range(args.time))
                else:
                    raise CLIError("exception needs --cancel, --move DAY or --time HH:MM-HH:MM.")
    text = _summary(result)
    if result.status == "REQUIRES_AI":
        return CommandResult(False, text + "\nThis needs a judgment call, so nothing was changed. "
                                           "On the command line, --generate-prompt writes the prompt for it.",
                             action=result.action, activity_id=result.activity_id)
    focus = None
    for item in result.state.resolver().build_actual_schedule(reference, reference + timedelta(days=60)):
        if item.activity_id == result.activity_id:
            focus = item.start.date()
            break
    return CommandResult(True, text, result.state, result.action, result.activity_id, focus)


def _query(action: str, rest: list[str], state: CalendarState, reference: date) -> CommandResult:
    if action == "--preview" or (action == "--conflicts" and rest and rest[0].startswith("--") and rest[0][2:] in PRIMARY):
        rule, target = _activity_from_tokens(rest, reference)
        return CommandResult(True, _summary(preview_activity(state, rule, target)), focus=target)
    args = _query_parser(action).parse_args(rest)
    if action == "--check":
        start, end = reference, reference + timedelta(days=6)
    else:
        start, end = _query_range(args, reference)
    if action == "--schedule":
        items = state.resolver().build_actual_schedule(start, end)
        lines = [f"{_when(i)}  {i.name}" for i in sorted(items, key=lambda i: i.start)]
        return CommandResult(True, "\n".join(lines) or "Nothing scheduled.", focus=start)
    if action == "--free":
        window = (datetime.combine(start, datetime.min.time()), datetime.combine(end + timedelta(days=1), datetime.min.time()))
        resolver = state.resolver()
        free = resolver.calculate_free_time(resolver.build_actual_schedule(start, end), *window)
        lines = [f"{_when(i)}  ({int((i.end - i.start).total_seconds() // 60)} min)" for i in free]
        return CommandResult(True, "\n".join(lines) or "No free time.", focus=start)
    found = conflicts_in_schedule(state, start, end)
    if args.time:
        window = parse_time_range(args.time)
        lo, hi = (int(p) for p in window.start_time.split(":")), (int(p) for p in window.end_time.split(":"))
        lo_min, hi_min = lo[0] * 60 + lo[1], hi[0] * 60 + hi[1]
        found = [c for c in found
                 if datetime.fromisoformat(c["overlap_start"]).hour * 60 + datetime.fromisoformat(c["overlap_start"]).minute < hi_min
                 and datetime.fromisoformat(c["overlap_end"]).hour * 60 + datetime.fromisoformat(c["overlap_end"]).minute > lo_min]
    lines = [f"{datetime.fromisoformat(c['overlap_start']):%a %d %b  %H:%M}–{datetime.fromisoformat(c['overlap_end']):%H:%M}"
             f"  {c['left']['name']} × {c['right']['name']}" for c in found]
    return CommandResult(True, "\n".join(lines) or "No conflicts.", focus=start)


def run_command(text: str, state: CalendarState, reference: date | None = None) -> CommandResult:
    """Run one command. Never raises for bad input; returns ``ok=False`` with a plain message."""
    reference = reference or date.today()
    if text.strip() in {"?", "help", "--help"}:
        return CommandResult(True, HELP)
    errors = io.StringIO()
    try:
        with contextlib.redirect_stderr(errors):
            tokens = _tokens(text)
            action, rest = tokens[0], tokens[1:]
            if action[2:] in MUTATIONS:
                return _mutate(action, rest, state, reference)
            return _query(action, rest, state, reference)
    except SystemExit:  # argparse reports usage errors by exiting
        message = errors.getvalue().strip().splitlines()
        return _fail(message[-1].split(": ", 1)[-1] if message else "That command isn’t valid. Type ? for examples.")
    except (CLIError, OperationError, CommandParseError, StorageError, ValueError, KeyError) as exc:
        return _fail(str(exc).strip("'\""))
