# Read-only commands for the CLI


from datetime import date, datetime, timedelta

from ..conflict.ai_escalation import build_conflict_prompt, build_schedule_review_prompt
from ..core.operations import conflicts_in_schedule, preview_activity
from ..core.parser import PRIMARY_FLAGS, parse_time_range
from ..core.storage import CalendarState
from .arguments import _activity_from_tokens, _parse_cli_date, _query_parser
from .errors import CLIError
from .output import _print_json


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
    
    return


def _check_group(name, config):
    """Display a group's configuration and associated activities."""
    ...


def _query_range(args, reference: date) -> tuple[date, date]:
    start_raw = args.start or args.date
    start = _parse_cli_date(start_raw, reference) if start_raw else reference
    end = _parse_cli_date(args.end, reference) if args.end else start
    if end < start:
        raise CLIError("Query end date must not precede its start date.")
    return start, end