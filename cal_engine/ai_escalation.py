"""
Phase 4 — AI Escalation Pipeline.

Used only when Phase 3's local solver returns REQUIRES_AI. The LLM is
always given the full scheduling context (never a minimal "move Math"
prompt), and its response is always validated against a strict JSON
schema before it's trusted -- a malformed response gets one repair
attempt, then a clear error, never a silent guess.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import List, Optional, Protocol

from jsonschema import validate as _jsonschema_validate
from jsonschema.exceptions import ValidationError as _SchemaValidationError

from .conflict import LocalSolveResult
from .models import ActivityRule
from .resolver import Interval, ScheduleResolver

# ---------------------------------------------------------------------------
# 1. Prompt construction
# ---------------------------------------------------------------------------

_RULES_AND_HIERARCHY = """\
[SCHEDULING RULES & HIERARCHY]
1. FIXED/CRITICAL activities MUST NOT be moved or removed under any circumstances.
2. CHANGEABLE activities may be moved ONLY to their explicit allowed destinations.
3. FLEXIBLE activities can be shifted into any contiguous FREE time block that satisfies their minimum duration.
4. OPTIONAL activities should be compressed or deleted if space is constrained.
5. Absolute respect for task deadlines."""

_OUTPUT_FORMAT = """\
[REQUIRED OUTPUT FORMAT]
Return a JSON structure matching this exact layout:

{
  "proposed_schedule_changes": [
    {
      "activity_id": "string",
      "action": "MOVE | SHIFT | REMOVE | RESCHEDULE",
      "new_start": "YYYY-MM-DD HH:MM or null",
      "new_end": "YYYY-MM-DD HH:MM or null",
      "reasoning": "Short explicit justification"
    }
  ],
  "unscheduled_items": ["string"],
  "conflict_summary": "Summary of resolution strategy",
  "trade_off_explanation": "Clear text explaining trade-offs made to the user"
}"""


def _format_schedule_matrix(resolver: ScheduleResolver, start_date: date, end_date: date) -> str:
    lines = []
    current = start_date
    while current <= end_date:
        lines.append(f"{current.strftime('%A').upper()} ({current.isoformat()})")
        day_schedule = resolver.build_actual_schedule(current, current)
        day_start = datetime.combine(current, datetime.min.time())
        day_end = day_start + timedelta(days=1)
        free = ScheduleResolver.calculate_free_time(day_schedule, day_start, day_end)

        entries: List[Interval] = sorted(day_schedule + free, key=lambda i: i.start)
        for entry in entries:
            start_s, end_s = entry.start.strftime("%H:%M"), entry.end.strftime("%H:%M")
            if entry.activity_id == "FREE":
                lines.append(f"- {start_s} - {end_s} | FREE TIME")
            else:
                flex = entry.flexibility.value if entry.flexibility else "?"
                prio = entry.priority or "?"
                lines.append(f"- {start_s} - {end_s} | {entry.name} [{flex} | Priority: {prio}]")
        lines.append("")
        current += timedelta(days=1)
    return "\n".join(lines).rstrip()


def _format_task_pool(task_pool: List[ActivityRule]) -> str:
    if not task_pool:
        return "(none)"
    lines = []
    for idx, task in enumerate(task_pool, start=1):
        tc = task.time_constraints
        duration_bits = []
        if tc.min_duration_minutes is not None:
            duration_bits.append(f"min {tc.min_duration_minutes}m")
        if tc.max_duration_minutes is not None:
            duration_bits.append(f"max {tc.max_duration_minutes}m")
        lines.append(f"{idx}. {task.name}")
        lines.append(f"   - ID: {task.id}")
        if duration_bits:
            lines.append(f"   - Duration: {', '.join(duration_bits)}")
        lines.append(f"   - Flexibility: {task.flexibility.value}")
        if tc.deadline:
            lines.append(f"   - Deadline: {tc.deadline}")
        lines.append(f"   - Priority: {task.priority.value}")
        if tc.allowed_windows:
            windows = ", ".join(f"{w.start_time}-{w.end_time}" for w in tc.allowed_windows)
            lines.append(f"   - Allowed Windows: {windows}")
        lines.append("")
    return "\n".join(lines).rstrip()


def _format_conflict_analysis(conflicts: List[Interval]) -> str:
    if not conflicts:
        return "(none)"
    lines = ["Direct overlap detected with:"]
    for c in conflicts:
        flex = c.flexibility.value if c.flexibility else "?"
        lines.append(f"- {c.name} ({c.start.strftime('%H:%M')} - {c.end.strftime('%H:%M')}) [{flex}]")
    return "\n".join(lines)


def build_conflict_prompt(
    resolver: ScheduleResolver,
    new_rule: ActivityRule,
    on_date: date,
    conflicts: List[Interval],
    task_pool: Optional[List[ActivityRule]] = None,
    window_days: int = 7,
) -> str:
    """Assemble the full deterministic escalation prompt (never a minimal
    'move X' prompt -- always the whole schedule + task pool + rules)."""
    task_pool = task_pool or []
    window_start = on_date
    window_end = on_date + timedelta(days=window_days - 1)

    new_window = new_rule.time_constraints.fixed_interval
    new_time_str = f"{new_window.start_time} - {new_window.end_time}" if new_window else "(unspecified)"

    parts = [
        "=" * 80,
        "CALENDAR CONSTRAINTS ENGINE - CONFLICT RESOLUTION REQUEST",
        "=" * 80,
        "",
        "[SYSTEM CONTEXT]",
        "The user is attempting to add a new activity that causes a schedule conflict",
        "that cannot be resolved deterministically. You are an expert execution coach",
        "and schedule optimizer.",
        "",
        "[CURRENT SCHEDULE MATRIX - TARGET WINDOW]",
        f"Period: {window_start.isoformat()} to {window_end.isoformat()}",
        "",
        _format_schedule_matrix(resolver, window_start, window_end),
        "",
        "[TASK POOL / UNPLACED TODOs]",
        _format_task_pool(task_pool),
        "",
        "[NEW PROPOSED ACTIVITY]",
        f"- Name: {new_rule.name}",
        f"- Requested Window: {on_date.strftime('%A')} {new_time_str}",
        f"- Flexibility: {new_rule.flexibility.value}",
        f"- Priority: {new_rule.priority.value}",
        "",
        "[CONFLICT ANALYSIS]",
        _format_conflict_analysis(conflicts),
        "",
        _RULES_AND_HIERARCHY,
        "",
        _OUTPUT_FORMAT,
        "=" * 69,
    ]
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# 2. Response schema + strict parsing
# ---------------------------------------------------------------------------

AI_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "proposed_schedule_changes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "activity_id": {"type": "string"},
                    "action": {"type": "string", "enum": ["MOVE", "SHIFT", "REMOVE", "RESCHEDULE"]},
                    "new_start": {"type": ["string", "null"]},
                    "new_end": {"type": ["string", "null"]},
                    "reasoning": {"type": "string"},
                },
                "required": ["activity_id", "action", "reasoning"],
            },
        },
        "unscheduled_items": {"type": "array", "items": {"type": "string"}},
        "conflict_summary": {"type": "string"},
        "trade_off_explanation": {"type": "string"},
    },
    "required": [
        "proposed_schedule_changes",
        "unscheduled_items",
        "conflict_summary",
        "trade_off_explanation",
    ],
}

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


class LLMResponseError(ValueError):
    """Raised when the LLM's response is not valid JSON matching AI_RESPONSE_SCHEMA."""


def _strip_fences(raw: str) -> str:
    return _FENCE_RE.sub("", raw.strip()).strip()


def parse_llm_response(raw_text: str) -> dict:
    cleaned = _strip_fences(raw_text)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise LLMResponseError(f"LLM response was not valid JSON: {e}") from None
    try:
        _jsonschema_validate(instance=data, schema=AI_RESPONSE_SCHEMA)
    except _SchemaValidationError as e:
        raise LLMResponseError(f"LLM response did not match the required schema: {e.message}") from None
    return data


# ---------------------------------------------------------------------------
# 3. LLM client protocol + escalation orchestration
# ---------------------------------------------------------------------------

class LLMClient(Protocol):
    def complete(self, prompt: str) -> str: ...


class MockLLMClient:
    """Test/dev double: returns a fixed canned response regardless of prompt."""

    def __init__(self, canned_response: str):
        self.canned_response = canned_response
        self.prompts_received: List[str] = []

    def complete(self, prompt: str) -> str:
        self.prompts_received.append(prompt)
        return self.canned_response


@dataclass
class AIEscalationResult:
    prompt: str
    raw_response: str
    parsed_response: dict


def escalate_to_ai(
    resolver: ScheduleResolver,
    new_rule: ActivityRule,
    on_date: date,
    local_result: LocalSolveResult,
    llm_client: LLMClient,
    task_pool: Optional[List[ActivityRule]] = None,
    window_days: int = 7,
    max_repair_attempts: int = 1,
) -> AIEscalationResult:
    """Build the full context prompt, call the LLM, and strictly validate
    the response -- with one repair attempt on a malformed reply before
    giving up with a clear error."""
    if local_result.status != "REQUIRES_AI":
        raise ValueError("escalate_to_ai should only be called when the local solver returns REQUIRES_AI.")

    prompt = build_conflict_prompt(
        resolver, new_rule, on_date, local_result.conflicts, task_pool=task_pool, window_days=window_days
    )

    raw = llm_client.complete(prompt)
    attempts = 0
    last_error: Optional[LLMResponseError] = None
    while True:
        try:
            parsed = parse_llm_response(raw)
            return AIEscalationResult(prompt=prompt, raw_response=raw, parsed_response=parsed)
        except LLMResponseError as e:
            last_error = e
            attempts += 1
            if attempts > max_repair_attempts:
                raise LLMResponseError(
                    f"LLM response invalid after {attempts} attempt(s): {e}"
                ) from None
            repair_prompt = (
                prompt
                + "\n\n[REPAIR REQUEST]\nYour previous response was invalid: "
                + str(e)
                + "\nReturn ONLY the corrected JSON object, matching the required schema exactly."
            )
            raw = llm_client.complete(repair_prompt)
