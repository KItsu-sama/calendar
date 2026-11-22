# Command routing

import argparse
import json
from datetime import date
import shlex
from pathlib import Path

from ..scheduling.change_log import log_change
from ..core.parser import parse_command
from ..scheduling.review import review_handle
from ..core.storage import CalendarState

from .arguments import (
    DIRECT_ACTIONS,
    _global_parser,
    _master_action_index,
    _master_parser,
    _parse_cli_date,
    _print_sample,
)
from ..ui.helpper import Help
from .errors import CLIError
from .mutations import _run_mutation
from .output import _print_json
from .queries import _list_calendars, _run_query
from .special import _run_batch, _run_today, _run_week
from ..debuger.trace import trace_stage


def _execute_command(argv: list[str]) -> int:
    if argv and argv[0] == "cal":
        argv = argv[1:]

    if argv and argv[0] in {"group-schedule", "--group-schedule"}:
        group_parser = argparse.ArgumentParser(
            prog="python main.py group-schedule",
            description="Edit and export reusable group timetables.",
        )
        group_parser.add_argument(
            "--export-config",
            metavar="FILE",
            help="Export an existing group/schedule configuration into a calendar.",
        )
        group_parser.add_argument(
            "--calendar",
            default="data/calendar.json",
            help="Calendar JSON destination for --export-config (default: data/calendar.json).",
        )
        with trace_stage("group_schedule.parse"):
            group_args = group_parser.parse_args(argv[1:])
        from ..groups.group_schedule import load_config, export_to_calendar

        if group_args.export_config:
            count = export_to_calendar(
                load_config(group_args.export_config),
                group_args.calendar,
            )
            print(json.dumps({
                "status": "EXPORTED",
                "count": count,
                "calendar": str(Path(group_args.calendar)),
            }, indent=2))
            return 0
        from ..groups.group_schedule_gui import main as group_editor_main
        group_editor_main()
        return 0

    if argv in (["gui"], ["--gui"]):
        from ..ui.gui import main as gui_main
        gui_main()
        return 0

    if not argv:
        return Help().assist()

    if argv in (["-h"], ["--help"]):
        with trace_stage("help.parse"):
            _global_parser().print_help()
        return 0

    # ------------------------------------------------------------
    # Special commands
    # ------------------------------------------------------------

    if argv[0] in {"today", "week"}:
        with trace_stage("date_options.parse"):
            global_args = _global_parser().parse_args([])

        reference = _parse_cli_date(
            global_args.reference_date,
            date.today(),
        )

        with trace_stage("state.load"):
            state = CalendarState.load(
                global_args.load
            ) if global_args.load else CalendarState()

        if argv[0] == "today":
            with trace_stage("schedule.today"):
                return _run_today(state, reference)

        with trace_stage("schedule.week"):
            return _run_week(state, reference)


    # ------------------------------------------------------------
    # Batch
    # ------------------------------------------------------------

    batch_index = next(
        (i for i, token in enumerate(argv) if token in {"batch", "--batch"}),
        None,
    )

    if batch_index is not None:
        with trace_stage("batch.options.parse"):
            global_args = _global_parser().parse_args(argv[:batch_index])
        remaining = argv[batch_index + 1:]

        if len(remaining) != 1:
            raise CLIError(
                "Batch requires exactly one schedule file.\n"
                "Example: python main.py --save data//calendar.json --batch schedule.txt"
            )

        reference = _parse_cli_date(
            global_args.reference_date,
            date.today(),
        )

        with trace_stage("state.load"):
            state = (
                CalendarState.load(global_args.load)
                if global_args.load
                else CalendarState()
            )

        with trace_stage("batch.run"):
            return _run_batch(
                remaining[0],
                state,
                reference,
                global_args.save,
            )


    # ------------------------------------------------------------
    # Existing command system
    # ------------------------------------------------------------

    with trace_stage("action.detect"):
        detected_action = next((token for token in argv if token in DIRECT_ACTIONS), None)
    if detected_action is None:
        with trace_stage("definition.parse"):
            parsed = parse_command("cal " + shlex.join(argv))
        _print_json(parsed.to_dict())
        return 0

    # review/export/watch commands: parse the full argv so flags
    # like --export-commands FILE, --profile, --relations, --no-snapshot are seen.
    if detected_action in {"--review", "--watch", "--export-commands", "--profile", "--relations", "--no-snapshot"}:
        with trace_stage("review.options.parse"):
            global_args = _global_parser().parse_args(argv)
        return review_handle(global_args) or 0

    with trace_stage("action.resolve"):
        action_location = _master_action_index(argv)
    if action_location is None:
        with trace_stage("definition.parse"):
            parsed = parse_command("cal " + shlex.join(argv))
        _print_json(parsed.to_dict())
        return 0
    action_index, action = action_location
    command_argv = [
        *argv[:action_index],
        action[2:],
        "--",
        *argv[action_index + 1:],
    ]
    with trace_stage("action.arguments.parse"):
        parsed_args = _master_parser().parse_args(command_argv)
    action = f"--{parsed_args.command}"

    if action == "--list-calendars":
        with trace_stage("query.list-calendars"):
            return _list_calendars(parsed_args.load)

    tokens = parsed_args.tokens[1:]

    reference = _parse_cli_date(
        parsed_args.reference_date,
        date.today(),
    )

    state_path = parsed_args.load

    # Convenience default for whole-calendar reports.
    if (
        action == "--generate-prompt"
        and not state_path
        and Path("data/calendar.json").is_file()
    ):
        state_path = "data/calendar.json"

    with trace_stage("state.load"):
        state = CalendarState.load(state_path) if state_path else CalendarState()

    if action == "--reset":
        if tokens:
            raise CLIError("--reset does not accept extra arguments.")

        target = parsed_args.save or parsed_args.load
        if not target:
            target = "data/calendar.json" if Path("data/calendar.json").is_file() else "calendar.json"

        # Reload target so reset reflects and logs what is actually cleared.
        reset_state = CalendarState.load(target) if Path(target).is_file() else state
        old_activity_ids = [rule.id for rule in reset_state.rules]

        # Create a fresh, empty calendar.
        empty_state = CalendarState()
        empty_state.save(target)

        log_change(
            "RESET",
            activities=old_activity_ids,
            details=f"Removed {len(old_activity_ids)} activities.",
            log_path=Path(target).with_name("cal_changes.jsonl"),
        )

        _print_json({
            "status": "RESET",
            "removed_activities": old_activity_ids,
            "count": len(old_activity_ids),
            "saved_to": target,
        })
        return 0

    if action in {
        "--add",
        "--remove",
        "--change",
        "--move",
        "--cancel",
        "--exception",
    }:
        with trace_stage(f"mutation.{action[2:]}"):
            return _run_mutation(
                action,
                tokens,
                state,
                reference,
                parsed_args.save,
            )

    if parsed_args.save:
        raise CLIError(
            "--save is only valid with a mutating action."
        )

    with trace_stage(f"query.{action[2:]}"):
        return _run_query(action, tokens, state, reference)


def _execute(argv: list[str]) -> int:
    with trace_stage("cli.dispatch"):
        return _execute_command(argv)

#TODO: scale down
""" ex
def _execute(args, state, config):
    if args.action in QUERY_ACTIONS:
        return _run_query(args, state, config)

    if args.action in MUTATION_ACTIONS:
        return _run_mutation(args, state, config)

    if args.action == "today":
        return _run_today(args, state)

    if args.action == "week":
        return _run_week(args, state)

    if args.action == "batch":
        return _run_batch(args, state)
"""