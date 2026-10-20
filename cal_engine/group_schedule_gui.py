"""Tkinter editor for reusable groups and schedules."""
from __future__ import annotations

import copy
import json
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .group_schedule import default_config, load_config, save_config, export_to_calendar

DAYS = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]
DAY_LABELS = {"MON":"Mon","TUE":"Tue","WED":"Wed","THU":"Thu","FRI":"Fri","SAT":"Sat","SUN":"Sun"}

class GroupScheduleEditor:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("cal — Group & Schedule Editor")
        root.geometry("1120x760")
        self.config = default_config()
        self.config_path = Path("groups_schedules.json")
        self._build()
        self._populate_groups()
        self._populate_schedules()
        self._populate_activities()
        self._load_group_fields()
        self._load_schedule_fields()
        self._status("Ready. Edit the fields, then Save configuration or Export to calendar.json.")

    def _build(self):
        top = ttk.Frame(self.root, padding=10); top.pack(fill="x")
        ttk.Label(top, text="Configuration file").pack(side="left")
        self.path_var = tk.StringVar(value=str(self.config_path))
        ttk.Entry(top, textvariable=self.path_var, width=55).pack(side="left", padx=6)
        ttk.Button(top, text="Open…", command=self.open_config).pack(side="left")
        ttk.Button(top, text="Load", command=self.load).pack(side="left", padx=4)
        ttk.Button(top, text="Save configuration", command=self.save).pack(side="left")

        pane = ttk.Panedwindow(self.root, orient="horizontal"); pane.pack(fill="both", expand=True, padx=10, pady=4)
        left = ttk.Frame(pane, padding=8); right = ttk.Frame(pane, padding=8)
        pane.add(left, weight=1); pane.add(right, weight=4)
        ttk.Label(left, text="Groups").pack(anchor="w")
        self.group_list = tk.Listbox(left, height=7, exportselection=False); self.group_list.pack(fill="x", pady=4)
        self.group_list.bind("<<ListboxSelect>>", lambda _e: self._load_group_fields())
        ttk.Button(left, text="Add group", command=self.add_group).pack(fill="x", pady=2)
        ttk.Button(left, text="Apply group edits", command=self.apply_group).pack(fill="x", pady=2)
        ttk.Separator(left).pack(fill="x", pady=10)
        ttk.Label(left, text="Schedules").pack(anchor="w")
        self.schedule_list = tk.Listbox(left, height=8, exportselection=False); self.schedule_list.pack(fill="x", pady=4)
        self.schedule_list.bind("<<ListboxSelect>>", lambda _e: self._select_schedule())
        ttk.Button(left, text="Add schedule", command=self.add_schedule).pack(fill="x", pady=2)
        ttk.Button(left, text="Apply schedule edits", command=self.apply_schedule).pack(fill="x", pady=2)

        self.tabs = ttk.Notebook(right); self.tabs.pack(fill="both", expand=True)
        group_tab = ttk.Frame(self.tabs, padding=12); sched_tab = ttk.Frame(self.tabs, padding=12); activity_tab = ttk.Frame(self.tabs, padding=8)
        self.tabs.add(group_tab, text="Group defaults"); self.tabs.add(sched_tab, text="Schedule settings"); self.tabs.add(activity_tab, text="Activities / break")
        self.group_vars = {k: tk.StringVar() for k in ("id","name","priority","flexibility","override_policy","description")}
        self._form(group_tab, [("ID", "id"),("Display name", "name"),("Priority", "priority"),("Flexibility", "flexibility"),("Override policy", "override_policy"),("Description", "description")], self.group_vars,
                   combos={"priority":["CRITICAL","HIGH","NORMAL","LOW"],"flexibility":["FIXED","CHANGEABLE","FLEXIBLE","OPTIONAL"],"override_policy":["never","overrideable","always"]})
        self.schedule_vars = {k: tk.StringVar() for k in ("id","name","group_id","days","start_time","end_time","period_minutes")}
        self._form(sched_tab, [("ID", "id"),("Display name", "name"),("Group", "group_id"),("Days (MON,TUE,...)", "days"),("Start time", "start_time"),("End time", "end_time"),("Period length (minutes)", "period_minutes")], self.schedule_vars,
                   combos={"group_id":[]})
        ttk.Label(sched_tab, text="Days use codes MON,TUE,WED,THU,FRI,SAT,SUN. Group defaults flow to schedule activities unless an activity overrides priority/flexibility.", wraplength=600).pack(anchor="w", pady=12)

        cols = ("day","period","name","start_time","end_time","priority")
        self.tree = ttk.Treeview(activity_tab, columns=cols, show="headings", height=15, selectmode="browse")
        for col, title, width in [("day","Day",70),("period","Period",60),("name","Activity",210),("start_time","Start",85),("end_time","End",85),("priority","Priority override",120)]:
            self.tree.heading(col, text=title); self.tree.column(col, width=width, anchor="w")
        self.tree.pack(fill="both", expand=True)
        edit = ttk.LabelFrame(activity_tab, text="Selected activity", padding=8); edit.pack(fill="x", pady=8)
        self.act_vars = {k:tk.StringVar() for k in ("day","period","name","start_time","end_time","priority")}
        self._form(edit, [("Day","day"),("Period","period"),("Name","name"),("Start","start_time"),("End","end_time"),("Priority override (blank = inherit)","priority")], self.act_vars,
                   combos={"day":DAYS,"priority":["","CRITICAL","HIGH","NORMAL","LOW"]}, compact=True)
        self.tree.bind("<<TreeviewSelect>>", self._load_activity_fields)
        buttons = ttk.Frame(activity_tab); buttons.pack(fill="x")
        ttk.Button(buttons, text="Apply selected activity", command=self.apply_activity).pack(side="left")
        ttk.Button(buttons, text="Add activity", command=self.add_activity).pack(side="left", padx=5)
        ttk.Button(buttons, text="Delete activity", command=self.delete_activity).pack(side="left")
        ttk.Separator(activity_tab).pack(fill="x", pady=8)
        ttk.Label(activity_tab, text="Breaks are editable in the JSON configuration. Each break has its own priority and override policy; HIGH priority does not itself mean protected.", wraplength=760).pack(anchor="w")

        bottom = ttk.Frame(self.root, padding=10); bottom.pack(fill="x")
        ttk.Button(bottom, text="Export to calendar.json…", command=self.export).pack(side="left")
        ttk.Button(bottom, text="Reset editor to school example", command=self.reset).pack(side="left", padx=8)
        self.status = tk.StringVar(value="Ready")
        ttk.Label(bottom, textvariable=self.status).pack(side="left", padx=12)

    def _form(self, parent, fields, variables, combos=None, compact=False):
        combos = combos or {}
        for i, (label, key) in enumerate(fields):
            row = ttk.Frame(parent); row.pack(fill="x", pady=3)
            ttk.Label(row, text=label, width=27 if not compact else 27).pack(side="left")
            if key in combos and combos[key]:
                widget = ttk.Combobox(row, textvariable=variables[key], values=combos[key], state="normal", width=30)
            else:
                widget = ttk.Entry(row, textvariable=variables[key], width=34)
            widget.pack(side="left", fill="x", expand=True)

    def _status(self, msg): self.status.set(msg)
    def _populate_groups(self):
        self.group_list.delete(0, tk.END)
        for g in self.config["groups"]: self.group_list.insert(tk.END, f"{g['name']} [{g['id']}]")
    def _populate_schedules(self):
        self.schedule_list.delete(0, tk.END)
        for s in self.config["schedules"]: self.schedule_list.insert(tk.END, f"{s['name']} [{s['id']}]")
        if self.config["schedules"] and not self.schedule_list.curselection(): self.schedule_list.selection_set(0)
    def _group(self):
        sel = self.group_list.curselection(); return self.config["groups"][sel[0]] if sel else None
    def _schedule(self):
        sel = self.schedule_list.curselection(); return self.config["schedules"][sel[0]] if sel else None
    def _load_group_fields(self):
        g=self._group()
        if not g: return
        for k,v in self.group_vars.items(): v.set(str(g.get(k,"")))
    def _load_schedule_fields(self):
        s=self._schedule()
        if not s: return
        for k,v in self.schedule_vars.items():
            value=s.get(k,"")
            if k=="days": value=",".join(s.get("days",[]))
            v.set(str(value))
    def _select_schedule(self): self._load_schedule_fields(); self._populate_activities()
    def _populate_activities(self):
        self.tree.delete(*self.tree.get_children())
        s=self._schedule()
        if not s: return
        for i,a in enumerate(s.get("activities",[])):
            self.tree.insert("", "end", iid=str(i), values=(a.get("day","MON"),a.get("period",""),a.get("name",""),a.get("start_time",""),a.get("end_time",""),a.get("priority","")))
    def _selected_activity(self):
        s=self._schedule(); selected=self.tree.selection()
        if not s or not selected: return None
        idx=int(selected[0]); return idx,s["activities"][idx]
    def _load_activity_fields(self, _event=None):
        selected=self._selected_activity()
        if not selected: return
        _,a=selected
        for k,v in self.act_vars.items(): v.set(str(a.get(k,"")))
    def add_group(self):
        base="group"; ids={g["id"] for g in self.config["groups"]}; n=1
        while f"{base}_{n}" in ids: n+=1
        self.config["groups"].append({"id":f"group_{n}","name":f"New group {n}","priority":"NORMAL","flexibility":"FLEXIBLE","override_policy":"overrideable","description":""})
        self._populate_groups(); self.group_list.selection_set(tk.END); self._load_group_fields(); self.tabs.select(0); self._status("New group added; edit fields and apply.")
    def apply_group(self):
        g=self._group()
        if not g: return messagebox.showinfo("Group", "Select a group first.")
        values={k:v.get().strip() for k,v in self.group_vars.items()}
        g.update(values)
        self._populate_groups(); self._status(f"Updated group {g['id']} (not saved yet).")
    def add_schedule(self):
        groups=self.config["groups"]
        if not groups: return messagebox.showerror("Schedule", "Create a group first.")
        base="schedule"; ids={s["id"] for s in self.config["schedules"]}; n=1
        while f"{base}_{n}" in ids: n+=1
        self.config["schedules"].append({"id":f"schedule_{n}","name":f"New schedule {n}","group_id":groups[0]["id"],"days":["MON","TUE","WED","THU","FRI"],"start_time":"09:00","end_time":"10:00","period_minutes":60,"breaks":[],"activities":[]})
        self._populate_schedules(); self.schedule_list.selection_set(tk.END); self._load_schedule_fields(); self._populate_activities(); self.tabs.select(1); self._status("New schedule added; edit fields and apply.")
    def apply_schedule(self):
        s=self._schedule()
        if not s: return messagebox.showinfo("Schedule", "Select a schedule first.")
        try:
            v={k:x.get().strip() for k,x in self.schedule_vars.items()}
            s.update({"id":v["id"],"name":v["name"],"group_id":v["group_id"],"days":[d.strip().upper() for d in v["days"].split(",") if d.strip()],"start_time":v["start_time"],"end_time":v["end_time"],"period_minutes":int(v["period_minutes"])})
            self._populate_schedules(); self._status(f"Updated schedule {s['id']} (not saved yet).")
        except Exception as e: messagebox.showerror("Schedule", str(e))
    def apply_activity(self):
        selected=self._selected_activity()
        if not selected: return messagebox.showinfo("Activity", "Select an activity first.")
        idx,a=selected
        try:
            v={k:x.get().strip() for k,x in self.act_vars.items()}
            a.update({"day":v["day"].upper(),"period":int(v["period"]),"name":v["name"],"start_time":v["start_time"],"end_time":v["end_time"]})
            if v["priority"]: a["priority"]=v["priority"].upper()
            else: a.pop("priority",None)
            self._populate_activities(); self.tree.selection_set(str(idx)); self._status("Activity updated (not saved yet).")
        except Exception as e: messagebox.showerror("Activity", str(e))
    def add_activity(self):
        s=self._schedule()
        if not s: return messagebox.showinfo("Activity", "Select a schedule first.")
        s["activities"].append({"id":f"activity_{len(s.get('activities',[]))+1}","day":s.get("days",["MON"])[0],"period":1,"name":"New activity","start_time":s["start_time"],"end_time":s["end_time"]})
        self._populate_activities(); self.tree.selection_set(str(len(s["activities"])-1)); self._load_activity_fields(); self._status("Activity added; edit its fields.")
    def delete_activity(self):
        selected=self._selected_activity(); s=self._schedule()
        if not selected or not s: return messagebox.showinfo("Activity", "Select an activity first.")
        idx,a=selected
        if messagebox.askyesno("Delete activity", f"Delete '{a.get('name')}'?"):
            del s["activities"][idx]; self._populate_activities(); self._status("Activity deleted (not saved yet).")
    def open_config(self):
        name=filedialog.askopenfilename(title="Open group/schedule configuration",filetypes=[("JSON","*.json"),("All files","*.*")])
        if name: self.path_var.set(name); self.load()
    def load(self):
        try:
            self.config_path=Path(self.path_var.get().strip()); self.config=load_config(self.config_path)
            self._populate_groups(); self._populate_schedules(); self._populate_activities(); self._load_group_fields(); self._load_schedule_fields(); self._status(f"Loaded {self.config_path}")
        except Exception as e: messagebox.showerror("Load failed",str(e))
    def save(self):
        try:
            self.config_path=Path(self.path_var.get().strip()); save_config(self.config,self.config_path); self._status(f"Saved {self.config_path}")
        except Exception as e: messagebox.showerror("Save failed",str(e))
    def export(self):
        try:
            from tkinter import filedialog
            path=filedialog.asksaveasfilename(title="Export / update calendar state",initialfile="calendar.json",defaultextension=".json",filetypes=[("Calendar JSON","*.json")])
            if not path: return
            count=export_to_calendar(self.config,path)
            self._status(f"Exported {count} timetable rules to {path}")
            messagebox.showinfo("Export complete",f"Exported {count} timetable rules. Existing non-timetable rules were retained.\n\nNote: override_policy is stored in the group configuration; the existing calendar engine does not yet enforce it as a first-class field.")
        except Exception as e: messagebox.showerror("Export failed",str(e))
    def reset(self):
        if messagebox.askyesno("Reset editor", "Discard current unsaved edits and reload the example school timetable?"):
            self.config=default_config(); self._populate_groups(); self._populate_schedules(); self._populate_activities(); self._load_group_fields(); self._load_schedule_fields(); self._status("Reset to built-in example; save if you want to keep it.")

def main():
    root=tk.Tk(); GroupScheduleEditor(root); root.mainloop()

if __name__ == "__main__": main()
