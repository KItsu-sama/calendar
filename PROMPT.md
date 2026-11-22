# Calendar Engine — Meta-Prompt: How to Use Every Command and How They Connect

Use this document as a **reference prompt** when asking an AI assistant (or when writing your own instructions) about the Calendar Engine CLI. It covers every command, every global option, how commands connect, the data flow, and the scheduling model.

---

## 1. Entry Point

```
python main.py [GLOBAL OPTIONS] <ACTION> [ACTION ARGS]
```

- `main.py` is the CLI entry point at the repository root.
- Every invocation goes through `cal_engine/cli/__init__.py: main()` → `_execute()` → `dispatch.py: _execute_command()`.
- Global options are parsed **before** the action and apply to the whole invocation.

## 2. Global Options (apply before the action)

| Option | Meaning |
|--------|---------|
| `--load FILE` | Load a version-1 calendar JSON file before running the action. Default: no calendar (empty state). |
| `--save FILE` | Persist the resulting calendar state after a mutation. Only valid with mutating actions. |
| `--reference-date DATE` | Anchor date (YYYY-MM-DD or D/M) for omitted dates and weekday names. Defaults to today. |
| `--on DATE` | Reference date (YYYY-MM-DD or D/M) for review/week commands. |
| `--debug` | Enable diagnostic execution tracing on stderr. |
| `--profile FILE` | Optional protected-time/availability constraints for the review workflow. |
| `--relations FILE` | Optional derived preparation/review rules. |
| `--review` | Run the v0.2 review workflow (snapshot comparison + derived activities). |
| `--watch` | Re-run review after save (watcher mode). |
| `--no-snapshot` | Prevent writing/updating the review snapshot file. |
| `--export-commands` | Export command definitions. |

**Order matters:** global options come first, then the action, then action-specific arguments.

```
python main.py --load calendar.json --save calendar.json --add --fixed school --daily --time 07:00-11:30 --except sunday
```
## 3. Command Reference

Commands fall into four categories:

### 3.1 State Inspection / Read-Only Queries

These commands read the calendar and print JSON. They do not modify state and do not require `--save`.

| Command | What it does | Typical usage |
|---------|--------------|---------------|
| `--schedule` | Build the actual schedule (occurrences) for a date or date range. | `--schedule --date 2026-11-11` or `--schedule --from 2026-11-09 --to 2026-11-15` |
| `--free` | Calculate unallocated time gaps in a window (midnight-to-midnight, inclusive). | `--free --from 2026-11-09 --to 2026-11-15` |
| `--conflicts` | Report overlapping activities in a window; with an activity token, preview a single activity's conflicts. | `--conflicts --from ... --to ...` or `--conflicts --fixed exam --wed --time 15:00-18:00` |
| `--preview` | Preview a proposed activity before adding it; returns status (DIRECT_ADD / REQUIRES_AI). | `--preview --fixed exam --wed --time 15:00-16:00` |
| `--generate-prompt` | Build an AI review or conflict-resolution prompt (no AI call is made). | `--generate-prompt` (full calendar review) or `--generate-prompt --fixed exam --wed --time 10:00-11:00` (conflict prompt) |
| `--check` | Check for conflicts across the default 7-day window from the reference date. | `--check` |
| `--list-calendars` | List available calendar JSON files and report the active one. | `--list-calendars` |

Query range options (used with `--schedule`, `--free`, `--conflicts`):
- `--date DATE` — single date
- `--from DATE` / `--to DATE` — inclusive range
- `--on DATE` — target/reference date

### 3.2 Direct Mutations (modify calendar state)

These require `--save` to persist. Each takes an activity name/ID plus optional modifiers.

| Command | What it does | Required args | Typical usage |
|---------|--------------|---------------|---------------|
| `--add` | Add a new activity rule to the calendar. | activity definition tokens | `--add --fixed school --daily --time 07:00-11:30 --except sunday` |
| `--remove` | Remove an activity rule by ID. | `name` | `--remove math` |
| `--change` | Change fields of an existing activity rule (time, duration, recurrence, priority, flexibility, etc.). | `name` + at least one field | `--change school --time 07:30-12:00` |
| `--move` | Move one occurrence to a different weekday (creates an exception). | `name`, `--to DAY` | `--move math --to sat --date 2026-11-11` |
| `--cancel` | Cancel one occurrence (creates an exception). | `name`, `--date` | `--cancel school --date 2026-11-09` |
| `--exception` | Override one occurrence: cancel, move, or modify its time for a single date. | `name` + `--cancel` or `--move DAY` or `--time` | `--exception school --date 2026-11-09 --time 07:30-08:00` |

**Activity definition grammar** (used by `--add`, `--preview`, `--generate-prompt <activity>`, and `--exception`/`--change` source):

```
--fixed <NAME> --time HH:MM-HH:MM [--daily | --days DAY ...] [--except DAY ...] [--until DATE]
--todo  <NAME> --days DAY ... [--min N[m|h]] [--max N[m|h]] [--due DAY HH:MM]
```

- `--fixed`: fixed-time recurring activity.
- `--todo`: flexible/duration-based activity with optional min/max duration and a deadline.
- `--daily`: every day. `--days`: specific weekdays. `--except`: exclude weekdays. `--until`: end recurrence.

### 3.3 Special / Batch Commands

| Command | What it does | Typical usage |
|---------|--------------|---------------|
| `today` | Show today's schedule (reference date, defaults to today). | `today` (with `--load`) |
| `week` | Show the week (Mon–Sun) containing the reference date. | `week` (with `--load`) |
| `batch` / `--batch FILE` | Import activities from a batch schedule text file. Requires exactly one file path. | `--batch schedule.txt` (with `--load` and `--save`) |
| `--reset` | Reset the calendar to empty (removes all rules). Writes to `--save` or `--load` target. | `--reset --save calendar.json` |
| `gui` / `--gui` | Launch the desktop GUI (Tkinter). | `gui` |
| `group-schedule` / `--group-schedule` | Open or export the group timetable editor. | `group-schedule` or `--export-config FILE --calendar FILE` |

### 3.4 Review / AI Workflow Commands

| Command | What it does |
|---------|--------------|
| `--generate-prompt` | Builds a context-rich prompt for an AI assistant. Without tokens: full schedule-review prompt from the loaded calendar. With activity tokens: conflict-resolution preview prompt. |
| `--review` | Run the v0.2 review workflow (snapshot diff + derived preparation/review activities + profile constraints). |
| `--preview` | Preview a proposed activity; if status is `REQUIRES_AI`, pair it with `--generate-prompt` to get an AI prompt. |
## 4. How Commands Connect (Data Flow)

```
┌─────────────────────────────────────────────────────────────┐
│  USER WRITES activity definitions (calendar.json)           │
│  (rules + exceptions, version 1 JSON)                       │
└──────────────┬──────────────────────────────────────────────┘
               │ --load
               v
### 3.3 Batch Import System (Detailed)

The `--batch` command imports activities from a text schedule file. This is useful for defining multiple activities at once, such as a weekly timetable or semester schedule.

#### File Format

The batch file is a plain text file with one activity per line. Each line follows this grammar:

```
--fixed <NAME> --time HH:MM-HH:MM [--daily | --days DAY_LIST] [--except DAY_LIST] [--until DATE]
--todo    <NAME> --days DAY_LIST [--min N[m|h]] [--max N[m|h]] [--due DAY HH:MM]
```

Where:
- `<NAME>` — activity identifier (no spaces, alphanumeric + underscores)
- `HH:MM-HH:MM` — time range (24-hour format, e.g., `07:00-11:30`)
- `--daily` — repeat every day (mutually exclusive with `--days`)
- `--days DAY_LIST` — comma-separated weekdays: mon, tue, wed, thu, fri, sat, sun
- `--except DAY_LIST` — exclude these weekdays from the recurrence
- `--until DATE` — stop recurring on this date (YYYY-MM-DD or D/M format)
- `--min N[m|h]` — minimum duration (e.g., `30m`, `1h`)
- `--max N[m|h]` — maximum duration (e.g., `2h`)
- `--due DAY HH:MM` — deadline: activity must finish by this time on the given day

**Example batch file (`schedule.txt`):**

```
--fixed school --time 07:00-11:30 --daily --except sunday
--fixed lunch --time 12:00-13:00 --daily
--todo homework --days mon,wed,fri --min 30m --max 90m --due fri 21:00
--exam review --time 15:00-17:00 --wed --until 2026-12-20
```

#### Usage

```
python main.py --load calendar.json --save calendar.json --batch schedule.txt
```

**Important rules:**
- Duplicate activity IDs are rejected with an error
- Activities are added to the in-memory calendar state
- `--save` must be specified to persist changes to `calendar.json`
- After batch import, re-run `--schedule` to verify the occurrences
- The batch file is additive; it does not remove existing activities

#### Interaction with Other Commands

- **`--batch` + `--schedule`**: Verify imported activities appear in the schedule
- **`--batch` + `--preview`**: Preview individual activities from the batch
- **`--batch` + `--generate-prompt`**: Generate AI prompts for conflict resolution of batch-imported activities
- **`--batch` + `--review`**: Derived activities from batch-imported rules will be included in the review workflow
┌─────────────────────────────────────────────────────────────┐
│  CalendarState (in memory)                                  │
│  ├─ rules[]        activity recurrence + time constraints   │
│  └─ exceptions[]   per-occurrence overrides                 │
└──────────────┬──────────────────────────────────────────────┘
               │ resolver()
               v
┌─────────────────────────────────────────────────────────────┐
│  ScheduleResolver                                           │
│  ├─ build_actual_schedule(start, end)  → dated occurrences  │
│  ├─ calculate_free_time(...)           → unallocated gaps   │
│  └─ conflicts_in_schedule(start, end)  → overlaps           │
└──────────────┬──────────────────────────────────────────────┘
         ┌─────┴─────┬─────┬──────┬────────┬──────────┐
         v           v     v      v        v          v
    --schedule   --free  --conflicts  --check   --preview  --generate-prompt
  (occurrences) (gaps)   (overlaps)   (7-day)   (single   (AI context
                                             activity)   prompt)
```

### The mutation pipeline

```
USER REQUEST  ──►  dispatch.py  ──►  mutations.py  ──►  operations.py
  (--add/--remove/--change/--move/--cancel/--exception)
                                                │
                                                v
                                      CalendarState (candidate copy)
                                                │
                                                v
                                          --save  ──►  calendar.json
                                          log_change ──►  cal_changes.jsonl
```

### The AI escalation pipeline

```
--preview <activity>  or  --generate-prompt <activity>
        │
        ▼
  preview_activity()  ──►  local solver (conflict.py)
        │
        ├─ DIRECT_ADD  →  no AI needed, can --add directly
        │
        └─ REQUIRES_AI ──►  build_conflict_prompt()  ──►  --generate-prompt output
                                              │
                                              ▼
                                    USER sends prompt to AI assistant
                                              │
                                              ▼
                                    AI returns structured JSON changes
                                              │
                                              ▼
                                    USER inspects + applies via --add/--change/--move...
```

### Key interconnection rules

1. **`--schedule` → `--free` → `--conflicts`**: View occurrences first, then gaps, then overlaps. All three use the same `--from`/`--to`/`--date` window and `--load` calendar.

2. **`--preview` → `--generate-prompt`**: Preview a proposed activity. If the local solver can't place it deterministically (`REQUIRES_AI`), use `--generate-prompt` with the same activity tokens to get a full conflict-resolution prompt for an AI assistant.

3. **`--generate-prompt` (no tokens) → full calendar review**: Generates a schedule-review prompt summarizing the whole calendar, derived work, usable blocks (with a profile), and planning results.

4. **Mutations need `--load` + `--save`**: The CLI is stateless by default. Load a calendar, mutate it, save it. Without `--save`, changes exist only in memory for that invocation.

5. **`--add` → `--schedule`/`--free`/`--conflicts`**: After adding activities, re-run inspection commands to see the effect. Mutations do not auto-persist; you must `--save` then `--load` (or just keep the state in the same session).

6. **`today` / `week` → `--schedule`**: `today` and `week` are convenience shortcuts that show the default (empty) state unless you `--load` a calendar. Use `--schedule --load calendar.json` to query a saved calendar.

7. **`batch` → rules**: Import many activities at once from a text file. After `--batch`, re-run `--schedule` to verify. Rejects duplicate IDs.

8. **`group-schedule` → calendar**: The group timetable editor manages `groups_schedules.json` separately. Exporting merges generated rules (with `group_schedule__` prefix) into `calendar.json`, preserving unrelated rules.

9. **`--review` → snapshot + profile + relations**: Compares current state against `calendar.snapshot.json`, applies profile-protected times and buffers, derives preparation/review activities from relations.
## 5. How to Use This Document as an AI Prompt

When asking an AI assistant about the Calendar Engine, you can reference this document directly, or use the following prompt structures:

### Prompt: Full Calendar Review

```
You are examining a Calendar Engine schedule. Use the meta-prompt at PROMPT.md to guide your analysis. 
Give me a full review of the calendar including:
- Current scheduled activities and their recurrence
- Free time gaps in the next 7 days
- Any scheduling conflicts
- Derived preparation/review activities if relations are defined
- Planning suggestions with usable blocks
Format your response as structured JSON with sections: "schedule", "gaps", "conflicts", "derived", "recommendations".
```

### Prompt: Conflict-Resolution for a Specific Activity

```
You are helping resolve a scheduling conflict in Calendar Engine. 
Here is a proposed activity: --fixed exam --wed --time 10:00-11:00

Analyze potential conflicts with existing activities and suggest adjustments. 
Include in your response:
- Conflicting activities and the nature of the conflict
- Possible resolutions (time shift, day change, duration adjustment)
- The recommended change with rationale
Format as JSON: {"status": "REQUIRES_AI", "conflicts": [...], "resolution": {"action": "...", "rationale": "..."}}
```

### Prompt: Review Workflow

```
Run the Calendar Engine v0.2 review workflow. 
Compare the current calendar against its snapshot, apply profile constraints, 
and derive preparation/review activities from any defined relations. 
Output the review results including:
- Snapshot diff (added/removed/modified activities)
- Derived activities with timing
- Protected-time violations
- Suggested actions
Format as structured JSON.
```
### Prompt: Batch Import Assistance

```
You are helping the user import activities into Calendar Engine using the --batch system. 
The user will provide a batch file or describe activities they want to add. Help them:

1. Validate the batch file format against the expected grammar:
   --fixed <NAME> --time HH:MM-HH:MM [--daily | --days DAY_LIST] [--except DAY_LIST] [--until DATE]
   --todo    <NAME> --days DAY_LIST [--min N[m|h]] [--max N[m|h]] [--due DAY HH:MM]

2. Identify any issues in their batch file:
   - Duplicate activity IDs
   - Invalid time formats
   - Conflicting recurrence rules (e.g., both --daily and --days)
   - Invalid weekday names
   - Missing required fields

3. Suggest corrections and improvements:
   - Reformat activities to match the grammar
   - Add missing fields (e.g., --except for exclusions)
   - Resolve conflicts between rules

4. Provide the corrected batch file content
5. Guide them on usage:
   python main.py --load calendar.json --save calendar.json --batch schedule.txt
   After import, verify with: python main.py --load calendar.json --schedule

Output the results as structured JSON with sections:
- "valid_activities": [...]
- "invalid_activities": [{name, errors: [...], suggested_fix: "..."}]
- "corrected_file_content": "..."
- "usage_instructions": "..."
```

### Example Exchange

**User:** Here's my batch file, can you check it?
```
--fixed math --time 08:00-09:30 --daily
--fixed physics --time 15:00-17:00 --mon,fri
--todo chemistry --days tue,thu --min 1h --max 3h --due fri 20:00
--fixed history --time 10:00-11:00 --except sat --until 2026-01-15
```

**AI:** (applies the prompt above, validates, corrects if needed, returns JSON)
## 6. Module Mapping (Code Structure Overview)

- `main.py` — CLI entry point, argument parsing, main loop
- `cal_engine/cli/__init__.py` — `main()` → `_execute()` → dispatch
- `cal_engine/cli/dispatch.py` — command dispatch, global option handling
- `cal_engine/cli/queries.py` --schedule, --free, --conflicts, --check, --list-calendars
- `cal_engine/cli/mutations.py` --add, --remove, --change, --move, --cancel, --exception
- `cal_engine/core/resolver.py` — ScheduleResolver, build_actual_schedule, calculate_free_time, conflicts_in_schedule
- `cal_engine/core/calendar.py` — CalendarState, rules, exceptions, version-1 JSON format
- `cal_engine/core/profile.py` — protected-time/availability constraints
- `cal_engine/core/relations.py` — derived preparation/review rules
- `cal_engine/io/exports.py` --export-commands, --export-config, batch import/export
- `cal_engine/review/__init__.py` --review workflow, snapshot comparison, derived activities
- `cal_engine/review/prompt.py` --generate-prompt, build_conflict_prompt, full review prompt
- `cal_engine/review/snapshot.py` --snapshot read/write, diff computation
- `cal_engine/gui/__init__.py` --gui, Tkinter interface
- `data/calendar.json` — user calendar (version 1 JSON)
- `data/calendar.snapshot.json` — review snapshot
- `data/cal_changes.jsonl` — change log

## 7. Quick Start Checklist

- [ ] Create or load a `calendar.json` with activity rules
- [ ] Use `--schedule` to verify occurrences
- [ ] Use `--free` to see available gaps
- [ ] Use `--conflicts` to check for overlaps
- [ ] Add activities with `--add`, persist with `--save`
- [ ] Use `--preview` before `--add` if unsure
- [ ] Use `--generate-prompt` + AI for complex conflicts
- [ ] Run `--review` to derive preparation/review activities
- [ ] Use `today` and `week` for quick state checks
- [ ] Use `batch` for importing multiple activities
- [ ] Export/import with `group-schedule` for group timetables