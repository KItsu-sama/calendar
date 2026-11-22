# cal_engine/group/group_schedule_gui.py
"""Tkinter editor for reusable groups and timetables.

Groups are organizational metadata only (id, name, description); they never
carry scheduling policy. Timetables hold the scheduling rules (days, times,
slot length, place tag, slots/activities and breaks).
"""
from __future__ import annotations

import copy
import json
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any

from .group_schedule import export_to_calendar, load_config, save_config

DAYS = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]
DAY_LABELS = {
    "MON": "Mon",
    "TUE": "Tue",
    "WED": "Wed",
    "THU": "Thu",
    "FRI": "Fri",
    "SAT": "Sat",
    "SUN": "Sun",
}


class GroupScheduleEditor:

  def __init__(self, root: tk.Tk, config_path: str | Path | None = None):
    self.root = root
    root.title("cal — Group & Timetable Editor")
    root.geometry("1120x760")

    self.config: dict[str, Any] = {
        "version": 2,
        "groups": [],
        "timetables": [],
    }  

    self.config_path = (
        Path(config_path) if config_path else Path("groups_schedules.json")
    )
    self._build()

    if self.config_path.is_file():
      try:
        self.config = load_config(self.config_path)
        self._status(f"Loaded {self.config_path}")
      except (ValueError, OSError) as exc:
        messagebox.showerror("Configuration load failed", str(exc))
        self._status("Configuration could not be loaded.")
    else:
      self._status(
          "No configuration loaded. Open an existing file or create "
          "a group and timetable, then save."
      )

    self._populate_groups()
    self._populate_timetables()
    self._populate_activities()
    self._load_group_fields()
    self._load_timetable_fields()

  def _build(self):
    top = ttk.Frame(self.root, padding=10)
    top.pack(fill="x")
    ttk.Label(top, text="Configuration file").pack(side="left")
    self.path_var = tk.StringVar(value=str(self.config_path))
    ttk.Entry(top, textvariable=self.path_var, width=55).pack(
        side="left", padx=6
    )
    ttk.Button(top, text="Open…", command=self.open_config).pack(side="left")
    ttk.Button(top, text="Load", command=self.load).pack(side="left", padx=4)
    ttk.Button(top, text="Save configuration", command=self.save).pack(
        side="left"
    )

    pane = ttk.Panedwindow(self.root, orient="horizontal")
    pane.pack(fill="both", expand=True, padx=10, pady=4)
    left = ttk.Frame(pane, padding=8)
    right = ttk.Frame(pane, padding=8)
    pane.add(left, weight=1)
    pane.add(right, weight=4)
    ttk.Label(left, text="Groups").pack(anchor="w")
    self.group_list = tk.Listbox(left, height=7, exportselection=False)
    self.group_list.pack(fill="x", pady=4)
    self.group_list.bind(
        "<<ListboxSelect>>", lambda _e: self._load_group_fields()
    )
    ttk.Button(left, text="Add group", command=self.add_group).pack(
        fill="x", pady=2
    )
    ttk.Button(left, text="Apply group edits", command=self.apply_group).pack(
        fill="x", pady=2
    )
    ttk.Separator(left).pack(fill="x", pady=10)
    ttk.Label(left, text="Timetables").pack(anchor="w")
    self.timetable_list = tk.Listbox(left, height=8, exportselection=False)
    self.timetable_list.pack(fill="x", pady=4)
    self.timetable_list.bind(
        "<<ListboxSelect>>", lambda _e: self._select_timetable()
    )
    ttk.Button(left, text="Add timetable", command=self.add_timetable).pack(
        fill="x", pady=2
    )
    ttk.Button(
        left, text="Apply timetable edits", command=self.apply_timetable
    ).pack(fill="x", pady=2)

    self.tabs = ttk.Notebook(right)
    self.tabs.pack(fill="both", expand=True)
    group_tab = ttk.Frame(self.tabs, padding=12)
    timetable_tab = ttk.Frame(self.tabs, padding=12)
    activity_tab = ttk.Frame(self.tabs, padding=8)
    self.tabs.add(group_tab, text="Group metadata")
    self.tabs.add(timetable_tab, text="Timetable settings")
    self.tabs.add(activity_tab, text="Slots / break")
    # Groups are organizational metadata only: id, name and description. They
    # deliberately carry no scheduling policy (priority, flexibility,
    # override_policy); scheduling belongs to the timetable.
    self.group_vars = {
        k: tk.StringVar()
        for k in (
            "id",
            "name",
            "description",
        )
    }
    self._form(
        group_tab,
        [
            ("ID", "id"),
            ("Display name", "name"),
            ("Description", "description"),
        ],
        self.group_vars,
    )
    self.timetable_vars = {
        k: tk.StringVar()
        for k in (
            "id",
            "name",
            "description",
            "place_tag",
            "days",
            "start_time",
            "end_time",
            "period_minutes",
        )
    }
    self._form(
        timetable_tab,
        [
            ("ID", "id"),
            ("Display name", "name"),
            ("Description", "description"),
            ("Place tag", "place_tag"),
            ("Days (MON,TUE,...)", "days"),
            ("Start time", "start_time"),
            ("End time", "end_time"),
            ("Period length (minutes)", "period_minutes"),
        ],
        self.timetable_vars,
    )
    ttk.Label(
        timetable_tab,
        text=(
            "Days use codes MON,TUE,WED,THU,FRI,SAT,SUN. The place tag lets you"
            " re-time or re-place a whole timetable and swap it out easily."
        ),
        wraplength=600,
    ).pack(anchor="w", pady=12)

    cols = ("day", "period", "name", "start_time", "end_time", "priority", "group_ids")
    self.tree = ttk.Treeview(
        activity_tab, columns=cols, show="headings", height=15, selectmode="browse"
    )
    for col, title, width in [
        ("day", "Day", 70),
        ("period", "Period", 60),
        ("name", "Activity", 200),
        ("start_time", "Start", 85),
        ("end_time", "End", 85),
        ("priority", "Priority", 100),
        ("group_ids", "Groups", 150),
    ]:
      self.tree.heading(col, text=title)
      self.tree.column(col, width=width, anchor="w")
    self.tree.pack(fill="both", expand=True)
    edit = ttk.LabelFrame(activity_tab, text="Selected slot", padding=8)
    edit.pack(fill="x", pady=8)
    self.act_vars = {
        k: tk.StringVar()
        for k in ("day", "period", "name", "start_time", "end_time", "priority", "group_ids")
    }
    self.act_group_var = tk.StringVar()
    self._form(
        edit,
        [
            ("Day", "day"),
            ("Period", "period"),
            ("Name", "name"),
            ("Start time", "start_time"),
            ("End time", "end_time"),
            ("Priority", "priority"),
            ("Group IDs (space-separated)", "group_ids"),
        ],
        self.act_vars,
    )
    self.tree.bind("<<TreeviewSelect>>", self._load_activity_fields)
    buttons = ttk.Frame(activity_tab)
    buttons.pack(fill="x")
    ttk.Button(
        buttons, text="Apply selected activity", command=self.apply_activity
    ).pack(side="left")
    ttk.Button(buttons, text="Add activity", command=self.add_activity).pack(
        side="left", padx=5
    )
    ttk.Button(
        buttons, text="Delete activity", command=self.delete_activity
    ).pack(side="left")

    picker = ttk.Frame(activity_tab)
    picker.pack(fill="x", pady=(4, 0))
    ttk.Label(picker, text="Add activity to group:").pack(side="left")
    self.act_group_combo = ttk.Combobox(
        picker,
        textvariable=self.act_group_var,
        values=[],
        state="readonly",
        width=26,
    )
    self.act_group_combo.pack(side="left", padx=4)
    ttk.Button(
        picker, text="Add to group", command=self._add_activity_to_group
    ).pack(side="left")
    ttk.Button(
        picker, text="Remove from group",
        command=self._remove_activity_from_group,
    ).pack(side="left", padx=4)
    ttk.Label(
        picker,
        text=(
            "Group IDs are space-separated; the group is metadata only and "
            "never drives scheduling policy."
        ),
        foreground="gray",
    ).pack(side="left", padx=(10, 0))
    ttk.Separator(activity_tab).pack(fill="x", pady=8)
    ttk.Label(
        activity_tab,
        text=(
            "Breaks are editable in the JSON configuration. Each break has its"
            " own priority and override policy; HIGH priority does not itself"
            " mean protected."
        ),
        wraplength=760,
    ).pack(anchor="w")

    bottom = ttk.Frame(self.root, padding=10)
    bottom.pack(fill="x")
    ttk.Button(
        bottom, text="Export to data//calendar.json…", command=self.export
    ).pack(side="left")
    self.status = tk.StringVar(value="Ready")
    ttk.Label(bottom, textvariable=self.status).pack(side="left", padx=12)

  def _form(self, parent, fields, variables, combos=None, compact=False):
    combos = combos or {}
    for i, (label, key) in enumerate(fields):
      row = ttk.Frame(parent)
      row.pack(fill="x", pady=3)
      ttk.Label(row, text=label, width=27 if not compact else 27).pack(
          side="left"
      )
      if key in combos and combos[key]:
        widget = ttk.Combobox(
            row,
            textvariable=variables[key],
            values=combos[key],
            state="normal",
            width=30,
        )
      else:
        widget = ttk.Entry(row, textvariable=variables[key], width=34)
      widget.pack(side="left", fill="x", expand=True)

  def _status(self, msg):
    self.status.set(msg)

  def _populate_groups(self):
    self.group_list.delete(0, tk.END)
    for g in self.config["groups"]:
      self.group_list.insert(tk.END, f"{g['name']} [{g['id']}]")
    # Refresh the activity group picker with the current group IDs.
    if hasattr(self, "act_group_combo"):
      self.act_group_combo["values"] = [g["id"] for g in self.config["groups"]]

  def _populate_timetables(self):
    self.timetable_list.delete(0, tk.END)
    for t in self.config["timetables"]:
      self.timetable_list.insert(tk.END, f"{t['name']} [{t['id']}]")
    if self.config["timetables"] and not self.timetable_list.curselection():
      self.timetable_list.selection_set(0)

  def _group(self):
    sel = self.group_list.curselection()
    return self.config["groups"][sel[0]] if sel else None

  def _timetable(self):
    sel = self.timetable_list.curselection()
    return self.config["timetables"][sel[0]] if sel else None

  def _load_group_fields(self):
    g = self._group()
    if not g:
      return
    for k, v in self.group_vars.items():
      v.set(str(g.get(k, "")))

  def _load_timetable_fields(self):
    t = self._timetable()
    if not t:
      return
    for k, v in self.timetable_vars.items():
      value = t.get(k, "")
      if k == "days":
        value = ",".join(t.get("days", []))
      v.set(str(value))

  def _select_timetable(self):
    self._load_timetable_fields()
    self._populate_activities()

  def _populate_activities(self):
    self.tree.delete(*self.tree.get_children())
    t = self._timetable()
    if not t:
      return
    for i, a in enumerate(t.get("activities", [])):
      self.tree.insert(
          "",
          "end",
          iid=str(i),
          values=(
              a.get("day", "MON"),
              a.get("period", ""),
              a.get("name", ""),
              a.get("start_time", ""),
              a.get("end_time", ""),
              a.get("priority", ""),
              ", ".join(a.get("group_ids", [])),
          ),
      )

  def _selected_activity(self):
    t = self._timetable()
    selected = self.tree.selection()
    if not t or not selected:
      return None
    idx = int(selected[0])
    return idx, t["activities"][idx]

  def _load_activity_fields(self, _event=None):
    selected = self._selected_activity()
    if not selected:
      return
    _, a = selected
    for k, v in self.act_vars.items():
      v.set(str(a.get(k, "")))
    # Refresh the group picker with the current group IDs.
    if hasattr(self, "act_group_combo"):
      self.act_group_combo["values"] = [g["id"] for g in self.config["groups"]]
    self.act_group_var.set("")

  def _add_activity_to_group(self):
    selected = self._selected_activity()
    if not selected:
      return messagebox.showinfo("Activity", "Select an activity first.")
    group_id = self.act_group_var.get().strip()
    if not group_id:
      return messagebox.showinfo("Activity", "Pick a group from the dropdown first.")
    idx, a = selected
    group_ids = list(a.get("group_ids", []))
    if group_id in group_ids:
      return messagebox.showinfo("Activity", f"Already in group '{group_id}'.")
    group_ids.append(group_id)
    a["group_ids"] = group_ids
    self._populate_activities()
    self.tree.selection_set(str(idx))
    self._load_activity_fields()
    self._status(f"Activity added to group '{group_id}' (not saved yet).")

  def _remove_activity_from_group(self):
    selected = self._selected_activity()
    if not selected:
      return messagebox.showinfo("Activity", "Select an activity first.")
    group_id = self.act_group_var.get().strip()
    if not group_id:
      return messagebox.showinfo("Activity", "Pick a group from the dropdown first.")
    idx, a = selected
    group_ids = list(a.get("group_ids", []))
    if group_id not in group_ids:
      return messagebox.showinfo(
          "Activity", f"Not in group '{group_id}'."
      )
    group_ids.remove(group_id)
    a["group_ids"] = group_ids
    self._populate_activities()
    self.tree.selection_set(str(idx))
    self._load_activity_fields()
    self._status(f"Activity removed from group '{group_id}' (not saved yet).")

  def add_group(self):
    base = "group"
    ids = {g["id"] for g in self.config["groups"]}
    n = 1
    while f"{base}_{n}" in ids:
      n += 1
    self.config["groups"].append({
        "id": f"group_{n}",
        "name": f"New group {n}",
        "description": "",
    })
    self._populate_groups()
    self.group_list.selection_set(tk.END)
    self._load_group_fields()
    self.tabs.select(0)
    self._status("New group added; edit fields and apply.")

  def apply_group(self):
    g = self._group()
    if not g:
      return messagebox.showinfo("Group", "Select a group first.")
    values = {k: v.get().strip() for k, v in self.group_vars.items()}
    g.update(values)
    self._populate_groups()
    self._status(f"Updated group {g['id']} (not saved yet).")

  def add_timetable(self):
    base = "timetable"
    ids = {t["id"] for t in self.config["timetables"]}
    n = 1
    while f"{base}_{n}" in ids:
      n += 1
    self.config["timetables"].append({
        "id": f"timetable_{n}",
        "name": f"New timetable {n}",
        "description": "",
        "place_tag": "",
        "days": ["MON", "TUE", "WED", "THU", "FRI"],
        "start_time": "09:00",
        "end_time": "10:00",
        "period_minutes": 60,
        "breaks": [],
        "activities": [],
    })
    self._populate_timetables()
    self.timetable_list.selection_set(tk.END)
    self._load_timetable_fields()
    self._populate_activities()
    self.tabs.select(1)
    self._status("New timetable added; edit fields and apply.")

  def apply_timetable(self):
    t = self._timetable()
    if not t:
      return messagebox.showinfo("Timetable", "Select a timetable first.")
    try:
      v = {k: x.get().strip() for k, x in self.timetable_vars.items()}
      t.update({
          "id": v["id"],
          "name": v["name"],
          "description": v["description"],
          "place_tag": v["place_tag"],
          "days": [
              d.strip().upper() for d in v["days"].split(",") if d.strip()
          ],
          "start_time": v["start_time"],
          "end_time": v["end_time"],
          "period_minutes": int(v["period_minutes"]),
      })
      self._populate_timetables()
      self._status(f"Updated timetable {t['id']} (not saved yet).")
    except Exception as e:
      messagebox.showerror("Timetable", str(e))

  def apply_activity(self):
    selected = self._selected_activity()
    if not selected:
      return messagebox.showinfo("Activity", "Select an activity first.")
    idx, a = selected
    try:
      v = {k: x.get().strip() for k, x in self.act_vars.items()}
      group_ids = [x.strip() for x in v["group_ids"].split() if x.strip()]
      a.update({
          "day": v["day"].upper(),
          "period": int(v["period"]),
          "name": v["name"],
          "start_time": v["start_time"],
          "end_time": v["end_time"],
          "group_ids": group_ids,
      })
      if v["priority"]:
        a["priority"] = v["priority"].upper()
      else:
        a.pop("priority", None)
      self._populate_activities()
      self.tree.selection_set(str(idx))
      self._status("Slot updated (not saved yet).")
    except Exception as e:
      messagebox.showerror("Activity", str(e))

  def add_activity(self):
    t = self._timetable()
    if not t:
      return messagebox.showinfo("Activity", "Select a timetable first.")
    ids = {str(a.get("id", "")) for a in t.get("activities", [])}
    n = 1
    while f"activity_{n}" in ids:
      n += 1
    t["activities"].append({
        "id": f"activity_{n}",
        "day": t.get("days", ["MON"])[0],
        "period": 1,
        "name": "New activity",
        "start_time": t["start_time"],
        "end_time": t["end_time"],
        "group_ids": [],
    })
    self._populate_activities()
    self.tree.selection_set(str(len(t["activities"]) - 1))
    self._load_activity_fields()
    self._status("Activity added; edit its fields.")

  def delete_activity(self):
    selected = self._selected_activity()
    t = self._timetable()
    if not selected or not t:
      return messagebox.showinfo("Activity", "Select an activity first.")
    idx, a = selected
    if messagebox.askyesno("Delete activity", f"Delete '{a.get('name')}'?"):
      del s["activities"][idx]
      self._populate_activities()
      self._status("Activity deleted (not saved yet).")

  def open_config(self):
    name = filedialog.askopenfilename(
        title="Open group/timetable configuration",
        filetypes=[("JSON", "*.json"), ("All files", "*.*")],
    )
    if name:
      self.path_var.set(name)
      self.load()

  def load(self):
    try:
      self.config_path = Path(self.path_var.get().strip())
      self.config = load_config(self.config_path)
      self._populate_groups()
      self._populate_timetables()
      self._populate_activities()
      self._load_group_fields()
      self._load_timetable_fields()
      self._status(f"Loaded {self.config_path}")
    except Exception as e:
      messagebox.showerror("Load failed", str(e))

  def save(self):
    try:
      self.config_path = Path(self.path_var.get().strip())
      save_config(self.config, self.config_path)
      self._status(f"Saved {self.config_path}")
    except Exception as e:
      messagebox.showerror("Save failed", str(e))

  def export(self):
    try:
      from tkinter import filedialog

      path = filedialog.asksaveasfilename(
          title="Export / update calendar state",
          initialfile="data/calendar.json",
          defaultextension=".json",
      )
      if not path:
        return
      count = export_to_calendar(self.config, path)
      self._status(f"Exported {count} timetable rules to {path}")
      messagebox.showinfo(
          "Export complete",
          f"Exported {count} timetable rules. Existing non-timetable rules were"
          f" retained. Groups are organizational metadata only; scheduling"
          " policy lives on the timetable and its slots.",
      )
    except Exception as e:
      messagebox.showerror("Export failed", str(e))


def main():
  root = tk.Tk()
  GroupScheduleEditor(root)
  root.mainloop()


if __name__ == "__main__":
  main()