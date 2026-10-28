"""Unified desktop editor for calendar events and reusable group timetables."""
from __future__ import annotations

import calendar as month_calendar
import copy
import tkinter as tk
from datetime import date, datetime, timedelta
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any

from .group_schedule import export_to_calendar, load_config, save_config
from .models import ActivityRule, Flexibility, Priority, Recurrence, RecurrenceType, TimeConstraints, TimeWindow
from .storage import CalendarState

DAYS = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]
DAY_INDEX = {day: i for i, day in enumerate(DAYS)}
DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _minutes(value: str) -> int:
    hour, minute = map(int, value.split(":"))
    return hour * 60 + minute


def _clock(value: int) -> str:
    return f"{value // 60:02d}:{value % 60:02d}"


def _school_template() -> dict[str, Any]:
    """Explicit opt-in starter template; never loaded as an implicit fallback."""
    return {
        "version": 1,
        "groups": [{
            "id": "school", "name": "School", "priority": "HIGH",
            "flexibility": "FIXED", "override_policy": "overrideable",
            "description": "Weekday school timetable",
        }],
        "schedules": [{
            "id": "school_week", "name": "School week", "group_id": "school",
            "days": ["MON", "TUE", "WED", "THU", "FRI"],
            "start_time": "06:30", "end_time": "11:15", "period_minutes": 45,
            "breaks": [{
                "id": "recess", "name": "Giải lao", "start_time": "08:30",
                "end_time": "09:00", "priority": "HIGH",
                "override_policy": "never",
            }],
            "activities": [],
        }],
    }


class UnifiedCalendarGUI:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("cal — Calendar")
        root.geometry("1180x780")
        self.calendar_path = Path("calendar.json")
        self.config_path = Path("groups_schedules.json")
        self.state = CalendarState()
        self.config: dict[str, Any] = {"version": 1, "groups": [], "schedules": []}
        self.view_var = tk.StringVar(value="Week")
        self.anchor_var = tk.StringVar(value=date.today().isoformat())
        self.status_var = tk.StringVar(value="Ready")
        self._build()
        self._load_data()

    def _build(self):
        top = ttk.Frame(self.root, padding=8)
        top.pack(fill="x")
        ttk.Label(top, text="Calendar").pack(side="left")
        self.calendar_path_var = tk.StringVar(value=str(self.calendar_path))
        ttk.Entry(top, textvariable=self.calendar_path_var, width=34).pack(side="left", padx=5)
        ttk.Button(top, text="Open…", command=self._browse_calendar).pack(side="left")
        ttk.Button(top, text="Load", command=self._load_data).pack(side="left", padx=3)
        ttk.Button(top, text="Save", command=self._save_calendar).pack(side="left")
        ttk.Label(top, text="Group rules").pack(side="left", padx=(18, 0))
        self.config_path_var = tk.StringVar(value=str(self.config_path))
        ttk.Entry(top, textvariable=self.config_path_var, width=28).pack(side="left", padx=5)
        ttk.Button(top, text="Open…", command=self._browse_config).pack(side="left")
        ttk.Button(top, text="Save rules", command=self._save_config).pack(side="left", padx=3)

        self.tabs = ttk.Notebook(self.root)
        self.tabs.pack(fill="both", expand=True, padx=8, pady=4)
        self.calendar_tab = ttk.Frame(self.tabs, padding=8)
        self.groups_tab = ttk.Frame(self.tabs, padding=8)
        self.tabs.add(self.calendar_tab, text="Calendar")
        self.tabs.add(self.groups_tab, text="Groups & slots")
        self._build_calendar_tab()
        self._build_groups_tab()
        ttk.Label(self.root, textvariable=self.status_var, padding=6, anchor="w").pack(fill="x")

    def _build_calendar_tab(self):
        controls = ttk.Frame(self.calendar_tab)
        controls.pack(fill="x", pady=(0, 6))
        ttk.Label(controls, text="View").pack(side="left")
        ttk.Combobox(controls, textvariable=self.view_var, values=["Week", "Month"], state="readonly", width=9).pack(side="left", padx=5)
        ttk.Label(controls, text="Anchor date (YYYY-MM-DD)").pack(side="left", padx=(12, 4))
        ttk.Entry(controls, textvariable=self.anchor_var, width=14).pack(side="left")
        ttk.Button(controls, text="Show", command=self._refresh_calendar).pack(side="left", padx=5)
        ttk.Button(controls, text="Today", command=self._today).pack(side="left")
        columns = ("date", "day", "start", "end", "event", "source")
        self.calendar_tree = ttk.Treeview(self.calendar_tab, columns=columns, show="headings", selectmode="browse")
        for col, label, width in [
            ("date", "Date", 100), ("day", "Day", 95), ("start", "Start", 75),
            ("end", "End", 75), ("event", "Event", 360), ("source", "Type", 120),
        ]:
            self.calendar_tree.heading(col, text=label)
            self.calendar_tree.column(col, width=width, anchor="w")
        self.calendar_tree.pack(fill="both", expand=True)
        form = ttk.LabelFrame(self.calendar_tab, text="Add event / custom time", padding=8)
        form.pack(fill="x", pady=(8, 0))
        self.event_name = tk.StringVar()
        self.event_date = tk.StringVar(value=date.today().isoformat())
        self.event_start = tk.StringVar(value="13:00")
        self.event_end = tk.StringVar(value="14:00")
        self.event_repeat = tk.StringVar(value="Selected date")
        self.event_priority = tk.StringVar(value="NORMAL")
        fields = [
            ("Event name", self.event_name, 22), ("Date", self.event_date, 12),
            ("Start", self.event_start, 8), ("End", self.event_end, 8),
        ]
        for label, var, width in fields:
            cell = ttk.Frame(form)
            cell.pack(side="left", padx=4)
            ttk.Label(cell, text=label).pack(anchor="w")
            ttk.Entry(cell, textvariable=var, width=width).pack()
        repeat = ttk.Frame(form)
        repeat.pack(side="left", padx=4)
        ttk.Label(repeat, text="Repeat").pack(anchor="w")
        ttk.Combobox(repeat, textvariable=self.event_repeat, values=["Selected date", "Every week", "Every day"], state="readonly", width=13).pack()
        priority = ttk.Frame(form)
        priority.pack(side="left", padx=4)
        ttk.Label(priority, text="Priority").pack(anchor="w")
        ttk.Combobox(priority, textvariable=self.event_priority, values=["CRITICAL", "HIGH", "NORMAL", "LOW"], state="readonly", width=10).pack()
        ttk.Button(form, text="Apply / add event", command=self._add_event).pack(side="left", padx=8)
        ttk.Button(form, text="Remove selected rule", command=self._remove_event).pack(side="left", padx=4)

    def _build_groups_tab(self):
        bar = ttk.Frame(self.groups_tab)
        bar.pack(fill="x", pady=(0, 6))
        ttk.Label(bar, text="Group").pack(side="left")
        self.group_var = tk.StringVar()
        self.group_combo = ttk.Combobox(bar, textvariable=self.group_var, state="readonly", width=24)
        self.group_combo.pack(side="left", padx=5)
        self.group_combo.bind("<<ComboboxSelected>>", lambda _e: self._refresh_slots())
        ttk.Button(bar, text="Add School template", command=self._add_school_template).pack(side="left", padx=4)
        ttk.Button(bar, text="Add blank group", command=self._add_blank_group).pack(side="left", padx=4)
        ttk.Button(bar, text="Apply selected slot", command=self._apply_slot).pack(side="right", padx=4)
        ttk.Label(self.groups_tab, text="Group rules generate slots. Select a row, type a subject, then Apply. Blank slots can use custom times.").pack(anchor="w", pady=(0, 5))

        panes = ttk.Panedwindow(self.groups_tab, orient="horizontal")
        panes.pack(fill="both", expand=True)
        left = ttk.Frame(panes, padding=4)
        right = ttk.Frame(panes, padding=4)
        panes.add(left, weight=3)
        panes.add(right, weight=2)
        cols = ("day", "time", "slot", "event")
        self.slot_tree = ttk.Treeview(left, columns=cols, show="headings", selectmode="browse")
        for col, label, width in [("day", "Day", 80), ("time", "Time", 115), ("slot", "Slot", 70), ("event", "Subject / default", 220)]:
            self.slot_tree.heading(col, text=label)
            self.slot_tree.column(col, width=width, anchor="w")
        self.slot_tree.pack(fill="both", expand=True)
        self.slot_tree.bind("<<TreeviewSelect>>", self._slot_selected)

        editor = ttk.LabelFrame(right, text="Selected slot", padding=8)
        editor.pack(fill="x")
        self.slot_name = tk.StringVar()
        self.slot_start = tk.StringVar()
        self.slot_end = tk.StringVar()
        self.slot_day = tk.StringVar()
        self.slot_priority = tk.StringVar(value="HIGH")
        for label, var in [("Subject / event name", self.slot_name), ("Day (MON..SUN)", self.slot_day), ("Start time", self.slot_start), ("End time", self.slot_end)]:
            row = ttk.Frame(editor)
            row.pack(fill="x", pady=3)
            ttk.Label(row, text=label, width=20).pack(side="left")
            ttk.Entry(row, textvariable=var).pack(side="left", fill="x", expand=True)
        row = ttk.Frame(editor)
        row.pack(fill="x", pady=3)
        ttk.Label(row, text="Priority override", width=20).pack(side="left")
        ttk.Combobox(row, textvariable=self.slot_priority, values=["", "CRITICAL", "HIGH", "NORMAL", "LOW"], state="readonly").pack(side="left", fill="x", expand=True)
        ttk.Label(right, text="Group defaults").pack(anchor="w", pady=(12, 3))
        self.group_info = tk.Text(right, height=8, wrap="word", state="disabled")
        self.group_info.pack(fill="x")
        ttk.Button(right, text="Export group slots to calendar", command=self._export_group).pack(fill="x", pady=(10, 3))
        ttk.Label(right, text="Breaks are reserved defaults. Slot generation respects them; slots with no preset can be edited to custom times.", wraplength=300).pack(anchor="w", pady=4)

    def _load_data(self):
        try:
            self.calendar_path = Path(self.calendar_path_var.get().strip() or "calendar.json")
            self.config_path = Path(self.config_path_var.get().strip() or "groups_schedules.json")
            self.state = CalendarState.load(self.calendar_path) if self.calendar_path.exists() else CalendarState()
            self.config = load_config(self.config_path) if self.config_path.exists() else {"version": 1, "groups": [], "schedules": []}
            self._refresh_calendar()
            self._refresh_groups()
            self.status_var.set(f"Loaded {self.calendar_path} and {self.config_path}")
        except Exception as exc:
            messagebox.showerror("Load failed", str(exc))

    def _browse_calendar(self):
        name = filedialog.askopenfilename(title="Open calendar", filetypes=[("Calendar JSON", "*.json"), ("All files", "*.*")])
        if name:
            self.calendar_path_var.set(name)
            self._load_data()

    def _browse_config(self):
        name = filedialog.askopenfilename(title="Open group rules", filetypes=[("JSON", "*.json"), ("All files", "*.*")])
        if name:
            self.config_path_var.set(name)
            self._load_data()

    def _save_calendar(self):
        try:
            self.calendar_path = Path(self.calendar_path_var.get().strip())
            self.state.save(self.calendar_path)
            self.status_var.set(f"Saved {self.calendar_path}")
        except Exception as exc:
            messagebox.showerror("Save failed", str(exc))

    def _save_config(self):
        try:
            self.config_path = Path(self.config_path_var.get().strip())
            save_config(self.config, self.config_path)
            self.status_var.set(f"Saved group rules to {self.config_path}")
        except Exception as exc:
            messagebox.showerror("Save failed", str(exc))

    def _range(self):
        anchor = date.fromisoformat(self.anchor_var.get().strip())
        if self.view_var.get() == "Month":
            start = anchor.replace(day=1)
            end = anchor.replace(day=month_calendar.monthrange(anchor.year, anchor.month)[1])
        else:
            start = anchor - timedelta(days=anchor.weekday())
            end = start + timedelta(days=6)
        return start, end

    def _refresh_calendar(self):
        try:
            start, end = self._range()
            self.calendar_tree.delete(*self.calendar_tree.get_children())
            items = self.state.resolver().build_actual_schedule(start, end)
            for item in items:
                self.calendar_tree.insert("", "end", values=(
                    item.start.date().isoformat(), item.start.strftime("%A"),
                    item.start.strftime("%H:%M"), item.end.strftime("%H:%M"),
                    item.name, "Calendar rule",
                ), tags=("event",))
            if not items:
                self.calendar_tree.insert("", "end", values=(start.isoformat(), "", "", "", "No scheduled events in this range", ""))
            self.status_var.set(f"Showing {start.isoformat()} to {end.isoformat()}")
        except Exception as exc:
            messagebox.showerror("Calendar view", str(exc))

    def _today(self):
        self.anchor_var.set(date.today().isoformat())
        self.event_date.set(date.today().isoformat())
        self._refresh_calendar()

    def _add_event(self):
        try:
            name = self.event_name.get().strip()
            if not name:
                raise ValueError("Enter an event name.")
            target = date.fromisoformat(self.event_date.get().strip())
            start, end = self.event_start.get().strip(), self.event_end.get().strip()
            if _minutes(start) >= _minutes(end):
                raise ValueError("Start time must be before end time.")
            repeat = self.event_repeat.get()
            if repeat == "Selected date":
                recurrence = Recurrence(type=RecurrenceType.NONE)
                constraints = TimeConstraints(fixed_interval=TimeWindow(start, end), date=target.isoformat())
                rule_id = f"custom__{target.isoformat().replace('-', '')}__{len(self.state.rules)+1}"
            elif repeat == "Every day":
                recurrence = Recurrence(type=RecurrenceType.DAILY)
                constraints = TimeConstraints(fixed_interval=TimeWindow(start, end))
                rule_id = f"custom_daily__{len(self.state.rules)+1}"
            else:
                recurrence = Recurrence(type=RecurrenceType.WEEKLY, days_of_week=[DAYS[target.weekday()]])
                constraints = TimeConstraints(fixed_interval=TimeWindow(start, end))
                rule_id = f"custom_weekly__{len(self.state.rules)+1}"
            rule = ActivityRule(id=rule_id, name=name, flexibility=Flexibility.FIXED,
                priority=Priority(self.event_priority.get()), recurrence=recurrence, time_constraints=constraints)
            candidate = copy.deepcopy(self.state)
            candidate.rules.append(rule)
            candidate._validate()
            self.state = candidate
            self._save_calendar()
            self.anchor_var.set(target.isoformat())
            self._refresh_calendar()
            self.status_var.set(f"Applied {name}")
        except Exception as exc:
            messagebox.showerror("Cannot apply event", str(exc))

    def _remove_event(self):
        selected = self.calendar_tree.selection()
        if not selected:
            return messagebox.showinfo("Remove event", "Select a calendar row first.")
        values = self.calendar_tree.item(selected[0], "values")
        name = values[4] if len(values) > 4 else ""
        matches = [r for r in self.state.rules if r.name == name or r.name.startswith(name + " (")]
        if not matches:
            return messagebox.showinfo("Remove event", "The selected row could not be matched to a rule. Select an event created as a rule, not an empty-range row.")
        if not messagebox.askyesno("Remove event", f"Remove rule '{matches[0].name}'?"):
            return
        self.state.rules = [r for r in self.state.rules if r.id != matches[0].id]
        self.state.exceptions = [e for e in self.state.exceptions if e.rule_id != matches[0].id]
        self._save_calendar()
        self._refresh_calendar()

    def _refresh_groups(self):
        names = [f"{g['name']} [{g['id']}]" for g in self.config["groups"]]
        self.group_combo["values"] = names
        if names and self.group_var.get() not in names:
            self.group_var.set(names[0])
        self._refresh_slots()

    def _selected_group(self):
        label = self.group_var.get()
        group_id = label.rsplit("[", 1)[-1].rstrip("]") if "[" in label else ""
        return next((g for g in self.config["groups"] if g["id"] == group_id), None)

    def _selected_schedule(self):
        group = self._selected_group()
        return next((s for s in self.config["schedules"] if group and s["group_id"] == group["id"]), None)

    def _refresh_slots(self):
        if not hasattr(self, "slot_tree"):
            return
        self.slot_tree.delete(*self.slot_tree.get_children())
        schedule = self._selected_schedule()
        group = self._selected_group()
        self.group_info.configure(state="normal")
        self.group_info.delete("1.0", tk.END)
        if not schedule or not group:
            self.group_info.insert("1.0", "No group selected. Add a preset or create a blank group.")
            self.group_info.configure(state="disabled")
            return
        breaks = sorted(schedule.get("breaks", []), key=lambda b: b["start_time"])
        self.group_info.insert("1.0", (
            f"{group['name']}\nDays: {', '.join(schedule['days'])}\n"
            f"Range: {schedule['start_time']}–{schedule['end_time']}\n"
            f"Default slot: {schedule['period_minutes']} minutes\n"
            + "Breaks: " + (", ".join(f"{b.get('name','Break')} {b['start_time']}–{b['end_time']}" for b in breaks) or "none")
        ))
        self.group_info.configure(state="disabled")
        activities = schedule.get("activities", [])
        for day in schedule["days"]:
            cursor = _minutes(schedule["start_time"])
            finish = _minutes(schedule["end_time"])
            period = 1
            day_breaks = [b for b in breaks if _minutes(b["end_time"]) > cursor and _minutes(b["start_time"]) < finish]
            while cursor < finish:
                br = next((b for b in day_breaks if _minutes(b["start_time"]) == cursor), None)
                if br:
                    self.slot_tree.insert("", "end", values=(day.title(), f"{br['start_time']}–{br['end_time']}", "Break", br.get("name", "Break")), tags=("break",))
                    cursor = _minutes(br["end_time"])
                    continue
                next_break = min([_minutes(b["start_time"]) for b in day_breaks if _minutes(b["start_time"]) > cursor] or [finish])
                slot_end = min(cursor + schedule["period_minutes"], next_break, finish)
                if slot_end <= cursor:
                    break
                activity = next((a for a in activities if a["day"] == day and int(a.get("period", 0)) == period), None)
                self.slot_tree.insert("", "end", values=(day.title(), f"{_clock(cursor)}–{_clock(slot_end)}", str(period), activity["name"] if activity else ""), tags=("break" if activity and activity.get("is_break") else "slot",))
                cursor = slot_end
                period += 1
        self.slot_tree.tag_configure("break", background="#eeeeee")

    def _slot_selected(self, _event=None):
        selected = self.slot_tree.selection()
        if not selected:
            return
        day, span, period, name = self.slot_tree.item(selected[0], "values")
        if period == "Break":
            self.slot_name.set(name)
            self.slot_day.set(day.upper()[:3])
            self.slot_start.set(span.split("–")[0])
            self.slot_end.set(span.split("–")[-1])
            return
        self.slot_name.set(name)
        self.slot_day.set(day.upper()[:3])
        self.slot_start.set(span.split("–")[0])
        self.slot_end.set(span.split("–")[-1])
        self.slot_priority.set("")

    def _apply_slot(self):
        selected = self.slot_tree.selection()
        schedule = self._selected_schedule()
        if not selected or not schedule:
            return messagebox.showinfo("Apply slot", "Select a slot and a group first.")
        day, span, period, _old_name = self.slot_tree.item(selected[0], "values")
        if period == "Break":
            return messagebox.showwarning("Reserved break", "This is a preset break. Edit the break in group rules instead of assigning a subject over it.")
        name = self.slot_name.get().strip()
        start, end = self.slot_start.get().strip(), self.slot_end.get().strip()
        day_code = self.slot_day.get().strip().upper()[:3]
        if not name:
            return messagebox.showerror("Apply slot", "Type a subject name or select an existing subject.")
        try:
            if day_code not in DAYS or _minutes(start) >= _minutes(end):
                raise ValueError("Enter a valid day and start/end time.")
            period_no = int(period)
            activities = schedule.setdefault("activities", [])
            activity = next((a for a in activities if a["day"] == day_code and int(a.get("period", 0)) == period_no), None)
            if activity is None:
                activity = {"id": f"{day_code.lower()}_p{period_no}", "day": day_code, "period": period_no, "name": name, "start_time": start, "end_time": end}
                activities.append(activity)
            else:
                activity.update({"name": name, "start_time": start, "end_time": end})
            priority = self.slot_priority.get().strip()
            if priority:
                activity["priority"] = priority
            else:
                activity.pop("priority", None)
            save_config(self.config, self.config_path)
            self._export_group(silent=True)
            self._refresh_slots()
            self.status_var.set(f"Applied {name} to {day_code} {start}–{end}")
        except Exception as exc:
            messagebox.showerror("Apply slot", str(exc))

    def _add_school_template(self):
        if any(g["id"] == "school" for g in self.config["groups"]):
            return messagebox.showinfo("School template", "A school group already exists in this configuration.")
        template = _school_template()
        self.config["groups"].extend(template["groups"])
        self.config["schedules"].extend(template["schedules"])
        self._refresh_groups()
        self.group_var.set("School [school]")
        self._refresh_slots()
        self._save_config()
        self.status_var.set("School template added. Edit its slots and apply subjects.")

    def _add_blank_group(self):
        index = 1
        ids = {g["id"] for g in self.config["groups"]}
        while f"group_{index}" in ids:
            index += 1
        gid = f"group_{index}"
        self.config["groups"].append({"id": gid, "name": f"Group {index}", "priority": "NORMAL", "flexibility": "FLEXIBLE", "override_policy": "overrideable", "description": ""})
        self.config["schedules"].append({"id": f"schedule_{index}", "name": f"Schedule {index}", "group_id": gid, "days": ["MON", "TUE", "WED", "THU", "FRI"], "start_time": "09:00", "end_time": "17:00", "period_minutes": 60, "breaks": [], "activities": []})
        self._refresh_groups()
        self.group_var.set(f"Group {index} [{gid}]")
        self._refresh_slots()
        self._save_config()
        self.status_var.set(f"Created {gid}; edit schedule data in the group configuration file if needed.")

    def _export_group(self, silent=False):
        try:
            self.config_path = Path(self.config_path_var.get().strip() or "groups_schedules.json")
            self.calendar_path = Path(self.calendar_path_var.get().strip() or "calendar.json")
            count = export_to_calendar(self.config, self.calendar_path)
            self.state = CalendarState.load(self.calendar_path)
            self._refresh_calendar()
            if not silent:
                messagebox.showinfo("Export complete", f"Exported {count} group timetable rules to {self.calendar_path}.")
            self.status_var.set(f"Exported {count} group rules")
        except Exception as exc:
            messagebox.showerror("Export failed", str(exc))


def main():
    root = tk.Tk()
    UnifiedCalendarGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
