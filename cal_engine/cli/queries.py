# Read-only commands for the CLI


from datetime import date, datetime, timedelta
from pathlib import Path

from ..conflict.ai_escalation import build_conflict_prompt, build_schedule_review_prompt
from ..core.operations import conflicts_in_schedule, preview_activity
from ..core.parser import PRIMARY_FLAGS, parse_time_range
from ..core.storage import CalendarState
from ..ui.helpper import _display_calendars
from ..ui.terminal import Terminal
from .arguments import _activity_from_tokens, _parse_cli_date, _query_parser
from .errors import CLIError
from .output import _print_json, print_activity


def _run_query(
    action: str,
    tokens: list[str],
    state: CalendarState,
    reference: date,
) -> int:
    has_activity = bool(tokens and tokens[0] in PRIMARY_FLAGS)

    if action == "--check":
        conflicts = conflicts_in_schedule(state, reference, reference + timedelta(days=6))
        _print_json({
            "start_date": reference.isoformat(),
            "end_date": (reference + timedelta(days=6)).isoformat(),
            "conflicts": conflicts,
        })
        return 0

    if action == "--generate-prompt" and not tokens:
        print(
            build_schedule_review_prompt(
                state.resolver(),
                state.rules,
                state.exceptions,
                reference,
            )
        )
        return 0
    
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


def _check_activity(name, state):
    """Display the current configuration of one activity."""
    rule = state.get_rule(name)
    if rule is None:
        raise CLIError(f"Unknown activity '{name}'.")
    print_activity(rule, console=Terminal().console)


def _check_group(name, config):
    """Display a group's configuration and associated activities."""
    groups = config.get("groups", []) if isinstance(config, dict) else []
    group = next((g for g in groups if str(g.get("id", "")) == name), None)
    if group is None:
        group = next((g for g in groups if str(g.get("name", "")) == name), None)
    if group is None:
        raise CLIError(f"Unknown group '{name}'.")

    terminal = Terminal()
    terminal.rule(f"Group: {group.get('name')} [{group.get('id')}]")
    terminal.print(f"  description: {group.get('description', '')}")
    terminal.print(f"  priority: {group.get('priority', 'NORMAL')}")
    terminal.print(f"  flexibility: {group.get('flexibility', 'FIXED')}")
    terminal.print(f"  override_policy: {group.get('override_policy', 'never')}")

    group_id = group.get("id")
    schedules = [
        schedule for schedule in config.get("schedules", [])
        if schedule.get("group_id") == group_id
    ]
    if not schedules:
        terminal.info("No schedules associated with this group.")
        return

    terminal.print(f"Schedules ({len(schedules)}):")
    for schedule in schedules:
        terminal.print(f"  {schedule.get('id')} ({schedule.get('group_id')})")
        terminal.print(f"    days: {', '.join(schedule.get('days', []))}")
        terminal.print(
            f"    time: {schedule.get('start_time', '')} - {schedule.get('end_time', '')}"
        )
        terminal.print(f"    period: {schedule.get('period_minutes', '?')} minutes")

        activities = schedule.get("activities", [])
        if activities:
            terminal.print("    Activities:")
            for activity in activities:
                terminal.print(
                    "      - "
                    f"{activity.get('name', '')} [{activity.get('id', '')}] "
                    f"{activity.get('day', '')} "
                    f"{activity.get('start_time', '')}-{activity.get('end_time', '')}"
                )

        breaks = schedule.get("breaks", [])
        if breaks:
            terminal.print("    Breaks:")
            for br in breaks:
                terminal.print(
                    "      - "
                    f"{br.get('name', 'Break')} [{br.get('id', '')}] "
                    f"{br.get('start_time', '')}-{br.get('end_time', '')} "
                    f"(override_policy={br.get('override_policy', 'overrideable')}, "
                    f"priority={br.get('priority', 'HIGH')})"
                )


def _query_range(args, reference: date) -> tuple[date, date]:
    start_raw = args.start or args.date
    start = _parse_cli_date(start_raw, reference) if start_raw else reference
    end = _parse_cli_date(args.end, reference) if args.end else start
    if end < start:
        raise CLIError("Query end date must not precede its start date.")
    return start, end


def _list_calendars(calendar_path: str | Path | None = None) -> int:
    """List available calendar files and report the active calendar.

    Shares the calendar display with ``cal_engine.ui.helpper`` so the CLI and
    the interactive helper render calendars identically.
    """
    active = Path(calendar_path) if calendar_path else Path.cwd() / "calendar.json"
    _display_calendars(Terminal(), active)
    return 0


def _list_activities(state_path: str | Path | None = None) -> int:
    """List all activity names in the current calendar state.

    Usage: ``cal --activities``

    Prints each activity's name on a separate line, along with its ID.
    """
    from ..core.storage import CalendarState

    state = CalendarState.load(state_path) if state_path else CalendarState()
    if not state.rules:
        terminal = Terminal()
        terminal.info("No activities found.")
        return 0

    terminal = Terminal()
    terminal.print(f"Activities ({len(state.rules)}):")
    for rule in state.rules:
        terminal.print(f"  {rule.name} [{rule.id}]")
    return 0