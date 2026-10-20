# Calendar Group & Schedule Editor — additive local patch

This patch adds an editable Tkinter Group/Schedule editor and a bridge to the existing version-1 `calendar.json` format. It does not modify GitHub and does not replace your current `cal_engine/gui.py`.

## Files
- `cal_engine/group_schedule.py` — config validation, load/save, timetable-to-calendar export.
- `cal_engine/group_schedule_gui.py` — editable desktop UI.
- `run_group_schedule_editor.cmd` — Windows launcher.
- `examples/groups_schedules.school.json` — example config shell; the editor's built-in default contains all 25 lesson entries.

## Install locally
Copy the two Python files into your existing repository's `cal_engine/` folder. Copy the `.cmd` launcher into the repository root. Do not overwrite existing files.

Run from the repository root:

```powershell
python -m cal_engine.group_schedule_gui
```

or double-click `run_group_schedule_editor.cmd` after copying it to the repository root.

## Workflow
1. Edit Group defaults (priority, flexibility, override policy).
2. Edit Schedule settings (group, days, time range, period duration).
3. Select a schedule and edit individual activities in the table; blank activity priority means inherit from its group.
4. Save the group/schedule config to `groups_schedules.json`.
5. Use **Export to calendar.json…** to merge generated timetable rules into an existing calendar file.

The exporter reserves IDs starting with `school_timetable__`. On later exports, only previously generated rules with that prefix are replaced; unrelated rules are retained. Exceptions tied to generated rules are removed when those generated rules are replaced.

## Important engine limitation
The existing engine has no first-class Group model or `override_policy` field. This patch stores groups and policy separately and translates timetable entries to valid `ActivityRule` objects. The UI warns that `override_policy` is not yet enforced by the scheduler. Break priority is HIGH and the break uses CHANGEABLE flexibility when overrideable, but this is not a complete conflict-resolution authorization system.

## CLI entry point (optional integration)
You can launch the editor with `python -m cal_engine.group_schedule_gui`. This is intentionally separate so the existing CLI parser and current GUI remain untouched until you merge/test the patch locally.

## Add a button to the existing calendar GUI
The archive includes `integration.patch`, a small optional unified diff that adds a **Groups & schedules…** button to the current `cal_engine/gui.py`. Review it before applying. From the repository root, if Git is available locally:

```powershell
git apply --check integration.patch
git apply integration.patch
```

This only changes your local working tree; it does not commit or push anything. If you prefer not to use Git commands, manually add `import subprocess` and `import sys`, add the button after the existing **Load** button, and copy the `open_group_schedule_editor` method from the patch.
