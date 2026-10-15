
"""Small desktop GUI for the cal scheduling engine."""
from __future__ import annotations

import shlex
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from datetime import date

from .change_log import log_change
from .models import ActivityRule
from .operations import add_activity, remove_activity
from .parser import parse_command
from .storage import CalendarState


class CalendarGUI:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("cal - Calendar")
        self.root.geometry("820x580")
        self.state = CalendarState()
        self.state_path = Path("calendar.json")

        top = ttk.Frame(root, padding=10)
        top.pack(fill="x")

        ttk.Label(top, text="Calendar file:").pack(side="left")

        self.path_var = tk.StringVar(value=str(self.state_path))
        ttk.Entry(top, textvariable=self.path_var, width=48).pack(
            side="left", padx=6
        )
        ttk.Button(top, text="Browse", command=self.browse).pack(side="left")
        ttk.Button(top, text="Load", command=self.load).pack(
            side="left", padx=4
        )

        body = ttk.PanedWindow(root, orient="horizontal")
        body.pack(fill="both", expand=True, padx=10, pady=5)

        left = ttk.Frame(body, padding=5)
        right = ttk.Frame(body, padding=5)
        body.add(left, weight=1)
        body.add(right, weight=2)

        ttk.Label(left, text="Activities").pack(anchor="w")

        self.activity_list = tk.Listbox(left, height=18)
        self.activity_list.pack(fill="both", expand=True, pady=5)

        ttk.Button(
            left, text="Remove selected", command=self.remove_selected
        ).pack(fill="x", pady=2)

        ttk.Button(
            left, text="Reset calendar", command=self.reset
        ).pack(fill="x", pady=2)

        ttk.Label(right, text="Today's schedule").pack(anchor="w")

        self.schedule_text = tk.Text(
            right, wrap="word", state="disabled", height=18
        )
        self.schedule_text.pack(fill="both", expand=True, pady=5)

        add_box = ttk.LabelFrame(root, text="Add activity", padding=10)
        add_box.pack(fill="x", padx=10, pady=8)

        self.name_var = tk.StringVar()
        self.time_var = tk.StringVar(value="09:00-10:00")
        self.days_var = tk.StringVar(value="mon,tue,wed,thu,fri")

        fields = [
            ("Name / ID", self.name_var, 18),
            ("Time", self.time_var, 14),
            ("Days (or daily)", self.days_var, 24),
        ]

        for label, variable, width in fields:
            group = ttk.Frame(add_box)
            group.pack(side="left", padx=5)
            ttk.Label(group, text=label).pack(anchor="w")
            ttk.Entry(
                group, textvariable=variable, width=width
            ).pack()

        ttk.Button(
            add_box, text="Add activity", command=self.add
        ).pack(side="left", padx=10)

        self.status_var = tk.StringVar(value="Ready")
        ttk.Label(root, textvariable=self.status_var, padding=5).pack(
            fill="x"
        )

        self.load()

    def browse(self):
        filename = filedialog.askopenfilename(
            title="Open calendar",
            filetypes=[("Calendar JSON", "*.json"), ("All files", "*.*")],
        )
        if filename:
            self.path_var.set(filename)
            self.load()

    def _select_path(self) -> bool:
        raw = self.path_var.get().strip()
        if not raw:
            messagebox.showerror("Calendar", "Enter a calendar file path.")
            return False

        self.state_path = Path(raw)
        return True

    def load(self):
        if not self._select_path():
            return

        try:
            if self.state_path.exists():
                self.state = CalendarState.load(self.state_path)
            else:
                self.state = CalendarState()
            self.refresh()
            self.status_var.set(f"Loaded {self.state_path}")
        except Exception as error:
            messagebox.showerror("Load failed", str(error))

    def save(self):
        self.state.save(self.state_path)

    def _log(self, action: str, activity_ids=(), details: str = ""):
        log_change(
            action,
            activities=activity_ids,
            details=details,
            log_path=self.state_path.with_name("cal_changes.jsonl"),
        )

    def refresh(self):
        self.activity_list.delete(0, tk.END)

        for rule in self.state.rules:
            self.activity_list.insert(tk.END, f"{rule.name} [{rule.id}]")

        today = date.today()
        schedule = self.state.resolver().build_actual_schedule(today, today)

        lines = [today.strftime("%A, %Y-%m-%d"), ""]

        if not schedule:
            lines.append("No scheduled activities today.")
        else:
            for item in schedule:
                lines.append(
                    f"{item.start:%H:%M}-{item.end:%H:%M}  {item.name}"
                )

        self.schedule_text.configure(state="normal")
        self.schedule_text.delete("1.0", tk.END)
        self.schedule_text.insert("1.0", "\n".join(lines))
        self.schedule_text.configure(state="disabled")

    def add(self):
        name = self.name_var.get().strip()
        time_range = self.time_var.get().strip()
        days = self.days_var.get().strip().lower()

        if not name:
            messagebox.showerror("Add activity", "Enter a name / ID.")
            return

        try:
            # shlex.quote preserves names containing spaces.
            command = (
                f"cal --fixed {shlex.quote(name)} "
                f"--time {shlex.quote(time_range)} "
            )

            if days == "daily":
                command += "--daily"
            else:
                day_tokens = [
                    item.strip()
                    for item in days.split(",")
                    if item.strip()
                ]
                if not day_tokens:
                    raise ValueError(
                        "Enter 'daily' or weekday codes such as mon,wed,fri."
                    )
                command += "--days " + " ".join(
                    shlex.quote(day) for day in day_tokens
                )

            rule = parse_command(command, date.today())

            result = add_activity(self.state, rule, date.today())

            if result.status == "REQUIRES_AI":
                messagebox.showwarning(
                    "Not added",
                    "The activity conflicts with existing events and "
                    "needs further resolution.",
                )
                return

            self.state = result.state
            self.save()
            self._log("ADD", [rule.id], f"status={result.status}")
            self.refresh()
            self.status_var.set(f"Added {rule.name}")

        except Exception as error:
            messagebox.showerror("Add failed", str(error))

    def remove_selected(self):
        selection = self.activity_list.curselection()
        if not selection:
            messagebox.showinfo("Remove activity", "Select an activity first.")
            return

        rule = self.state.rules[selection[0]]

        if not messagebox.askyesno(
            "Remove activity",
            f"Remove '{rule.name}' and its exceptions?",
        ):
            return

        try:
            result = remove_activity(self.state, rule.id)
            self.state = result.state
            self.save()
            self._log("REMOVE", [rule.id])
            self.refresh()
            self.status_var.set(f"Removed {rule.name}")
        except Exception as error:
            messagebox.showerror("Remove failed", str(error))

    def reset(self):
        if not messagebox.askyesno(
            "Reset calendar",
            "Delete all activities and exceptions from this calendar?\n\n"
            "This will overwrite the selected calendar file.",
            icon="warning",
        ):
            return

        try:
            removed = [rule.id for rule in self.state.rules]
            self.state = CalendarState()
            self.save()
            self._log(
                "RESET",
                removed,
                f"Removed {len(removed)} activities.",
            )
            self.refresh()
            self.status_var.set("Calendar reset.")
        except Exception as error:
            messagebox.showerror("Reset failed", str(error))


def main():
    root = tk.Tk()
    CalendarGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
