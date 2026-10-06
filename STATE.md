# STATE.md

## Active Phase
Phases 1–6: ✅ complete.
**58/58 tests passing.**
Phase 7: End-to-End Testing — intentionally deferred; the source spec document
referenced by its worked-example checklist is not present in this workspace.

## What Phase 3 Delivered
- `cal_engine/conflict.py` — `find_overlapping_intervals` (strict overlap, touching
  endpoints don't count) and `resolve_conflict_locally(resolver, new_rule,
  on_date)`:
  - No overlap → `DIRECT_ADD`.
  - Any conflicting item is FIXED → `REQUIRES_AI` immediately (no attempt).
  - CHANGEABLE with `move_options.preferred_days` → tries to keep the
    activity's original time-of-day on the target day first, falls back to
    the first fitting free slot there, else `REQUIRES_AI`.
  - FLEXIBLE → shifts into same-day free time **at or after its own
    original start time** (see design decision below), else `REQUIRES_AI`.
  - Anything else (OPTIONAL, CHANGEABLE with no usable destination, etc.) →
    `REQUIRES_AI`. If *any* conflicting item can't be solved locally, the
    *whole* result is `REQUIRES_AI` — never a partial local fix.
- `tests/test_conflict.py` — reproduces the spec's DIRECT_ADD example, the
  easy Math→Saturday conflict example exactly (including that Exercise is
  untouched), a FLEXIBLE same-day shift, a FIXED-vs-FIXED escalation, and a
  complex multi-item conflict (CHANGEABLE + OPTIONAL + FLEXIBLE) that must
  escalate solely because of the OPTIONAL item.

### Design decision (Phase 3)
FLEXIBLE items are only shifted **forward** in the day (to a free slot at
or after their own original start time) — never backward into early
morning/pre-day hours, even though Phase 2 correctly reports those hours as
free. Reaching that far back is a judgment call left to the AI escalation
path, not something the deterministic solver should decide on its own.

## What Phase 4 Delivered
- `cal_engine/ai_escalation.py`:
  - `build_conflict_prompt(...)` — assembles the exact section structure
    from the spec's template ([SYSTEM CONTEXT], [CURRENT SCHEDULE MATRIX -
    TARGET WINDOW], [TASK POOL / UNPLACED TODOs], [NEW PROPOSED ACTIVITY],
    [CONFLICT ANALYSIS], [SCHEDULING RULES & HIERARCHY], [REQUIRED OUTPUT
    FORMAT]) — always the full window's schedule + free time + task pool,
    never a minimal prompt.
  - `AI_RESPONSE_SCHEMA` + `parse_llm_response(raw_text)` — strips markdown
    code fences, JSON-decodes, validates against the schema
    (`proposed_schedule_changes[]`, `unscheduled_items[]`,
    `conflict_summary`, `trade_off_explanation`), raises `LLMResponseError`
    with a clear message on any failure.
  - `LLMClient` protocol + `MockLLMClient` (records prompts, returns a
    canned response) — the real Anthropic-backed client is a drop-in swap,
    not built yet (not needed until this is wired to a live app).
  - `escalate_to_ai(...)` — builds the prompt, calls the client, validates
    the response, and gives it **one repair attempt** (re-prompts with the
    validation error) before raising `LLMResponseError` for good.
- `tests/test_ai_escalation.py` — prompt contains every required section
  and names every conflicting activity + the new one; task pool renders
  with deadlines; valid/fenced/malformed responses parse correctly; full
  escalation succeeds via `MockLLMClient`; one bad-then-good response is
  auto-repaired; and giving up after repair attempts raises a clear error.

## What Phase 5 Delivered
- `cal_engine/transactions.py` — canonical JSON + SHA-256 state digests,
  `TransactionRecord`, schema validation, and append-only JSONL persistence.
  A pending record and its later feedback update share one transaction ID;
  the latest line is the current state.
- `cal_engine/resolution.py` — `ResolutionEngine` ties parsing, local conflict
  detection, escalation, proposal materialization, and feedback together.
  AI proposals are immutable previews until `accept`, `modify`, or `reject`.
- AI/manual changes are revalidated against move destinations, allowed
  windows/days, deadlines, duration, overlap freedom, and the FIXED/CRITICAL
  protection rule before they can affect accepted state.
- `tests/test_transactions.py` covers deterministic commit, canonical digests,
  mock-LLM prompt/response capture, accept, modify, reject, and protected-item
  rejection.

## What Phase 6 Delivered
- `cal_engine/storage.py` — explicit JSON calendar state (`version: 1`) with
  schema-valid round trips. The CLI is stateless by default and uses
  `--load`/`--save`; this avoids hidden global state while allowing callers
  to persist between invocations.
- `cal_engine/operations.py` — add/remove/change/move/cancel/exception and
  preview operations. Every mutation returns a copied candidate state; callers
  only persist it when safe.
- `cal_engine/cli.py` — schedule/free/conflicts/preview/generate-prompt plus
  all six direct mutations. Global options precede the action; query target
  dates use `--on` when an activity's own `--date` grammar is in use.
- The package was normalized from `src/` to `cal_engine/`, matching the
  existing public imports and eliminating the baseline test collection error.

### Design decision (Phase 6)
Direct `--change`, `--remove`, and `--cancel` commands are explicit user edits
and may update a FIXED item. The protection invariant applies to automatic
resolution: local and AI paths cannot alter any FIXED or CRITICAL item.

## Next Actions
Phase 7 remains: obtain the referenced source spec, reproduce sections 5–9 and
11-12 exactly, add deterministic seeded fuzz coverage, then perform the
documented manual mock-LLM walkthrough. Do not infer those missing examples
from the current documentation alone.

## Blockers
None.

## Testing Checklist (carried into every phase)
- [x] All spec example commands parse to schema-valid objects (Phase 1).
- [x] Monday/Wednesday free-time examples reproduce exactly, plus the
      documented 00:00-07:00 extension (Phase 2).
- [x] Easy-conflict example (Math bumped to Saturday, Exercise untouched)
      reproduces exactly (Phase 3).
- [x] Complex-conflict example escalates to AI with full context, not a
      minimal prompt (Phase 4).
- [x] Mock AI transaction captures input/final digests and accept/modify/reject
      feedback (Phase 5).
- [x] All direct manipulation and inspection commands recalculate persisted
      state safely (Phase 6).
- [x] Automated local/AI resolution never moves or removes FIXED/CRITICAL
      items; explicit user edit commands remain intentionally available.
