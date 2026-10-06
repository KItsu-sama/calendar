# ROADMAP.md — `cal` Scheduling Engine

## Phase 1: Command Grammar & Data Model — ✅ DONE
- [x] Define `ActivityRule` / `CalendarException` dataclasses (mirror the
      provided JSON Schema exactly: flexibility, priority, recurrence,
      time_constraints, move_options).
- [x] JSON Schema validation for parsed objects.
- [x] Command parser for: `--fixed`, `--todo`, `--optional`,
      `--temp-fixed`, `--exception`, with modifiers `--daily`, `--wed`
      (day flags), `--time`, `--duration`, `--min`, `--max`, `--except`,
      `--changeable`, `--move-to`, `--due`, `--date`, `--until`, `--cancel`,
      `--move`, `--priority`, `--between`, `--days`.
- [x] No LLM anywhere in this phase.
- **Verify:** every example command in the spec parses into a valid
  structured object matching the JSON Schema; malformed commands raise a
  clear parse error. — **18/18 tests passing** (`tests/test_parser.py`).

## Phase 2: Schedule Resolver (build actual schedule + free time) — ✅ DONE
- [x] Implement `ScheduleResolver.build_actual_schedule` (recurrence match,
      valid_from/valid_until windows, exceptions CANCEL/MOVE/MODIFY, temp-fixed
      expiry) — ported from the reference code and wired to Phase 1's typed
      `ActivityRule`/`CalendarException` models instead of raw dicts.
- [x] Implement `calculate_free_time` via interval-gap arithmetic; confirmed
      free time is never persisted, only derived (see
      `test_free_time_never_stored_only_derived`).
- **Verify:** given the Monday example (School/Lunch/Math), free time output
  matches the spec's stated gaps (11:30-13:00, 14:00-15:00, 16:00-24:00) —
  **plus** the 00:00-07:00 pre-school gap, which the spec's prose omitted
  but a complete engine must still report (documented decision, see
  STATE.md). **27/27 tests passing.**

## Phase 3: Conflict Detection & Local Solver — ✅ DONE
- [x] `find_overlapping_intervals(schedule, new_event)`.
- [x] `resolve_conflict_locally`: FIXED conflict → `REQUIRES_AI`;
      CHANGEABLE with `move_options.preferred_days` + room → `MOVE`;
      FLEXIBLE with same-day room → `SHIFT`; otherwise → `REQUIRES_AI`.
- **Verify:** the "extra-class 16:00-17:00 fits in free slot" case →
  `DIRECT_ADD`, no other changes. The "extra-class 15:00-17:00" case → Math
  moves to Saturday, Exercise untouched, matches the spec's expected
  preview output. **5/5 tests passing** (`tests/test_conflict.py`).

## Phase 4: AI Escalation Pipeline — ✅ DONE
- [x] Prompt template builder producing the full context block (schedule
      matrix, task pool, new activity, conflict analysis, rules/hierarchy,
      required JSON output format) — per the template in the spec.
- [x] LLM call wrapper (swappable client) + strict JSON response parser/
      validator against the required output schema.
- [x] Reject/repair malformed LLM responses (one retry-with-error, then a
      clear `LLMResponseError`) rather than silently trusting them.
- **Verify:** the "New Fixed Activity 15:00-18:00 vs Math/Coding/Exercise/
  Project" complex case produces a well-formed prompt and a validated
  parsed response. **9/9 tests passing** (`tests/test_ai_escalation.py`).

## Phase 5: Transaction Logging / Dataset Capture — ✅ DONE
- [x] Transaction record schema (transaction_id, timestamp, input state
      digest, new change request, conflict detection, local solver result,
      LLM interaction, user feedback, final accepted schedule digest).
- [x] Canonical SHA-256 hashing of schedule state (before/after).
- [x] Accept / modify / reject flow that records `user_feedback` and diffs.
- **Verify:** the exam-review scenario runs end to end with `MockLLMClient`,
      writes a schema-valid pending transaction, and records accepted, modified,
      or rejected feedback plus final digests. **6/6 tests passing**
      (`tests/test_transactions.py`).

## Phase 6: Direct Manipulation Commands — ✅ DONE
- [x] `cal --add / --remove / --change / --move / --cancel / --exception`
- [x] `cal --schedule / --free / --conflicts / --preview / --generate-prompt`
- **Verify:** `cal --move math --to sat` and `cal --change school --time
      07:30-12:00` trigger full schedule/free-time recalculation. All six
      mutations and all five inspection commands have CLI coverage.
      **11/11 Phase 6 tests passing** (`tests/test_operations.py`,
      `tests/test_cli.py`).

## Phase 7: End-to-End Testing
- [ ] Reproduce all worked examples from the spec as automated tests
      (sections 5–9, 11–12 of the spec doc).
- [ ] Fuzz test: random FIXED insertions against a populated week, confirm
      FIXED/CRITICAL items are never altered.
- **Verify:** full test suite green; manual walkthrough of one AI-escalation
  case end to end (command → prompt → mock LLM response → transaction log).
