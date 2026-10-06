# PROJECT.md — `cal` Deterministic Scheduling Engine

## Vision
A command-driven calendar/scheduling engine that resolves conflicts
deterministically whenever possible, and only escalates to an LLM (GPT) when
a conflict genuinely has multiple valid resolutions. The system also logs
every AI-assisted resolution as a structured transaction, building a future
training dataset for a dedicated scheduling model.

No LLM is required for the core engine. LLM involvement is opt-in and only
triggered on "complex conflict."

## Tech Stack
- Python (stdlib `dataclasses`, `datetime`) for the resolver/engine.
- JSON Schema for validating `ActivityRule` / `CalendarException` objects.
- Pluggable LLM call (GPT) only for the escalation path — swappable/mockable.
- CLI-style command grammar (`cal --fixed ... --daily --time ...`) as the
  primary input surface; a parser converts commands into structured objects.
  No AI in the parser.

## Architecture (Pipeline)
```
COMMAND → PARSER → STRUCTURED ACTIVITY → SCHEDULE DATABASE
  → BUILD ACTUAL SCHEDULE → CALCULATE FREE TIME → CHECK CONSTRAINTS
  → DETECT CONFLICTS
      NO CONFLICT → ADD
      CONFLICT → EASY SOLUTION?
          YES → PREVIEW (local solver)
          NO  → GENERATE AI PROMPT → GPT → PROPOSED SCHEDULE
```

Underlying model:
```
BASE SCHEDULE + RECURRING RULES + TEMPORARY RULES + EXCEPTIONS = ACTUAL SCHEDULE
```

## Core Data Model
- **ActivityRule**: id, name, `flexibility` (FIXED / CHANGEABLE / FLEXIBLE /
  OPTIONAL), `priority` (CRITICAL / HIGH / NORMAL / LOW), `recurrence`
  (NONE/DAILY/WEEKLY + except_days/days_of_week + valid_from/valid_until),
  `time_constraints` (fixed_interval, allowed_windows, allowed_days,
  min/max_duration_minutes, deadline), `move_options` (preferred_days).
- **CalendarException**: id, rule_id, target_date, action
  (CANCEL/MOVE/MODIFY), new_date, new_time.
- **Interval**: start, end, activity_id, flexibility (runtime/computed).
- Free time is never stored — it's always computed from the actual schedule
  via interval arithmetic (gap-finding between sorted intervals).

## Core Rules (Non-Negotiable, enforced everywhere — parser, solver, and AI prompt)
1. FIXED/CRITICAL activities must never move or be removed.
2. CHANGEABLE activities may move only to their explicit allowed
   destination(s) (`move_options.preferred_days`).
3. FLEXIBLE activities may shift within any same-day free block that fits
   their required duration.
4. OPTIONAL activities are disposable — compress/remove first when space is
   constrained.
5. Deadlines (on TODO-style tasks) are always respected.
6. "Fixed" only describes that one activity — the rest of the day's items
   keep their own independent flexibility. The engine must never treat a
   whole day as immovable because one item in it is FIXED.

## Local Solver Escalation Rule
- Any conflict touching a FIXED item → immediately `REQUIRES_AI` (no local
  attempt).
- CHANGEABLE conflict with a defined preferred destination that has room →
  solved locally (MOVE).
- FLEXIBLE conflict with same-day free space of sufficient size → solved
  locally (SHIFT).
- Anything needing removal, multi-hop reordering, or lacking room →
  `REQUIRES_AI`.

## AI Escalation Contract
- The LLM is never given a minimal prompt ("move Math"). It always receives:
  full schedule matrix for the target window, the task pool (TODOs with
  min/max/deadline/priority), the new change, conflict analysis, and the
  scheduling rules/hierarchy above.
- Required LLM output is strict JSON: `proposed_schedule_changes[]`
  (activity_id, action, new_start, new_end, reasoning),
  `unscheduled_items[]`, `conflict_summary`, `trade_off_explanation`.

## Transaction Logging (dataset capture)
Every AI-assisted resolution is persisted as one transaction record:
input state (digest of current schedule), the new change request (raw
command + parsed object), conflict detection result, local solver result,
the generated prompt + raw/parsed LLM response, user feedback
(accepted/modified/rejected + manual diffs), and the final accepted
schedule digest. This is the dataset for eventually training a dedicated
scheduling model — not just a call log.

## Out of Scope (for now)
- UI/calendar rendering.
- Multi-user / shared calendars.
- Notification delivery.




python main.py --load state.json --schedule

