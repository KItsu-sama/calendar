from __future__ import annotations

# ├── cli/
# │   ├── __init__.py      # Public main() entry point
# |   ├── errors.py        # CLI-specific error handling
# │   ├── arguments.py     # Argument parsers and action detection
# │   ├── dispatch.py      # Command routing
# │   ├── queries.py       # Read-only commands
# │   ├── mutations.py     # Commands that modify calendar state
# │   ├── special.py       # today, week, batch, reset
# │   └── output.py        # Shared output formatting

from datetime import date, datetime, timedelta
import argparse
import copy
import json
import shlex
import sys
from pathlib import Path
from typing import Optional

from ..debuger.context import DiagnosticContext, diagnostic_context
from ..debuger.logger import configure_logging
from ..debuger.trace import trace_stage
from ..conflict.ai_escalation import build_conflict_prompt, build_schedule_review_prompt
from ..scheduling.batch import import_batch_file
from ..scheduling.change_log import log_change
from ..scheduling.duration import parse_duration_minutes
from ..core.models import (
    ActivityRule,
    CalendarException,
    ExceptionAction,
    Flexibility,
    MoveOptions,
    Priority,
    Recurrence,
    RecurrenceType,
)
from ..core.operations import (
    OperationError,
    add_activity,
    change_activity,
    conflicts_in_schedule,
    create_exception,
    first_occurrence,
    preview_activity,
    remove_activity,
)
from ..core.parser import (
    PRIMARY_FLAGS,
    CommandParseError,
    parse_command,
    parse_day_date,
    parse_dm_date,
    parse_time_range,
)
from ..scheduling.review import review_args, review_handle
from ..core.storage import CalendarState, StorageError
from ..scheduling.review import review_args

from .errors import CLIError
from .arguments import (
    _activity_from_tokens,
    _direct_parser,
    _global_parser,
    _master_action_index,
    _master_parser,
    _parse_cli_date,
    _pop_value,
    _print_sample,
    _query_parser,
    _source_date,
    DIRECT_ACTIONS,
    
)
from .output import _print_json, _print_table
from .mutations import (
    _complete,
    _run_mutation,
    _updated_rule,
)
from .queries import (
    _check_activity,
    _check_group,
    _query_range,
    _run_query,
)
from .special import (
    _run_batch,
    _run_today,
    _run_week,
)
from .dispatch import _execute

__all__ = [
    "main",
    "CLIError",
]


def _helper_calendar_path(arguments: list[str]) -> str | None:
    fallback = None
    index = 0
    while index < len(arguments):
        token = arguments[index]
        if token.startswith("--load="):
            return token.partition("=")[2]
        if token == "--load" and index + 1 < len(arguments):
            return arguments[index + 1]
        if token.startswith("--save="):
            fallback = token.partition("=")[2]
        elif token == "--save" and index + 1 < len(arguments):
            fallback = arguments[index + 1]
            index += 1
        index += 1
    return fallback


def main(argv: Optional[list[str]] = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    debug_enabled = "--debug" in arguments
    if debug_enabled:
        arguments = [argument for argument in arguments if argument != "--debug"]

    logger = configure_logging(debug_enabled)
    context = (
        DiagnosticContext(
            command=next(
                (argument for argument in arguments if argument in DIRECT_ACTIONS),
                arguments[0] if arguments else "(no command)",
            ),
            arguments=tuple(
                sys.argv[1:] if argv is None else argv
            ),
        )
        if debug_enabled
        else None
    )

    try:
        if context is None:
            return _execute(arguments)

        with diagnostic_context(context):
            with trace_stage("cli.main"):
                return _execute(arguments)

    except (CLIError, CommandParseError) as error:
        # CLIError: valid shell syntax but invalid cal usage.
        # CommandParseError: malformed `cal` command (e.g. missing required primary flag).
        if debug_enabled:
            logger.error("Command failed: %s", error)
        if sys.stdin.isatty() and sys.stdout.isatty():
            from ..ui.helpper import Help

            return Help(
                calendar_path=_helper_calendar_path(arguments),
                debug_enabled=debug_enabled,
            ).assist(str(error))
        print(f"Error: {error}", file=sys.stderr)
        return 2
    except (OperationError, StorageError, ValueError) as error:
        # OperationError: requested state change cannot be applied safely.
        # StorageError: calendar state cannot be loaded, saved, or validated.
        # ValueError: inappropriate argument value (of correct type).
        if debug_enabled:
            logger.error("Command failed: %s", error)
        print(f"Error: {error}", file=sys.stderr)
        return 2
    finally:
        if context is not None:
            logger.info("DEBUG REPORT %s", json.dumps(context.report(), sort_keys=True))