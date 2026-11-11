# Calendar Engine

A rule-based personal calendar and timetable manager. The project includes a
Python CLI, a desktop GUI, reusable group timetables, and prompt generation for
AI-assisted schedule review. The scheduling engine works locally; it does not
require an AI service.

## 01. Overview

Calendar Engine turns activity definitions and recurrence rules into dated
schedule occurrences. It can inspect schedules, calculate unallocated time,
identify conflicts, and help manage recurring school or personal timetables.
Its design keeps source data explicit and computes schedules from that data
when requested.

Development is in progress. The core rule, recurrence, conflict, CLI, GUI, and
group-timetable features are implemented; some later review/planning
capabilities remain a lightweight development workflow rather than a
production-grade scheduling service.

## 02. Features

- **Calendar management:** create, change, move, cancel, and remove activities.
- **Recurring activities and exceptions:** describe daily or weekly rules and
  override a single occurrence without changing the recurring rule.
- **Conflict detection and resolution:** inspect overlaps; solve supported
  changeable/flexible conflicts locally or generate a prompt when judgment is
  needed.
- **Free-time calculation:** calculate gaps in a selected schedule window.
  These are unallocated intervals, not a guarantee of actual availability.
- **AI-assisted schedule review:** generate context-rich prompts and validate
  proposed structured changes in the library workflow. No live AI provider is
  configured by default.
- **Group timetable generation:** edit reusable group schedules in the GUI and
  export timetable entries to calendar rules while preserving unrelated data.
- **Desktop calendar:** view a week or month and edit calendar events and group
  timetables.
- **Review and change tracking:** inspect changes to source files, generate
  derived preparation/review activities, and record successful CLI mutations.

## 03. Scheduling model

| Concept | Meaning |
| --- | --- |
| Activity | A named scheduled task or subject, represented in the core calendar as a rule. |
| Rule | Defines an activity's recurrence, time constraints, priority, and flexibility. |
| Occurrence | One dated instance produced when a rule applies on a particular date. |
| Exception | Cancels, moves, or changes the time of one occurrence of a rule. |
| Group | Reusable timetable metadata and defaults; group membership is maintained by the group editor, not by the core activity-rule schema. |
| Profile | Optional protected-time and availability constraints used by the review/planning workflow. |
| Relation | An optional preparation or review rule that derives work from matching schedule occurrences. |

The core schedule is derived rather than stored as a separate daily or weekly
calendar:

```text
Activity definitions and recurring rules
                  |
                  v
Scheduling engine: dates + recurrence + exceptions
                  |
                  v
Derived daily/weekly occurrences
            /            \
           v              v
      CLI and GUI     AI review prompts
```

Free time is computed from the gaps between occurrences and is not saved as
calendar data. The separate review workflow can also use a profile to subtract
protected time and buffers before treating gaps as usable planning blocks.

## 04. Installation and setup

### Prerequisites

- Python 3.10 or later.
- Tkinter for the desktop GUI (normally included with standard Windows Python
  distributions; some Linux distributions package it separately).
- `jsonschema` for the engine's model validation.
- `pytest` to run the test suite.

There is no dependency manifest in the repository at present. Install the
runtime and test dependencies with:

```powershell
python -m pip install jsonschema pytest
```

### Clone and install

```powershell
git clone https://github.com/KItsu-sama/calendar.git
cd calendar
python -m pip install jsonschema
```

Add `pytest` if you plan to run tests. You can also use an existing checkout
without cloning.

### Configure the calendar

The CLI loads calendar state only when you pass `--load FILE`. To start with
the repository's sample calendar, use `calendar.json`; otherwise, create a
new state by using a mutation with `--save FILE`. The GUI uses
`calendar.json` and `groups_schedules.json` in the working directory by
default. Back up these files before experimenting with real schedule data.

### Launch

```powershell
python main.py --help
python main.py gui
```

On Windows, run `run_calendar.cmd` for the launcher menu. The unified GUI can
also be opened with `python main.py group-schedule`.

## 05. Quick start

Global options such as `--load` and `--save` go **before** the action.
Mutations are persisted only when `--save` is supplied.

Create a calendar with a recurring school block and lunch:

```powershell
python main.py --save calendar.json --add --fixed school --daily --time 07:00-11:30 --except sunday
python main.py --load calendar.json --save calendar.json --add --fixed lunch --daily --time 13:00-14:00
```

Display today (the reference date defaults to today):

```powershell
python main.py --load calendar.json --schedule
```

Display a Monday-to-Sunday week by providing its dates:

```powershell
python main.py --load calendar.json --schedule --from 2026-11-09 --to 2026-11-15
```

Add another recurring activity:

```powershell
python main.py --load calendar.json --save calendar.json --add --todo homework --days mon wed fri --min 30m --max 90m --due fri 21:00
```

Import activities from a batch schedule text file:

```powershell
python main.py --load calendar.json --save calendar.json --batch schedule.txt
```

Open the GUI and add the optional School timetable template from the
**Groups & slots** area. The template is opt-in and does not populate a new
configuration automatically.

## 06. Usage guide

### GUI

Launch with `python main.py gui` or `run_calendar.cmd`. The unified editor
provides week/month views, navigation to today, event editing, group and
timetable editing, and undo for recent in-session changes. Use **Files** to
open a calendar or group-rules file, reload from disk, or undo. Calendar events
and group timetable definitions are saved to separate files.

### CLI

Use `python main.py --help` for the current command-line options. The CLI
accepts calendar operations and schedule queries, with JSON output for most
queries and mutation results.

Common patterns:

```powershell
# See scheduled occurrences for one date or a date range.
python main.py --load calendar.json --schedule --date 2026-11-11
python main.py --load calendar.json --schedule --from 2026-11-09 --to 2026-11-15

# Calculate unallocated intervals and report overlapping activities.
python main.py --load calendar.json --free --from 2026-11-09 --to 2026-11-15
python main.py --load calendar.json --conflicts --from 2026-11-09 --to 2026-11-15

# Move one occurrence, or modify its time for one date.
python main.py --load calendar.json --save calendar.json --move math --to sat --date 2026-11-11
python main.py --load calendar.json --save calendar.json --exception school --date 2026-11-09 --time 07:30-08:00

# Preview a new activity before adding it.
python main.py --load calendar.json --preview --fixed exam --wed --time 15:00-16:00
```

`--reference-date` anchors weekday names and omitted dates. Query ranges are
inclusive. `--free` includes the full final date, from midnight through the
next midnight. Bare `today` and `week` commands show the empty default state;
use `--schedule` with `--load` to query a saved calendar.

### Batch schedule importing

`--batch FILE` parses the repository's batch schedule text format and adds all
parsed rules to the loaded state. Existing rules are retained. The import
fails if an imported activity ID already exists; it does not silently replace
that rule. Supply both `--load` and `--save` to extend a saved calendar.

### Group timetable management

Open the unified GUI with `python main.py group-schedule`, select
**Groups & slots**, and add the School starter template or a blank group.
Choose active days, the timetable's start/end times, and slot length; assign
subjects to generated slots and save. Use `--export-config` to export an
already saved group configuration without opening the GUI:

```powershell
python main.py group-schedule --export-config groups_schedules.json --calendar calendar.json
```

## 07. Configuration and data integrity

### Authoritative files and derived data

- **`calendar.json`** is the version-1 core calendar state: activity rules and
  occurrence exceptions. It is the source of truth for CLI schedule
  calculations.
- **`groups_schedules.json`** stores group defaults, active days, timetable
  settings, breaks, and slot assignments. It is separate from the core
  calendar schema.
- **`profile.json`** optionally defines protected periods, buffers, preferred
  windows, and planning limits for the review workflow.
- **`relations.json`** optionally defines rules that derive preparation or
  review activities from matching occurrences.
- **`*.snapshot.json`** is a comparison baseline written by `--review`; it is
  not calendar state.
- **`cal_changes.jsonl`** is an append-only log of successful CLI mutations;
  it is useful for audit/history but is not used to reconstruct state.
- **Daily/weekly schedules, occurrences, conflicts, free-time gaps, and review
  plans** are derived when requested and are not written into `calendar.json`.

The standard CLI is stateless between invocations: load the authoritative
calendar with `--load`, and persist mutations with `--save`. The GUI saves its
calendar and group configuration as edits are made.

### `calendar.json`

The file is a JSON object with `version: 1`, a `rules` array, and an
`exceptions` array. Rules hold recurrence and time constraints. Exceptions
refer to a rule ID and target date and can cancel, move, or modify that
occurrence. State loading validates the data and rejects duplicate rule IDs
or exceptions that refer to unknown rules.

### `profile.json` and `relations.json`

These are optional inputs to the v0.2 review workflow, not required to use the
core CLI or GUI. A profile describes protected times and buffers; relations
describe derived work such as preparation before, or review after, matching
events. Pass their paths explicitly:

```powershell
python main.py --load calendar.json --profile profile.json --relations relations.json --review
```

Without a profile, availability is unknown: the review workflow may list
unallocated time, but it does not present those gaps as usable planning slots.
The core `--free` command only reports gaps in the scheduled activities and
does not consult the profile.

### Group timetable configuration

A group is a reusable set of defaults and metadata. A schedule selects its
group, active days (`MON` through `SUN`), start and end times, slot duration,
optional breaks, and subject assignments. Each activity is assigned to a
day and period slot; an activity's priority can override the group default.
Groups are not first-class membership fields on core activity rules.

For example, the built-in **School** template defines a School group and a
weekday schedule from 06:30 to 11:15, with 45-minute periods and a break from
08:30 to 09:00. Add the template in the GUI, assign subjects to its generated
slots, and save. A 45-minute period is shortened if it reaches the timetable
end or the next break; after the break, the next period starts.

Export translates assigned slots and breaks into weekly core calendar rules.
The exporter replaces only rules whose IDs use the reserved
`group_schedule__` prefix and removes exceptions attached to those replaced
generated rules. Unrelated calendar rules and their exceptions are preserved.
The generated rules do not retain a core group ID. The editor's
`override_policy` is metadata and is not enforced as a scheduling permission
by the core conflict solver.

### Snapshots, backup, and recovery

`--review` compares current rules, relations, exceptions, and profile data
against a snapshot next to the calendar file (for example,
`calendar.snapshot.json`). By default it updates the snapshot after the
review. `--no-snapshot` prevents that write; an existing snapshot is still
read for comparison.

Back up the calendar, group configuration, profile and relations files, and
optionally the snapshot and change log. To restore, put the backed-up files
back in their original locations and use `--load` to select the restored
calendar. If the review snapshot was not backed up, the next review starts
without the previous comparison baseline.

Imports and exports are additive/prefix-scoped rather than full replacements:
batch import adds rules and rejects ID collisions; group export replaces its
own generated rules and keeps unrelated rules. Review the target calendar or
make a backup before exporting.

## 08. Architecture

### High-level architecture

```text
CLI / GUI
   |                     group configuration
   v                            |
CalendarState <--- export <--- Group timetable editor
   |
   v
ScheduleResolver -> occurrences -> free time and conflicts
   |
   +-> prompt generation / validated proposal workflow

calendar + profile + relations -> review, derivation, and planning report
```

### Core modules

- `models.py`: activity rules, recurrence, constraints, exceptions, and JSON
  schema validation.
- `parser.py`: command grammar and batch schedule parsing.
- `storage.py`: validated version-1 calendar state and JSON persistence.
- `resolver.py`: recurrence expansion, exception application, and free-time
  calculation.
- `conflict.py` and `operations.py`: overlap detection, deterministic
  conflict handling, previews, and calendar mutations.
- `group_schedule.py`, `group_schedule_gui.py`, and `unified_gui.py`: group
  configuration, export, and the desktop editor.
- `review.py`, `availability.py`, `derive.py`, and `planner.py`: sidecar
  review, availability, derived activities, and candidate planning.
- `ai_escalation.py`, `resolution.py`, and `transactions.py`: prompt
  construction, response validation, proposal workflows, and transaction
  records.
- `cli.py` and `main.py`: command-line interface and application entry point.

### Scheduling, recurrence, and exceptions

The resolver expands daily and weekly rules over an inclusive date range,
observes validity dates and excluded days, and applies date-specific
exceptions. A cancellation suppresses one matching occurrence; a move
replaces that source occurrence with a dated occurrence at its destination;
a modification changes its time for that date. The recurring rule remains
unchanged. Removing a rule also removes its associated exceptions.

Free time is calculated as the gaps in the resolved schedule. It does not
mean the person is free to use that time: sleep, commuting, meals, and other
unrecorded commitments are unknown unless represented in a separate profile.

### Conflict detection and change tracking

Conflicts are strict interval overlaps; activities that only touch at an end
point are not conflicts. The local resolver can add without changes, move a
CHANGEABLE activity to an allowed weekday, or shift a FLEXIBLE activity
forward into a suitable same-day gap. Conflicts involving FIXED or CRITICAL
activities, and cases the local solver cannot safely resolve, require
judgment rather than an automatic partial fix.

Successful CLI mutations append entries to `cal_changes.jsonl`. The review
workflow uses a separate snapshot file to summarize source-data differences.

### GUI and CLI

Both interfaces work with the same version-1 calendar format. The CLI uses
explicit `--load`/`--save` options for its calendar operations. The unified
GUI saves the calendar and group timetable configuration separately and
exports generated group rules into the calendar.

## 09. AI integration

AI is optional. The core engine does not make network calls. Prompt generation
and the library's injectable AI-client workflow are separate from a live AI
integration; the command line prints prompts for the user to send to an
assistant.

### Prompt generation and review workflow

For a full review, generate a prompt from the loaded calendar:

```powershell
python main.py --load calendar.json --generate-prompt
```

For a specific proposed activity, preview it first. If it requires AI
resolution, generate a conflict prompt:

```powershell
python main.py --load calendar.json --preview --fixed exam --wed --time 10:00-11:00
python main.py --load calendar.json --generate-prompt --fixed exam --wed --time 10:00-11:00
```

The intended workflow is:

1. Calendar Engine builds a review or conflict-resolution prompt with schedule
   context and scheduling rules.
2. The user sends that prompt to an AI assistant.
3. The assistant analyzes the schedule and returns proposed changes in the
   requested structure.
4. The user inspects the rationale, affected activities, and trade-offs.
5. The user decides which changes to apply and makes the approved edits.

The conflict prompt includes the target schedule window, task pool, conflicts,
and required JSON response shape. The schedule-review prompt summarizes
changes, derived work, usable blocks when a profile is available, and planning
results.

### Limits, approval, and data integrity

AI output is a proposal, not authoritative calendar state. The CLI does not
send prompts to a provider or automatically apply an assistant's response.
The library workflow validates response structure and rechecks proposed
changes against scheduling constraints before accepted changes can affect
state. FIXED/CRITICAL protections apply to automatic conflict resolution;
explicit user-edited calendar operations remain available.

Do not send private calendar data to an AI service unless you are comfortable
with that service's data handling. Review any suggested changes yourself and
save only the changes you intend to keep.

## 10. Development

### Project structure

```text
main.py                 CLI entry point
cal_engine/             scheduling models, resolver, CLI, GUI, review workflow
tests/                  automated test suite
calendar.json           local sample calendar (ignored by Git)
schedule.txt            batch schedule example
ROADMAP.md              development phases and priorities
STATE.md                implementation notes and current status
```

### Development environment and tests

Install the runtime and test dependencies, then run tests from the repository
root:

```powershell
python -m pip install jsonschema pytest
python -m pytest
```

Run a focused test module while working on a specific subsystem:

```powershell
python -m pytest tests/test_cli.py
```

### Debugging and contributing

Use `python main.py --help` to inspect CLI options. Calendar and group
configuration load errors are reported by the application; validate changes
with the relevant tests before saving them over important data. Keep changes
focused, preserve the versioned JSON formats, and add tests for behavior
changes. No separate contribution guide is currently included.

## 11. Roadmap and known limitations

### Current status

The core development phases 1-6 are implemented. The roadmap marks Phase 7
end-to-end examples and seeded fuzz coverage as outstanding; `STATE.md`
records the current test status and implementation decisions. See
[`ROADMAP.md`](./ROADMAP.md) and [`STATE.md`](./STATE.md) for the maintained
project notes.

### Development priorities

- Complete end-to-end coverage for the documented scheduling workflows.
- Add seeded fuzz coverage for fixed and critical activity protection.
- Continue developing the review/planning workflow and its configuration
  examples.
- Improve integration and polish across the GUI, CLI, and review features.

### Known limitations

- The project is under active development; the roadmap's end-to-end phase is
  not complete.
- No live AI provider is configured, and the CLI does not apply AI output.
- Groups are separate timetable configuration, not a first-class field in
  core activity rules; `override_policy` is not enforced by the core engine.
- `--free` reports schedule gaps only. Profile-aware usable time is part of
  the separate review workflow.
- The standard CLI does not load a calendar unless `--load` is supplied, and
  mutation commands do not persist unless `--save` is supplied.
- The GUI and review workflow are local desktop/development tools, not a
  multi-user calendar service.

## 12. License and credits

No license file is currently included, so the repository's licensing terms
have not been specified. Check with the maintainers before redistributing or
reusing the project.

The application uses Python's standard library, Tkinter for the desktop
interface, `jsonschema` for data validation, and `pytest` for tests.
Acknowledgements: contributors and maintainers of the project and its
dependencies.
