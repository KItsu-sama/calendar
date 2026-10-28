# cal_engine/unified_gui.py
"""Desktop calendar: a ruled week/month grid for events and group timetables.

Design notes
- The grid is drawn like an exercise book page: pale ruled paper, a red margin
  line at the hour gutter, and the same line marking "now".
- Everything is edited in place. Drag on the grid to block out time, click a
  block to edit it, click a timetable on the left to change its rules.
- Every change is saved immediately. Ctrl+Z undoes it, so nothing needs a
  confirmation dialog.
"""
from __future__ import annotations

import calendar as month_calendar
import copy
import tkinter as tk
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from tkinter import filedialog, ttk
from tkinter import font as tkfont
from typing import Any

from .group_schedule import export_to_calendar, load_config, save_config
from .models import (
    ActivityRule,
    Flexibility,
    Priority,
    Recurrence,
    RecurrenceType,
    TimeConstraints,
    TimeWindow,
)
from .storage import CalendarState

# ── Palette ────────────────────────────────────────────────────────────────
PAPER = "#FAFBFE"        # grid paper
PANEL = "#EEF2F9"        # side panels and toolbar
WHITE = "#FFFFFF"
INK = "#1C2744"
INK_SOFT = "#5B6786"
INK_FAINT = "#8D98B3"
RULE = "#E0E7F3"         # hour lines
RULE_STRONG = "#C6D1E6"  # borders, half-emphasis
MARGIN = "#D4505C"       # the red margin line and the "now" line
TODAY_TINT = "#F1F5FD"
BREAK_FILL = "#CBD3E3"
ERROR = "#B3333F"

# priority -> (soft fill, strong edge)
PRIORITY = {
    "CRITICAL": ("#F7D6D9", "#C8454F"),
    "HIGH": ("#FBEAB9", "#A87400"),
    "NORMAL": ("#D9E5F9", "#4F7CC4"),
    "LOW": ("#DDEBE2", "#5F9275"),
}
PRIORITY_LABELS = [("CRITICAL", "Critical"), ("HIGH", "High"), ("NORMAL", "Normal"), ("LOW", "Low")]

DAYS = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]
DAY_SHORT = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
DAY_LONG = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
GROUP_PREFIX = "group_schedule__"
SNAP = 15


# ── Small helpers ──────────────────────────────────────────────────────────
def _minutes(value: str) -> int:
    hour, minute = map(int, value.split(":"))
    return hour * 60 + minute


def _clock(value: int) -> str:
    return f"{value // 60:02d}:{value % 60:02d}"


def parse_time(text: str) -> int:
    """Accept 9, 930, 9:30, 09:30, 9.30 and return minutes after midnight."""
    raw = text.strip().replace(".", ":").replace("h", ":")
    try:
        if ":" in raw:
            hour_s, minute_s = raw.split(":", 1)
            hour, minute = int(hour_s), int(minute_s or 0)
        elif raw.isdigit():
            hour, minute = (int(raw), 0) if len(raw) <= 2 else (int(raw[:-2]), int(raw[-2:]))
        else:
            raise ValueError
    except ValueError:
        raise ValueError(f"“{text}” isn’t a time. Use a form like 9:30 or 13:00.") from None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"“{text}” isn’t a valid time of day.")
    return hour * 60 + minute


def parse_date(text: str) -> date:
    raw = text.strip()
    try:
        return date.fromisoformat(raw)
    except ValueError:
        pass
    parts = raw.replace("-", "/").replace(".", "/").split("/")
    try:
        if len(parts) == 3:
            day, month, year = (int(p) for p in parts)
            return date(year, month, day)
    except ValueError:
        pass
    raise ValueError(f"“{text}” isn’t a date. Use 2026-10-28 or 28/10/2026.")


def week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())


def slot_layout(schedule: dict[str, Any]) -> list[dict[str, Any]]:
    """The ordered slots and breaks that make up one day of a timetable."""
    breaks = sorted(schedule.get("breaks", []), key=lambda b: _minutes(b["start_time"]))
    cursor = _minutes(schedule["start_time"])
    finish = _minutes(schedule["end_time"])
    length = max(1, int(schedule.get("period_minutes", 45)))
    period, out = 1, []
    for _ in range(500):
        if cursor >= finish:
            break
        brk = next((b for b in breaks if _minutes(b["start_time"]) == cursor), None)
        if brk:
            end = min(_minutes(brk["end_time"]), finish)
            out.append({"kind": "break", "start": cursor, "end": end, "name": brk.get("name", "Break")})
            cursor = max(end, cursor + 1)
            continue
        upcoming = [_minutes(b["start_time"]) for b in breaks if _minutes(b["start_time"]) > cursor]
        end = min(cursor + length, min(upcoming, default=finish), finish)
        if end <= cursor:
            break
        out.append({"kind": "slot", "period": period, "start": cursor, "end": end})
        cursor, period = end, period + 1
    return out


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
                "end_time": "09:00", "priority": "HIGH", "override_policy": "never",
            }],
            "activities": [],
        }],
    }


@dataclass
class Block:
    kind: str  # custom | slot | ghost | break
    start: datetime
    end: datetime
    name: str
    priority: str = "NORMAL"
    rule: Any = None
    sched: dict | None = None
    act: dict | None = None
    period: int = 0

    @property
    def key(self) -> tuple:
        return (self.kind, self.start.isoformat(), self.name)

    @property
    def start_min(self) -> int:
        return self.start.hour * 60 + self.start.minute

    @property
    def end_min(self) -> int:
        return 1440 if self.end.date() > self.start.date() else self.end.hour * 60 + self.end.minute


class Chips(tk.Frame):
    """A row of colour-coded radio chips (used for priority)."""

    def __init__(self, master, options, variable, font, colors=None):
        super().__init__(master, bg=PANEL)
        self.variable = variable
        self.colors = colors or PRIORITY
        self.chips: dict[str, tk.Label] = {}
        for value, label in options:
            chip = tk.Label(
                self, text=label, font=font, padx=10, pady=4, cursor="hand2",
                takefocus=True, highlightthickness=2, highlightbackground=PANEL, highlightcolor=INK,
            )
            chip.pack(side="left", padx=(0, 4))
            chip.bind("<Button-1>", lambda _e, v=value: variable.set(v))
            chip.bind("<space>", lambda _e, v=value: variable.set(v))
            chip.bind("<Return>", lambda _e, v=value: variable.set(v))
            self.chips[value] = chip
        variable.trace_add("write", lambda *_: self._paint())
        self._paint()

    def _paint(self):
        current = self.variable.get()
        for value, chip in self.chips.items():
            fill, edge = self.colors.get(value, (WHITE, INK_SOFT))
            if value == current:
                chip.configure(bg=edge, fg=WHITE)
            else:
                chip.configure(bg=fill, fg=INK)


# ── Application ────────────────────────────────────────────────────────────
class UnifiedCalendarGUI:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("cal")
        root.geometry("1280x820")
        root.minsize(1020, 640)
        self.k = max(1.0, root.winfo_fpixels("1i") / 96.0)

        self.calendar_path = Path("calendar.json")
        self.config_path = Path("groups_schedules.json")
        self.state = CalendarState()
        self.config: dict[str, Any] = {"version": 1, "groups": [], "schedules": []}

        self.view = tk.StringVar(value="Week")
        self.show_free = tk.BooleanVar(value=True)
        self.status_var = tk.StringVar(value="")
        self.title_var = tk.StringVar()
        self.anchor = date.today()
        self.mini_month = self.anchor.replace(day=1)

        self.blocks: list[Block] = []
        self.hits: list[tuple] = []
        self.mini_hits: list[tuple] = []
        self.sel_key: tuple | None = None
        self.draft: tuple[date, int, int] | None = None
        self.edit_rule_id: str | None = None
        self.slot_ctx: dict[str, Any] | None = None
        self.group_id: str | None = None
        self.hour_range = (6, 22)
        self._undo: list[tuple] = []
        self._press: tuple[int, int] | None = None
        self._dragged = False
        self._redraw_job = None

        self._make_fonts()
        self._make_styles()
        self._build()
        self._bind_keys()
        self.view.trace_add("write", lambda *_: self._on_view_change())
        self.show_free.trace_add("write", lambda *_: self._redraw())
        self._load_data()
        self._tick()

    # ── setup ──────────────────────────────────────────────────────────────
    def S(self, value: float) -> int:
        return int(round(value * self.k))

    def _make_fonts(self):
        families = set(tkfont.families(self.root))

        def pick(*names):
            return next((n for n in names if n in families), tkfont.nametofont("TkDefaultFont").actual("family"))

        ui = pick("Segoe UI", "Noto Sans", "Helvetica Neue", "DejaVu Sans")
        head = pick("Bahnschrift", "Segoe UI Semibold", "Segoe UI", "Noto Sans", "DejaVu Sans")
        self.f_ui = tkfont.Font(family=ui, size=10)
        self.f_small = tkfont.Font(family=ui, size=9)
        self.f_bold = tkfont.Font(family=ui, size=10, weight="bold")
        self.f_block = tkfont.Font(family=ui, size=9, weight="bold")
        self.f_hour = tkfont.Font(family=ui, size=8)
        self.f_title = tkfont.Font(family=head, size=17, weight="bold")
        self.f_head = tkfont.Font(family=head, size=12, weight="bold")
        self.f_num = tkfont.Font(family=head, size=15, weight="bold")
        self.root.option_add("*TCombobox*Listbox.font", self.f_ui)

    def _make_styles(self):
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(".", background=PANEL, foreground=INK, font=self.f_ui, bordercolor=RULE_STRONG, focuscolor=INK)
        style.configure("TFrame", background=PANEL)
        style.configure("TLabel", background=PANEL, foreground=INK)
        style.configure("TEntry", fieldbackground=WHITE, padding=5, bordercolor=RULE_STRONG, lightcolor=WHITE, darkcolor=WHITE)
        style.map("TEntry", bordercolor=[("focus", INK)])
        style.configure("TCombobox", fieldbackground=WHITE, background=WHITE, padding=5, arrowcolor=INK_SOFT,
                        bordercolor=RULE_STRONG, lightcolor=WHITE, darkcolor=WHITE)
        style.map("TCombobox", bordercolor=[("focus", INK)], fieldbackground=[("readonly", WHITE)],
                  selectbackground=[("readonly", WHITE)], selectforeground=[("readonly", INK)])
        style.configure("TButton", padding=(12, 6), background=WHITE, bordercolor=RULE_STRONG, relief="flat")
        style.map("TButton", background=[("active", "#E3EAF6")])
        style.configure("Accent.TButton", background=INK, foreground=WHITE, bordercolor=INK)
        style.map("Accent.TButton", background=[("active", "#2D3D69")])
        style.configure("Quiet.TButton", background=PANEL, bordercolor=PANEL, foreground=INK_SOFT)
        style.map("Quiet.TButton", background=[("active", "#E1E8F4")], foreground=[("active", INK)])
        style.configure("Nav.TButton", padding=(9, 5), background=WHITE, bordercolor=RULE_STRONG)
        style.configure("Seg.Toolbutton", padding=(14, 5), background=WHITE, foreground=INK_SOFT, bordercolor=RULE_STRONG)
        style.map("Seg.Toolbutton", background=[("selected", INK), ("active", "#E3EAF6")],
                  foreground=[("selected", WHITE)])
        style.configure("Day.Toolbutton", padding=(6, 4), background=WHITE, foreground=INK_SOFT, bordercolor=RULE_STRONG)
        style.map("Day.Toolbutton", background=[("selected", INK), ("active", "#E3EAF6")],
                  foreground=[("selected", WHITE)])
        style.configure("TCheckbutton", background=PANEL, foreground=INK)
        style.configure("Vertical.TScrollbar", background=RULE_STRONG, troughcolor=PAPER, bordercolor=PAPER,
                        arrowcolor=INK_SOFT)

    # ── layout ─────────────────────────────────────────────────────────────
    def _build(self):
        root = self.root
        root.configure(bg=PANEL)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(2, weight=1)

        # toolbar
        bar = tk.Frame(root, bg=PANEL, padx=self.S(14), pady=self.S(10))
        bar.grid(row=0, column=0, sticky="ew")
        ttk.Button(bar, text="‹", style="Nav.TButton", width=2, command=lambda: self._step(-1)).pack(side="left")
        ttk.Button(bar, text="›", style="Nav.TButton", width=2, command=lambda: self._step(1)).pack(side="left", padx=(4, 8))
        ttk.Button(bar, text="Today", style="Nav.TButton", command=self._today).pack(side="left")
        tk.Label(bar, textvariable=self.title_var, font=self.f_title, bg=PANEL, fg=INK).pack(side="left", padx=self.S(18))

        files = ttk.Menubutton(bar, text="Files")
        menu = tk.Menu(files, tearoff=False, font=self.f_ui)
        menu.add_command(label="Open calendar…", command=self._browse_calendar)
        menu.add_command(label="Open group rules…", command=self._browse_config)
        menu.add_command(label="Reload from disk", command=self._load_data)
        menu.add_separator()
        menu.add_command(label="Undo last change    Ctrl+Z", command=self._undo_last)
        files["menu"] = menu
        files.pack(side="right")
        ttk.Button(bar, text="New event", style="Accent.TButton", command=self._new_event).pack(side="right", padx=10)
        seg = tk.Frame(bar, bg=PANEL)
        seg.pack(side="right", padx=10)
        for label in ("Week", "Month"):
            ttk.Radiobutton(seg, text=label, value=label, variable=self.view, style="Seg.Toolbutton").pack(side="left")

        tk.Frame(root, bg=RULE_STRONG, height=1).grid(row=1, column=0, sticky="ew")

        # three columns: sidebar | grid | inspector
        main = tk.Frame(root, bg=PANEL)
        main.grid(row=2, column=0, sticky="nsew")
        main.rowconfigure(0, weight=1)
        main.columnconfigure(2, weight=1)
        self.sidebar = tk.Frame(main, bg=PANEL, width=self.S(232))
        self.sidebar.grid(row=0, column=0, sticky="ns")
        self.sidebar.pack_propagate(False)
        tk.Frame(main, bg=RULE_STRONG, width=1).grid(row=0, column=1, sticky="ns")
        center = tk.Frame(main, bg=PAPER)
        center.grid(row=0, column=2, sticky="nsew")
        tk.Frame(main, bg=RULE_STRONG, width=1).grid(row=0, column=3, sticky="ns")
        self.inspector = tk.Frame(main, bg=PANEL, width=self.S(320))
        self.inspector.grid(row=0, column=4, sticky="ns")
        self.inspector.pack_propagate(False)

        self._build_sidebar()
        self._build_grid(center)
        self._build_inspector()

        tk.Frame(root, bg=RULE_STRONG, height=1).grid(row=3, column=0, sticky="ew")
        foot = tk.Frame(root, bg=PANEL, padx=self.S(14), pady=self.S(5))
        foot.grid(row=4, column=0, sticky="ew")
        tk.Label(foot, textvariable=self.status_var, font=self.f_small, bg=PANEL, fg=INK_SOFT).pack(side="left")
        self.files_var = tk.StringVar()
        tk.Label(foot, textvariable=self.files_var, font=self.f_small, bg=PANEL, fg=INK_FAINT).pack(side="right")

    def _build_sidebar(self):
        pad = self.S(14)
        self.mini = tk.Canvas(self.sidebar, bg=PANEL, highlightthickness=0, width=self.S(204), height=self.S(196))
        self.mini.pack(padx=pad, pady=(pad, 6), anchor="w")
        self.mini.bind("<Button-1>", self._mini_click)

        tk.Frame(self.sidebar, bg=RULE_STRONG, height=1).pack(fill="x", padx=pad, pady=8)
        head = tk.Frame(self.sidebar, bg=PANEL)
        head.pack(fill="x", padx=pad)
        tk.Label(head, text="Timetables", font=self.f_bold, bg=PANEL, fg=INK).pack(side="left")
        add = ttk.Menubutton(head, text="Add", style="Quiet.TButton")
        add_menu = tk.Menu(add, tearoff=False, font=self.f_ui)
        add_menu.add_command(label="School template", command=self._add_school_template)
        add_menu.add_command(label="Blank timetable", command=self._add_blank_group)
        add["menu"] = add_menu
        add.pack(side="right")
        self.group_rows = tk.Frame(self.sidebar, bg=PANEL)
        self.group_rows.pack(fill="x", padx=pad, pady=(4, 0))
        ttk.Checkbutton(self.sidebar, text="Show free slots on the grid", variable=self.show_free).pack(
            anchor="w", padx=pad, pady=(8, 0))

        tk.Frame(self.sidebar, bg=RULE_STRONG, height=1).pack(fill="x", padx=pad, pady=12)
        legend = tk.Canvas(self.sidebar, bg=PANEL, highlightthickness=0, width=self.S(204), height=self.S(150))
        legend.pack(padx=pad, anchor="w")
        y = self.S(4)
        for value, label in PRIORITY_LABELS:
            fill, edge = PRIORITY[value]
            legend.create_rectangle(0, y, self.S(18), y + self.S(14), fill=fill, outline="")
            legend.create_rectangle(0, y, self.S(4), y + self.S(14), fill=edge, outline="")
            legend.create_text(self.S(28), y + self.S(7), text=label, anchor="w", font=self.f_small, fill=INK_SOFT)
            y += self.S(22)
        legend.create_rectangle(0, y, self.S(18), y + self.S(14), fill=BREAK_FILL, stipple="gray50", outline="")
        legend.create_text(self.S(28), y + self.S(7), text="Break", anchor="w", font=self.f_small, fill=INK_SOFT)
        y += self.S(22)
        legend.create_rectangle(0, y, self.S(18), y + self.S(14), fill=PAPER, outline=RULE_STRONG, dash=(3, 2))
        legend.create_text(self.S(28), y + self.S(7), text="Free timetable slot", anchor="w", font=self.f_small, fill=INK_SOFT)

    def _build_grid(self, parent):
        parent.rowconfigure(1, weight=1)
        parent.columnconfigure(0, weight=1)
        self.head = tk.Canvas(parent, bg=PAPER, highlightthickness=0, height=self.S(66))
        self.head.grid(row=0, column=0, sticky="ew")
        self.body = tk.Canvas(parent, bg=PAPER, highlightthickness=0)
        self.body.grid(row=1, column=0, sticky="nsew")
        sb = ttk.Scrollbar(parent, orient="vertical", command=self.body.yview)
        sb.grid(row=1, column=1, sticky="ns")
        tk.Frame(parent, bg=PAPER, width=sb.winfo_reqwidth()).grid(row=0, column=1)
        self.body.configure(yscrollcommand=sb.set)
        tk.Frame(parent, bg=RULE_STRONG, height=1).grid(row=0, column=0, sticky="sew")
        self.body.bind("<Configure>", lambda _e: self._schedule_redraw())
        self.body.bind("<ButtonPress-1>", self._on_press)
        self.body.bind("<B1-Motion>", self._on_drag)
        self.body.bind("<ButtonRelease-1>", self._on_release)
        self.body.bind("<Double-Button-1>", self._on_double)
        self.body.bind("<MouseWheel>", lambda e: self.body.yview_scroll(-1 if e.delta > 0 else 1, "units"))
        self.body.bind("<Button-4>", lambda _e: self.body.yview_scroll(-1, "units"))
        self.body.bind("<Button-5>", lambda _e: self.body.yview_scroll(1, "units"))

    # ── inspector panels ───────────────────────────────────────────────────
    def _label(self, parent, text):
        return tk.Label(parent, text=text, font=self.f_small, bg=PANEL, fg=INK_SOFT, anchor="w")

    def _field(self, parent, label, var, values=None, side=None):
        box = tk.Frame(parent, bg=PANEL)
        self._label(box, label).pack(anchor="w")
        if values:
            widget = ttk.Combobox(box, textvariable=var, values=values, state="readonly", font=self.f_ui)
        else:
            widget = ttk.Entry(box, textvariable=var, font=self.f_ui)
        widget.pack(fill="x", pady=(2, 0))
        if side:
            box.pack(side="left", fill="x", expand=True, padx=(0, 8) if side == "first" else 0)
        else:
            box.pack(fill="x", pady=(0, 10))
        return widget

    def _pair(self, parent):
        row = tk.Frame(parent, bg=PANEL)
        row.pack(fill="x", pady=(0, 10))
        return row

    def _build_inspector(self):
        pad = self.S(16)
        self.panels: dict[str, tk.Frame] = {}
        for name in ("empty", "event", "slot", "group"):
            self.panels[name] = tk.Frame(self.inspector, bg=PANEL, padx=pad, pady=pad)

        # empty
        p = self.panels["empty"]
        tk.Label(p, text="Nothing selected", font=self.f_head, bg=PANEL, fg=INK, anchor="w").pack(fill="x")
        tk.Label(
            p, justify="left", anchor="w", wraplength=self.S(280), font=self.f_ui, bg=PANEL, fg=INK_SOFT,
            text=("Drag across the grid to block out time.\nClick an event or a timetable slot to edit it.\n\n"
                  "N  new event\n←  →  previous / next\nT  today\nW  M  week / month\nCtrl+Z  undo"),
        ).pack(fill="x", pady=(8, 0))

        # event
        p = self.panels["event"]
        self.ev_title = tk.StringVar(value="New event")
        self.ev_name, self.ev_date = tk.StringVar(), tk.StringVar()
        self.ev_start, self.ev_end = tk.StringVar(), tk.StringVar()
        self.ev_repeat, self.ev_priority = tk.StringVar(value="Once"), tk.StringVar(value="NORMAL")
        self.ev_hint, self.ev_error = tk.StringVar(), tk.StringVar()
        tk.Label(p, textvariable=self.ev_title, font=self.f_head, bg=PANEL, fg=INK, anchor="w").pack(fill="x", pady=(0, 10))
        self.ev_name_entry = self._field(p, "Name", self.ev_name)
        self._field(p, "Date", self.ev_date)
        row = self._pair(p)
        self._field(row, "Starts", self.ev_start, side="first")
        self._field(row, "Ends", self.ev_end, side="last")
        self._field(p, "Repeats", self.ev_repeat, values=["Once", "Every week", "Every day"])
        tk.Label(p, textvariable=self.ev_hint, font=self.f_small, bg=PANEL, fg=INK_FAINT, anchor="w",
                 wraplength=self.S(280), justify="left").pack(fill="x", pady=(0, 10))
        self._label(p, "Priority").pack(anchor="w")
        Chips(p, PRIORITY_LABELS, self.ev_priority, self.f_small).pack(anchor="w", pady=(3, 14))
        tk.Label(p, textvariable=self.ev_error, font=self.f_small, bg=PANEL, fg=ERROR, anchor="w",
                 wraplength=self.S(280), justify="left").pack(fill="x", pady=(0, 6))
        btns = tk.Frame(p, bg=PANEL)
        btns.pack(fill="x")
        ttk.Button(btns, text="Save event", style="Accent.TButton", command=self._save_event).pack(side="left")
        self.ev_delete = ttk.Button(btns, text="Delete", command=self._delete_event)
        self.ev_delete.pack(side="left", padx=6)
        ttk.Button(btns, text="Close", style="Quiet.TButton", command=self._close_panel).pack(side="right")
        self.ev_repeat.trace_add("write", lambda *_: self._update_hint())
        self.ev_date.trace_add("write", lambda *_: self._update_hint())

        # timetable slot
        p = self.panels["slot"]
        self.sl_title, self.sl_sub = tk.StringVar(), tk.StringVar()
        self.sl_name, self.sl_start, self.sl_end = tk.StringVar(), tk.StringVar(), tk.StringVar()
        self.sl_priority, self.sl_error = tk.StringVar(value=""), tk.StringVar()
        tk.Label(p, textvariable=self.sl_title, font=self.f_head, bg=PANEL, fg=INK, anchor="w").pack(fill="x")
        tk.Label(p, textvariable=self.sl_sub, font=self.f_small, bg=PANEL, fg=INK_SOFT, anchor="w").pack(fill="x", pady=(0, 12))
        self.sl_name_entry = self._field(p, "Subject", self.sl_name)
        row = self._pair(p)
        self._field(row, "Starts", self.sl_start, side="first")
        self._field(row, "Ends", self.sl_end, side="last")
        self._label(p, "Priority").pack(anchor="w")
        colors = dict(PRIORITY)
        colors[""] = (WHITE, INK_SOFT)
        Chips(p, [("", "Inherit")] + PRIORITY_LABELS, self.sl_priority, self.f_small, colors).pack(anchor="w", pady=(3, 14))
        tk.Label(p, textvariable=self.sl_error, font=self.f_small, bg=PANEL, fg=ERROR, anchor="w",
                 wraplength=self.S(280), justify="left").pack(fill="x", pady=(0, 6))
        btns = tk.Frame(p, bg=PANEL)
        btns.pack(fill="x")
        ttk.Button(btns, text="Save slot", style="Accent.TButton", command=self._save_slot).pack(side="left")
        self.sl_clear = ttk.Button(btns, text="Clear subject", command=self._clear_slot)
        self.sl_clear.pack(side="left", padx=6)
        ttk.Button(btns, text="Close", style="Quiet.TButton", command=self._close_panel).pack(side="right")
        ttk.Button(p, text="Edit timetable rules", style="Quiet.TButton", command=self._open_group_from_slot).pack(
            anchor="w", pady=(10, 0))

        # timetable rules
        p = self.panels["group"]
        self.gp_title, self.gp_error = tk.StringVar(), tk.StringVar()
        self.gp_name, self.gp_start, self.gp_end = tk.StringVar(), tk.StringVar(), tk.StringVar()
        self.gp_period, self.gp_bname = tk.StringVar(), tk.StringVar()
        self.gp_bstart, self.gp_bend = tk.StringVar(), tk.StringVar()
        self.gp_days = {d: tk.BooleanVar() for d in DAYS}
        tk.Label(p, textvariable=self.gp_title, font=self.f_head, bg=PANEL, fg=INK, anchor="w").pack(fill="x", pady=(0, 10))
        self._field(p, "Name", self.gp_name)
        self._label(p, "Active days").pack(anchor="w")
        days = tk.Frame(p, bg=PANEL)
        days.pack(fill="x", pady=(3, 10))
        for i, d in enumerate(DAYS):
            ttk.Checkbutton(days, text=DAY_SHORT[i], variable=self.gp_days[d], style="Day.Toolbutton").pack(
                side="left", padx=(0, 3))
        row = self._pair(p)
        self._field(row, "Day starts", self.gp_start, side="first")
        self._field(row, "Day ends", self.gp_end, side="last")
        self._field(p, "Slot length (minutes)", self.gp_period)
        self._field(p, "Break name", self.gp_bname)
        row = self._pair(p)
        self._field(row, "Break starts", self.gp_bstart, side="first")
        self._field(row, "Break ends", self.gp_bend, side="last")
        tk.Label(p, textvariable=self.gp_error, font=self.f_small, bg=PANEL, fg=ERROR, anchor="w",
                 wraplength=self.S(280), justify="left").pack(fill="x", pady=(0, 6))
        btns = tk.Frame(p, bg=PANEL)
        btns.pack(fill="x")
        ttk.Button(btns, text="Save timetable", style="Accent.TButton", command=self._save_group).pack(side="left")
        ttk.Button(btns, text="Close", style="Quiet.TButton", command=self._close_panel).pack(side="right")

        self._show_panel("empty")

    def _show_panel(self, name: str):
        for panel in self.panels.values():
            panel.pack_forget()
        self.panels[name].pack(fill="both", expand=True)

    def _close_panel(self):
        self.sel_key = None
        self.draft = None
        self.edit_rule_id = None
        self.slot_ctx = None
        self.group_id = None
        self._show_panel("empty")
        self._redraw()

    def _update_hint(self):
        try:
            day = parse_date(self.ev_date.get())
        except ValueError:
            self.ev_hint.set("")
            return
        repeat = self.ev_repeat.get()
        self.ev_hint.set({
            "Once": f"Only on {DAY_LONG[day.weekday()]} {day.day} {month_calendar.month_abbr[day.month]}.",
            "Every week": f"Every {DAY_LONG[day.weekday()]}.",
            "Every day": "Every day.",
        }.get(repeat, ""))

    # ── keys ───────────────────────────────────────────────────────────────
    def _bind_keys(self):
        r = self.root
        r.bind("<Control-z>", lambda _e: self._undo_last())
        r.bind("<Control-n>", lambda _e: self._new_event())
        r.bind("<Escape>", lambda _e: self._close_panel())
        for key, fn in [("<Left>", lambda: self._step(-1)), ("<Right>", lambda: self._step(1)),
                        ("t", self._today), ("n", self._new_event),
                        ("w", lambda: self.view.set("Week")), ("m", lambda: self.view.set("Month")),
                        ("<Delete>", self._delete_selected)]:
            r.bind(key, lambda e, f=fn: None if self._typing(e) else f())

    def _typing(self, event) -> bool:
        return event.widget.winfo_class() in ("Entry", "TEntry", "TCombobox", "Text", "Listbox")

    # ── data ───────────────────────────────────────────────────────────────
    def _load_data(self):
        try:
            self.state = CalendarState.load(self.calendar_path) if self.calendar_path.exists() else CalendarState()
            self.config = (load_config(self.config_path) if self.config_path.exists()
                           else {"version": 1, "groups": [], "schedules": []})
        except Exception as exc:
            self.status_var.set(f"Couldn’t load: {exc}")
            return
        self._undo.clear()
        self.files_var.set(f"{self.calendar_path.name}  ·  {self.config_path.name}")
        self.status_var.set(f"Loaded {self.calendar_path.name} and {self.config_path.name}")
        self._refresh_sidebar()
        self._redraw()

    def _browse_calendar(self):
        name = filedialog.askopenfilename(title="Open calendar", filetypes=[("Calendar JSON", "*.json"), ("All files", "*.*")])
        if name:
            self.calendar_path = Path(name)
            self._load_data()

    def _browse_config(self):
        name = filedialog.askopenfilename(title="Open group rules", filetypes=[("JSON", "*.json"), ("All files", "*.*")])
        if name:
            self.config_path = Path(name)
            self._load_data()

    def _snapshot(self):
        self._undo.append((copy.deepcopy(self.state), copy.deepcopy(self.config)))
        del self._undo[:-30]

    def _saved(self, what: str):
        self.status_var.set(f"{what}  ·  saved {datetime.now():%H:%M}  ·  Ctrl+Z to undo")

    def _persist(self, calendar=True, rules=False):
        if calendar:
            self.state.save(self.calendar_path)
        if rules:
            save_config(self.config, self.config_path)

    def _undo_last(self):
        if not self._undo:
            self.status_var.set("Nothing to undo.")
            return
        self.state, self.config = self._undo.pop()
        try:
            self._persist(calendar=True, rules=True)
        except Exception as exc:
            self.status_var.set(f"Couldn’t save the undo: {exc}")
        self._close_panel()
        self._refresh_sidebar()
        self.status_var.set(f"Undone  ·  saved {datetime.now():%H:%M}")

    def _sync_timetables(self) -> int:
        """Write the group timetables into calendar.json and reload it."""
        count = export_to_calendar(self.config, self.calendar_path)
        self.state = CalendarState.load(self.calendar_path)
        return count

    # ── navigation ─────────────────────────────────────────────────────────
    def _range(self) -> tuple[date, date]:
        if self.view.get() == "Month":
            start = self.anchor.replace(day=1)
            return start, self.anchor.replace(day=month_calendar.monthrange(self.anchor.year, self.anchor.month)[1])
        start = week_start(self.anchor)
        return start, start + timedelta(days=6)

    def _set_anchor(self, day: date):
        self.anchor = day
        self.mini_month = day.replace(day=1)
        self._redraw()

    def _step(self, direction: int):
        if self.view.get() == "Month":
            month = self.anchor.month - 1 + direction
            year, month = self.anchor.year + month // 12, month % 12 + 1
            last = month_calendar.monthrange(year, month)[1]
            self._set_anchor(date(year, month, min(self.anchor.day, last)))
        else:
            self._set_anchor(self.anchor + timedelta(days=7 * direction))

    def _today(self):
        self._set_anchor(date.today())

    def _on_view_change(self):
        self.hits.clear()
        self._redraw()

    def _title_text(self) -> str:
        start, end = self._range()
        abbr = month_calendar.month_abbr
        if self.view.get() == "Month":
            return f"{month_calendar.month_name[start.month]} {start.year}"
        if start.year != end.year:
            return f"{start.day} {abbr[start.month]} {start.year} – {end.day} {abbr[end.month]} {end.year}"
        if start.month != end.month:
            return f"{start.day} {abbr[start.month]} – {end.day} {abbr[end.month]} {end.year}"
        return f"{start.day} – {end.day} {abbr[end.month]} {end.year}"

    # ── block collection ───────────────────────────────────────────────────
    def _rule_for(self, item) -> Any:
        rid = getattr(item, "rule_id", None)
        if rid:
            found = next((r for r in self.state.rules if r.id == rid), None)
            if found:
                return found
        return next((r for r in self.state.rules if item.name == r.name or item.name.startswith(r.name + " (")), None)

    def _group(self, group_id: str) -> dict | None:
        return next((g for g in self.config["groups"] if g["id"] == group_id), None)

    def _schedule_of(self, group_id: str) -> dict | None:
        return next((s for s in self.config["schedules"] if s["group_id"] == group_id), None)

    def _slot_for(self, item) -> tuple[dict | None, dict | None]:
        code, hhmm = DAYS[item.start.weekday()], item.start.strftime("%H:%M")
        for sched in self.config["schedules"]:
            for act in sched.get("activities", []):
                if act.get("day") == code and act.get("start_time") == hhmm:
                    return sched, act
        return None, None

    def _collect(self, start: date, end: date) -> list[Block]:
        blocks: list[Block] = []
        for item in self.state.resolver().build_actual_schedule(start, end):
            rule = self._rule_for(item)
            grouped = bool(rule and rule.id.startswith(GROUP_PREFIX))
            priority = rule.priority.value if rule else "NORMAL"
            block = Block("slot" if grouped else "custom", item.start, item.end, item.name, priority, rule)
            if grouped:
                block.sched, block.act = self._slot_for(item)
                if block.sched:
                    group = self._group(block.sched["group_id"]) or {}
                    block.priority = (block.act or {}).get("priority") or group.get("priority", priority)
                    block.period = int((block.act or {}).get("period", 0))
            blocks.append(block)

        taken = {(b.start, b.name) for b in blocks}
        day = start
        while day <= end:
            code = DAYS[day.weekday()]
            for sched in self.config["schedules"]:
                if code not in sched.get("days", []):
                    continue
                group = self._group(sched["group_id"]) or {}
                for entry in slot_layout(sched):
                    s = datetime.combine(day, datetime.min.time()) + timedelta(minutes=entry["start"])
                    e = datetime.combine(day, datetime.min.time()) + timedelta(minutes=entry["end"])
                    if entry["kind"] == "break":
                        if (s, entry["name"]) not in taken:
                            blocks.append(Block("break", s, e, entry["name"], "NORMAL", sched=sched))
                    elif self.show_free.get():
                        act = next((a for a in sched.get("activities", [])
                                    if a.get("day") == code and int(a.get("period", 0)) == entry["period"]), None)
                        if act is None:
                            blocks.append(Block("ghost", s, e, "Free slot", group.get("priority", "NORMAL"),
                                                sched=sched, period=entry["period"]))
            day += timedelta(days=1)
        return blocks

    # ── drawing ────────────────────────────────────────────────────────────
    def _schedule_redraw(self):
        if self._redraw_job:
            self.root.after_cancel(self._redraw_job)
        self._redraw_job = self.root.after(30, self._redraw)

    def _redraw(self):
        self._redraw_job = None
        self.title_var.set(self._title_text())
        start, end = self._range()
        if self.view.get() == "Month":
            first = week_start(start)
            last = week_start(end) + timedelta(days=6)
            self.blocks = self._collect(first, last)
        else:
            self.blocks = self._collect(start, end)
        self.hits = []
        if self.view.get() == "Month":
            self.head.configure(height=self.S(34))
            self._draw_month()
        else:
            self.head.configure(height=self.S(66))
            self._draw_week()
        self._draw_mini()

    def _fit(self, text: str, font: tkfont.Font, width: int) -> str:
        if width <= 0:
            return ""
        if font.measure(text) <= width:
            return text
        while text and font.measure(text + "…") > width:
            text = text[:-1]
        return text + "…" if text else ""

    def _lanes(self, blocks: list[Block]) -> list[tuple[Block, int, int]]:
        blocks = sorted(blocks, key=lambda b: (b.start, -(b.end - b.start).total_seconds()))
        out: list[tuple[Block, int, int]] = []
        cluster: list[tuple[Block, int]] = []
        lane_ends: list[datetime] = []
        cluster_end = None
        for block in blocks:
            if cluster_end is not None and block.start >= cluster_end:
                out += [(b, lane, len(lane_ends)) for b, lane in cluster]
                cluster, lane_ends, cluster_end = [], [], None
            for i, lane_end in enumerate(lane_ends):
                if block.start >= lane_end:
                    lane, lane_ends[i] = i, block.end
                    break
            else:
                lane = len(lane_ends)
                lane_ends.append(block.end)
            cluster.append((block, lane))
            cluster_end = max(cluster_end or block.end, block.end)
        out += [(b, lane, len(lane_ends)) for b, lane in cluster]
        return out

    def _draw_week(self):
        S = self.S
        c, h = self.body, self.head
        c.delete("all")
        h.delete("all")
        width = max(c.winfo_width(), 400)
        gutter = S(58)
        colw = (width - gutter) / 7
        start, _ = self._range()
        today = date.today()
        dates = [start + timedelta(days=i) for i in range(7)]

        lo = min([6] + [b.start_min // 60 for b in self.blocks])
        hi = max([22] + [(b.end_min + 59) // 60 for b in self.blocks])
        self.hour_range = (max(0, lo), min(24, hi))
        hour_h = S(52)
        top_pad = S(10)
        total = (self.hour_range[1] - self.hour_range[0]) * hour_h

        def y_of(minute: float) -> float:
            return top_pad + (minute - self.hour_range[0] * 60) / 60 * hour_h

        c.configure(scrollregion=(0, 0, width, total + top_pad * 2))

        # day tint + separators
        for i, d in enumerate(dates):
            x0 = gutter + i * colw
            if d == today:
                c.create_rectangle(x0, 0, x0 + colw, total + top_pad * 2, fill=TODAY_TINT, outline="")
            if i:
                c.create_line(x0, 0, x0, total + top_pad * 2, fill=RULE)
        # hour rules
        for hour in range(self.hour_range[0], self.hour_range[1] + 1):
            y = y_of(hour * 60)
            c.create_line(gutter, y, width, y, fill=RULE_STRONG if hour % 6 == 0 else RULE)
            if hour < self.hour_range[1]:
                c.create_line(gutter, y + hour_h / 2, width, y + hour_h / 2, fill=RULE, dash=(1, 4))
            c.create_text(gutter - S(10), y, text=f"{hour:02d}:00", anchor="e", font=self.f_hour, fill=INK_FAINT)
        # the margin
        c.create_line(gutter, 0, gutter, total + top_pad * 2, fill=MARGIN, width=2)

        # header
        h.create_line(gutter, 0, gutter, S(66), fill=MARGIN, width=2)
        for i, d in enumerate(dates):
            x0 = gutter + i * colw
            h.create_text(x0 + S(12), S(10), text=DAY_SHORT[i], anchor="nw", font=self.f_small,
                          fill=INK if d == today else INK_SOFT)
            size = S(30)
            if d == today:
                h.create_oval(x0 + S(8), S(26), x0 + S(8) + size, S(26) + size, fill=INK, outline="")
            h.create_text(x0 + S(8) + size / 2, S(26) + size / 2, text=str(d.day), font=self.f_num,
                          fill=WHITE if d == today else INK)
            if i:
                h.create_line(x0, S(8), x0, S(66), fill=RULE)

        # blocks
        by_day: dict[date, list[Block]] = {d: [] for d in dates}
        for b in self.blocks:
            if b.start.date() in by_day:
                by_day[b.start.date()].append(b)
        pad = S(3)
        for i, d in enumerate(dates):
            x0 = gutter + i * colw
            day_blocks = by_day[d]
            for b in [b for b in day_blocks if b.kind in ("break", "ghost")]:
                self._draw_block(c, b, x0 + pad, y_of(b.start_min), x0 + colw - pad, y_of(b.end_min))
            lanes = self._lanes([b for b in day_blocks if b.kind in ("custom", "slot")])
            for b, lane, count in lanes:
                inner = colw - 2 * pad
                bx0 = x0 + pad + inner * lane / count
                bx1 = x0 + pad + inner * (lane + 1) / count - (S(2) if count > 1 else 0)
                self._draw_block(c, b, bx0, y_of(b.start_min), bx1, max(y_of(b.end_min), y_of(b.start_min) + S(18)))

        # draft
        if self.draft:
            d, a, z = self.draft
            if d in by_day:
                i = dates.index(d)
                x0 = gutter + i * colw
                c.create_rectangle(x0 + pad, y_of(a), x0 + colw - pad, y_of(z), fill="#DDE6F6", stipple="gray50",
                                   outline=INK, dash=(4, 3), width=1)
                c.create_text(x0 + S(10), y_of(a) + S(4), anchor="nw", font=self.f_block, fill=INK,
                              text=f"{_clock(a)}–{_clock(z)}")

        # now
        if today in by_day:
            now = datetime.now()
            minute = now.hour * 60 + now.minute
            if self.hour_range[0] * 60 <= minute <= self.hour_range[1] * 60:
                i = dates.index(today)
                y = y_of(minute)
                c.create_line(gutter, y, gutter + (i + 1) * colw, y, fill=MARGIN, width=2)
                c.create_oval(gutter - S(5), y - S(5), gutter + S(5), y + S(5), fill=MARGIN, outline=PAPER, width=2)
        self._first_view_scroll(y_of, total + top_pad * 2)

    def _first_view_scroll(self, y_of, total):
        if getattr(self, "_scrolled", False):
            return
        self._scrolled = True
        now = datetime.now()
        target = max(self.hour_range[0] * 60, 6 * 60)
        if self.hour_range[0] * 60 <= now.hour * 60 <= self.hour_range[1] * 60:
            target = max(self.hour_range[0] * 60, now.hour * 60 - 60)
        self.body.yview_moveto(max(0.0, (y_of(target) - self.S(10)) / total))

    def _block_colors(self, b: Block) -> tuple[str, str]:
        return PRIORITY.get(b.priority, PRIORITY["NORMAL"])

    def _draw_block(self, c: tk.Canvas, b: Block, x0, y0, x1, y1):
        S = self.S
        fill, edge = self._block_colors(b)
        selected = self.sel_key == b.key
        if b.kind == "break":
            c.create_rectangle(x0, y0, x1, y1, fill=BREAK_FILL, stipple="gray25", outline="")
            if y1 - y0 > S(16):
                c.create_text(x0 + S(8), y0 + S(3), anchor="nw", text=self._fit(b.name, self.f_small, int(x1 - x0 - S(12))),
                              font=self.f_small, fill=INK_SOFT)
        elif b.kind == "ghost":
            c.create_rectangle(x0, y0, x1, y1, fill=PAPER, outline=INK if selected else RULE_STRONG,
                               dash=() if selected else (3, 3), width=2 if selected else 1)
            if y1 - y0 > S(18):
                c.create_text(x0 + S(8), y0 + S(3), anchor="nw", text=self._fit(f"Slot {b.period}", self.f_small, int(x1 - x0 - S(12))),
                              font=self.f_small, fill=INK_FAINT)
        else:
            body = WHITE if b.kind == "slot" else fill
            c.create_rectangle(x0, y0, x1, y1, fill=body, outline=INK if selected else (RULE_STRONG if b.kind == "slot" else fill),
                               width=2 if selected else 1)
            c.create_rectangle(x0, y0, x0 + S(4), y1, fill=edge, outline="")
            room = int(x1 - x0 - S(16))
            c.create_text(x0 + S(10), y0 + S(3), anchor="nw", text=self._fit(b.name, self.f_block, room),
                          font=self.f_block, fill=INK)
            if y1 - y0 >= S(38):
                c.create_text(x0 + S(10), y0 + S(19), anchor="nw", font=self.f_small, fill=INK_SOFT,
                              text=self._fit(f"{_clock(b.start_min)}–{_clock(b.end_min)}", self.f_small, room))
        self.hits.append((x0, y0, x1, y1, b))

    def _draw_month(self):
        S = self.S
        c, h = self.body, self.head
        c.delete("all")
        h.delete("all")
        width = max(c.winfo_width(), 400)
        height = max(c.winfo_height(), 300)
        c.configure(scrollregion=(0, 0, width, height))
        weeks = month_calendar.Calendar(0).monthdatescalendar(self.anchor.year, self.anchor.month)
        colw = width / 7
        rowh = height / len(weeks)
        today = date.today()
        for i, name in enumerate(DAY_SHORT):
            h.create_text(i * colw + S(12), S(17), text=name, anchor="w", font=self.f_small, fill=INK_SOFT)
            if i:
                h.create_line(i * colw, S(6), i * colw, S(34), fill=RULE)
        h.create_line(0, S(34) - 1, width, S(34) - 1, fill=RULE_STRONG)

        by_day: dict[date, list[Block]] = {}
        for b in self.blocks:
            if b.kind in ("custom", "slot"):
                by_day.setdefault(b.start.date(), []).append(b)

        for r, week in enumerate(weeks):
            for i, d in enumerate(week):
                x0, y0 = i * colw, r * rowh
                inside = d.month == self.anchor.month
                if d == today:
                    c.create_rectangle(x0, y0, x0 + colw, y0 + rowh, fill=TODAY_TINT, outline="")
                if d == self.anchor and d != today:
                    c.create_rectangle(x0 + 1, y0 + 1, x0 + colw - 1, y0 + rowh - 1, outline=INK, width=1)
                c.create_line(x0, y0 + rowh, x0 + colw, y0 + rowh, fill=RULE_STRONG)
                if i:
                    c.create_line(x0, y0, x0, y0 + rowh, fill=RULE)
                size = S(24)
                if d == today:
                    c.create_oval(x0 + S(6), y0 + S(5), x0 + S(6) + size, y0 + S(5) + size, fill=INK, outline="")
                c.create_text(x0 + S(6) + size / 2, y0 + S(5) + size / 2, text=str(d.day), font=self.f_bold,
                              fill=WHITE if d == today else (INK if inside else INK_FAINT))
                items = sorted(by_day.get(d, []), key=lambda b: b.start)
                chip_h = S(18)
                room = int((rowh - S(34)) // chip_h)
                shown = items if len(items) <= room else items[:max(room - 1, 0)]
                y = y0 + S(34)
                for b in shown:
                    fill, edge = self._block_colors(b)
                    selected = self.sel_key == b.key
                    c.create_rectangle(x0 + S(4), y, x0 + colw - S(4), y + chip_h - 2, fill=WHITE if b.kind == "slot" else fill,
                                       outline=INK if selected else "", width=2 if selected else 1)
                    c.create_rectangle(x0 + S(4), y, x0 + S(7), y + chip_h - 2, fill=edge, outline="")
                    label = self._fit(f"{b.start:%H:%M} {b.name}", self.f_small, int(colw - S(22)))
                    c.create_text(x0 + S(12), y + (chip_h - 2) / 2, text=label, anchor="w", font=self.f_small, fill=INK)
                    self.hits.append((x0 + S(4), y, x0 + colw - S(4), y + chip_h - 2, b))
                    y += chip_h
                if len(items) > len(shown):
                    c.create_text(x0 + S(10), y + S(2), anchor="nw", font=self.f_small, fill=INK_SOFT,
                                  text=f"+{len(items) - len(shown)} more")
                self.hits.append((x0, y0, x0 + colw, y0 + rowh, d))
        self.body.yview_moveto(0)
        self._scrolled = False

    def _draw_mini(self):
        S = self.S
        c = self.mini
        c.delete("all")
        self.mini_hits = []
        cell = S(204) / 7
        month = self.mini_month
        c.create_text(0, S(10), anchor="w", text=f"{month_calendar.month_name[month.month]} {month.year}", font=self.f_bold, fill=INK)
        for label, x, delta in (("‹", S(166), -1), ("›", S(196), 1)):
            c.create_text(x, S(10), text=label, font=self.f_head, fill=INK_SOFT)
            self.mini_hits.append((x - S(11), 0, x + S(11), S(22), ("nav", delta)))
        for i, ch in enumerate("MTWTFSS"):
            c.create_text(i * cell + cell / 2, S(34), text=ch, font=self.f_hour, fill=INK_FAINT)
        start, end = self._range()
        today = date.today()
        for r, week in enumerate(month_calendar.Calendar(0).monthdatescalendar(month.year, month.month)):
            y = S(52) + r * S(24)
            if self.view.get() == "Week" and week[0] == week_start(start):
                c.create_rectangle(0, y - S(2), S(204), y + S(22), fill="#DCE5F5", outline="")
            for i, d in enumerate(week):
                x = i * cell + cell / 2
                if d == today:
                    c.create_oval(x - S(11), y, x + S(11), y + S(22), fill=INK, outline="")
                c.create_text(x, y + S(11), text=str(d.day), font=self.f_small,
                              fill=WHITE if d == today else (INK if d.month == month.month else INK_FAINT))
                self.mini_hits.append((x - cell / 2, y, x + cell / 2, y + S(24), ("day", d)))

    def _mini_click(self, event):
        for x0, y0, x1, y1, what in self.mini_hits:
            if x0 <= event.x <= x1 and y0 <= event.y <= y1:
                if what[0] == "nav":
                    m = self.mini_month.month - 1 + what[1]
                    self.mini_month = date(self.mini_month.year + m // 12, m % 12 + 1, 1)
                    self._draw_mini()
                else:
                    self._set_anchor(what[1])
                return

    # ── grid interaction ───────────────────────────────────────────────────
    def _hit(self, event):
        x, y = self.body.canvasx(event.x), self.body.canvasy(event.y)
        found = None
        for x0, y0, x1, y1, target in reversed(self.hits):
            if x0 <= x <= x1 and y0 <= y <= y1:
                if isinstance(target, Block) and target.kind == "break":
                    found = found or target
                    continue
                return target
        return found

    def _grid_point(self, event) -> tuple[int, int] | None:
        """Day index and snapped minute under the pointer (week view)."""
        gutter, width = self.S(58), max(self.body.winfo_width(), 400)
        x, y = self.body.canvasx(event.x), self.body.canvasy(event.y)
        if x < gutter:
            return None
        day = min(6, int((x - gutter) / ((width - gutter) / 7)))
        minute = self.hour_range[0] * 60 + (y - self.S(10)) / self.S(52) * 60
        minute = max(self.hour_range[0] * 60, min(self.hour_range[1] * 60, round(minute / SNAP) * SNAP))
        return day, int(minute)

    def _on_press(self, event):
        self.body.focus_set()
        self._press, self._dragged = None, False
        target = self._hit(event)
        if isinstance(target, Block):
            self._select_block(target)
            return
        if isinstance(target, date):
            self.anchor = target
            self.mini_month = target.replace(day=1)
            self._close_panel()
            return
        if self.view.get() == "Week":
            point = self._grid_point(event)
            if point:
                self._press = point

    def _on_drag(self, event):
        if not self._press:
            return
        point = self._grid_point(event)
        if not point or point == self._press:
            return
        self._dragged = True
        day = week_start(self.anchor) + timedelta(days=self._press[0])
        a, z = sorted((self._press[1], point[1]))
        self.draft = (day, a, max(z, a + SNAP))
        self._redraw()

    def _on_release(self, event):
        if self._press and self._dragged and self.draft:
            day, a, z = self.draft
            self._open_new_event(day, a, z)
        elif self._press and not self._dragged:
            self._close_panel()
        self._press, self._dragged = None, False

    def _on_double(self, event):
        target = self._hit(event)
        if isinstance(target, date):
            self.view.set("Week")
            self._set_anchor(target)

    # ── selection → panels ─────────────────────────────────────────────────
    def _select_block(self, b: Block):
        self.sel_key = b.key
        self.draft = None
        if b.kind == "custom" and b.rule:
            self._open_event(b)
        elif b.kind in ("slot", "ghost"):
            self._open_slot(b)
        elif b.kind == "break":
            self._open_group(b.sched["group_id"]) if b.sched else self._close_panel()
        else:
            self.status_var.set("That entry can’t be matched to a rule, so it can’t be edited here.")
            self._show_panel("empty")
        self._redraw()

    def _new_event(self):
        day = self.anchor
        now = datetime.now()
        a = (now.hour + 1) * 60 if day == date.today() and now.hour < 22 else 9 * 60
        self._open_new_event(day, a, a + 60)

    def _open_new_event(self, day: date, a: int, z: int):
        self.draft = (day, a, z)
        self.edit_rule_id = None
        self.sel_key = None
        self.ev_title.set("New event")
        self.ev_name.set("")
        self.ev_date.set(day.isoformat())
        self.ev_start.set(_clock(a))
        self.ev_end.set(_clock(z))
        self.ev_repeat.set("Once")
        self.ev_priority.set("NORMAL")
        self.ev_error.set("")
        self.ev_delete.state(["disabled"])
        self._update_hint()
        self._show_panel("event")
        self.ev_name_entry.focus_set()
        self._redraw()

    def _open_event(self, b: Block):
        rule = b.rule
        self.edit_rule_id = rule.id
        self.ev_title.set("Edit event")
        self.ev_name.set(rule.name)
        self.ev_date.set(b.start.date().isoformat())
        self.ev_start.set(_clock(b.start_min))
        self.ev_end.set(_clock(b.end_min))
        kind = rule.recurrence.type
        self.ev_repeat.set("Once" if kind == RecurrenceType.NONE else "Every day" if kind == RecurrenceType.DAILY else "Every week")
        self.ev_priority.set(rule.priority.value)
        self.ev_error.set("")
        self.ev_delete.state(["!disabled"])
        self._update_hint()
        if kind != RecurrenceType.NONE:
            self.ev_hint.set(self.ev_hint.get() + " Changes apply to every occurrence.")
        self._show_panel("event")

    def _open_slot(self, b: Block):
        sched = b.sched
        if sched is None:
            self.status_var.set("This slot’s timetable could not be found in the group rules.")
            self._show_panel("empty")
            return
        group = self._group(sched["group_id"]) or {}
        code = DAYS[b.start.weekday()]
        self.slot_ctx = {"sched": sched, "day": code, "period": b.period, "act": b.act, "date": b.start.date()}
        self.sl_title.set(f"{group.get('name', 'Timetable')}  ·  slot {b.period}")
        self.sl_sub.set(f"{DAY_LONG[b.start.weekday()]}, repeats every week")
        self.sl_name.set((b.act or {}).get("name", ""))
        self.sl_start.set(_clock(b.start_min))
        self.sl_end.set(_clock(b.end_min))
        self.sl_priority.set((b.act or {}).get("priority", ""))
        self.sl_error.set("")
        self.sl_clear.state(["!disabled"] if b.act else ["disabled"])
        self._show_panel("slot")
        self.sl_name_entry.focus_set()

    def _open_group_from_slot(self):
        if self.slot_ctx:
            self._open_group(self.slot_ctx["sched"]["group_id"])

    def _open_group(self, group_id: str):
        group, sched = self._group(group_id), self._schedule_of(group_id)
        if not group or not sched:
            return
        self.group_id = group_id
        self.sel_key = None
        self.gp_title.set("Timetable rules")
        self.gp_name.set(group["name"])
        for d in DAYS:
            self.gp_days[d].set(d in sched.get("days", []))
        self.gp_start.set(sched["start_time"])
        self.gp_end.set(sched["end_time"])
        self.gp_period.set(str(sched.get("period_minutes", 45)))
        brk = (sorted(sched.get("breaks", []), key=lambda b: b["start_time"]) or [{}])[0]
        self.gp_bname.set(brk.get("name", ""))
        self.gp_bstart.set(brk.get("start_time", ""))
        self.gp_bend.set(brk.get("end_time", ""))
        self.gp_error.set("")
        self._show_panel("group")
        self._refresh_sidebar()
        self._redraw()

    # ── event editing ──────────────────────────────────────────────────────
    def _save_event(self):
        try:
            name = self.ev_name.get().strip()
            if not name:
                raise ValueError("Give the event a name.")
            target = parse_date(self.ev_date.get())
            s, e = parse_time(self.ev_start.get()), parse_time(self.ev_end.get())
            if s >= e:
                raise ValueError("The event has to end after it starts.")
            start, end = _clock(s), _clock(e)
            repeat = self.ev_repeat.get()
            if repeat == "Once":
                recurrence = Recurrence(type=RecurrenceType.NONE)
                constraints = TimeConstraints(fixed_interval=TimeWindow(start, end), date=target.isoformat())
            elif repeat == "Every day":
                recurrence = Recurrence(type=RecurrenceType.DAILY)
                constraints = TimeConstraints(fixed_interval=TimeWindow(start, end))
            else:
                recurrence = Recurrence(type=RecurrenceType.WEEKLY, days_of_week=[DAYS[target.weekday()]])
                constraints = TimeConstraints(fixed_interval=TimeWindow(start, end))

            candidate = copy.deepcopy(self.state)
            existing = next((r for r in candidate.rules if r.id == self.edit_rule_id), None)
            if existing and existing.id.startswith(GROUP_PREFIX):
                raise ValueError("This belongs to a timetable. Edit it from its slot.")
            priority = Priority(self.ev_priority.get())
            if existing:
                existing.name, existing.priority = name, priority
                existing.recurrence, existing.time_constraints = recurrence, constraints
                rule_id = existing.id
            else:
                prefix = {"Once": "custom__", "Every day": "custom_daily__"}.get(repeat, "custom_weekly__")
                stamp, n = target.isoformat().replace("-", ""), 1
                taken = {r.id for r in candidate.rules}
                while f"{prefix}{stamp}__{n}" in taken:
                    n += 1
                rule_id = f"{prefix}{stamp}__{n}"
                candidate.rules.append(ActivityRule(
                    id=rule_id, name=name, flexibility=Flexibility.FIXED, priority=priority,
                    recurrence=recurrence, time_constraints=constraints))
            candidate._validate()
            self._snapshot()
            self.state = candidate
            self._persist()
        except Exception as exc:
            self.ev_error.set(str(exc))
            return
        self.edit_rule_id = rule_id
        self.draft = None
        self.ev_title.set("Edit event")
        self.ev_error.set("")
        self.ev_delete.state(["!disabled"])
        self._set_anchor(target)
        self._saved(f"Saved “{name}”")

    def _delete_event(self):
        rule_id = self.edit_rule_id
        rule = next((r for r in self.state.rules if r.id == rule_id), None)
        if not rule or rule.id.startswith(GROUP_PREFIX):
            return
        self._snapshot()
        self.state.rules = [r for r in self.state.rules if r.id != rule_id]
        self.state.exceptions = [x for x in self.state.exceptions if x.rule_id != rule_id]
        self._persist()
        self._close_panel()
        self.status_var.set(f"Deleted “{rule.name}”  ·  Ctrl+Z to undo")

    def _delete_selected(self):
        if self.edit_rule_id and self.panels["event"].winfo_ismapped():
            self._delete_event()

    # ── timetable slot editing ─────────────────────────────────────────────
    def _save_slot(self):
        ctx = self.slot_ctx
        if not ctx:
            return
        try:
            name = self.sl_name.get().strip()
            if not name:
                raise ValueError("Give the slot a subject, or use Clear subject.")
            s, e = parse_time(self.sl_start.get()), parse_time(self.sl_end.get())
            if s >= e:
                raise ValueError("The slot has to end after it starts.")
            self._snapshot()
            sched = ctx["sched"]
            activities = sched.setdefault("activities", [])
            act = next((a for a in activities if a["day"] == ctx["day"] and int(a.get("period", 0)) == ctx["period"]), None)
            if act is None:
                act = {"id": f"{ctx['day'].lower()}_p{ctx['period']}", "day": ctx["day"], "period": ctx["period"]}
                activities.append(act)
            act.update({"name": name, "start_time": _clock(s), "end_time": _clock(e)})
            if self.sl_priority.get():
                act["priority"] = self.sl_priority.get()
            else:
                act.pop("priority", None)
            self._persist(calendar=False, rules=True)
            self._sync_timetables()
        except Exception as exc:
            if self._undo:
                self.state, self.config = self._undo.pop()
            self.sl_error.set(str(exc))
            return
        self.sl_error.set("")
        self.slot_ctx["act"] = act
        self.sl_clear.state(["!disabled"])
        self._redraw()
        self._saved(f"Saved {name} on {DAY_SHORT[DAYS.index(ctx['day'])]}")

    def _clear_slot(self):
        ctx = self.slot_ctx
        if not ctx or not ctx.get("act"):
            return
        try:
            self._snapshot()
            sched = ctx["sched"]
            sched["activities"] = [a for a in sched.get("activities", [])
                                   if not (a["day"] == ctx["day"] and int(a.get("period", 0)) == ctx["period"])]
            self._persist(calendar=False, rules=True)
            self._sync_timetables()
        except Exception as exc:
            if self._undo:
                self.state, self.config = self._undo.pop()
            self.sl_error.set(str(exc))
            return
        self._close_panel()
        self.status_var.set("Cleared the subject  ·  Ctrl+Z to undo")

    # ── timetable rules ────────────────────────────────────────────────────
    def _save_group(self):
        sched = self._schedule_of(self.group_id) if self.group_id else None
        group = self._group(self.group_id) if self.group_id else None
        if not sched or not group:
            return
        try:
            name = self.gp_name.get().strip()
            if not name:
                raise ValueError("Give the timetable a name.")
            days = [d for d in DAYS if self.gp_days[d].get()]
            if not days:
                raise ValueError("Pick at least one active day.")
            start, end = _clock(parse_time(self.gp_start.get())), _clock(parse_time(self.gp_end.get()))
            if _minutes(start) >= _minutes(end):
                raise ValueError("The day has to end after it starts.")
            try:
                period = int(self.gp_period.get().strip())
            except ValueError:
                raise ValueError("Slot length is a whole number of minutes.") from None
            if not 1 <= period <= 1440:
                raise ValueError("Slot length must be between 1 and 1440 minutes.")
            bname, bstart, bend = self.gp_bname.get().strip(), self.gp_bstart.get().strip(), self.gp_bend.get().strip()
            breaks: list[dict[str, Any]] = []
            if bname or bstart or bend:
                if not (bname and bstart and bend):
                    raise ValueError("A break needs a name, a start and an end. Clear all three to remove it.")
                bs, be = parse_time(bstart), parse_time(bend)
                if bs >= be:
                    raise ValueError("The break has to end after it starts.")
                breaks = [{"id": "default_break", "name": bname, "start_time": _clock(bs), "end_time": _clock(be),
                           "priority": "HIGH", "override_policy": "never"}]
            self._snapshot()
            group["name"] = name
            sched.update({"days": days, "start_time": start, "end_time": end, "period_minutes": period, "breaks": breaks})
            self._persist(calendar=False, rules=True)
            self._sync_timetables()
        except Exception as exc:
            if self._undo and "Slot length" not in str(exc) and self._undo[-1][1] != self.config:
                self.state, self.config = self._undo.pop()
            self.gp_error.set(str(exc))
            return
        self.gp_error.set("")
        self._refresh_sidebar()
        self._redraw()
        self._saved(f"Saved {name}")

    def _add_school_template(self):
        if any(g["id"] == "school" for g in self.config["groups"]):
            self._open_group("school")
            self.status_var.set("A School timetable already exists, so it’s open instead.")
            return
        self._snapshot()
        template = _school_template()
        self.config["groups"].extend(template["groups"])
        self.config["schedules"].extend(template["schedules"])
        self._persist(calendar=False, rules=True)
        self._refresh_sidebar()
        self._open_group("school")
        self._saved("Added the School timetable")

    def _add_blank_group(self):
        n = 1
        ids = {g["id"] for g in self.config["groups"]}
        while f"group_{n}" in ids:
            n += 1
        gid = f"group_{n}"
        self._snapshot()
        self.config["groups"].append({"id": gid, "name": f"Timetable {n}", "priority": "NORMAL", "flexibility": "FLEXIBLE",
                                      "override_policy": "overrideable", "description": ""})
        self.config["schedules"].append({"id": f"schedule_{n}", "name": f"Schedule {n}", "group_id": gid,
                                         "days": ["MON", "TUE", "WED", "THU", "FRI"], "start_time": "09:00",
                                         "end_time": "17:00", "period_minutes": 60, "breaks": [], "activities": []})
        self._persist(calendar=False, rules=True)
        self._refresh_sidebar()
        self._open_group(gid)
        self._saved(f"Added Timetable {n}")

    def _refresh_sidebar(self):
        for child in self.group_rows.winfo_children():
            child.destroy()
        if not self.config["groups"]:
            tk.Label(self.group_rows, text="No timetables yet. Add the School template to start.", wraplength=self.S(200),
                     justify="left", anchor="w", font=self.f_small, bg=PANEL, fg=INK_SOFT).pack(fill="x", pady=4)
            return
        for group in self.config["groups"]:
            sched = self._schedule_of(group["id"]) or {}
            active = group["id"] == self.group_id
            row = tk.Frame(self.group_rows, bg=WHITE if active else PANEL, padx=8, pady=5, cursor="hand2",
                           highlightthickness=1, highlightbackground=RULE_STRONG if active else PANEL)
            row.pack(fill="x", pady=1)
            _, edge = PRIORITY.get(group.get("priority", "NORMAL"), PRIORITY["NORMAL"])
            tk.Frame(row, bg=edge, width=4, height=self.S(26)).pack(side="left", padx=(0, 8))
            text = tk.Frame(row, bg=row["bg"])
            text.pack(side="left", fill="x")
            tk.Label(text, text=group["name"], font=self.f_bold, bg=row["bg"], fg=INK, anchor="w").pack(fill="x")
            days = sched.get("days", [])
            span = f"{DAY_SHORT[DAYS.index(days[0])]}–{DAY_SHORT[DAYS.index(days[-1])]}" if len(days) > 1 else (
                DAY_SHORT[DAYS.index(days[0])] if days else "")
            tk.Label(text, text=f"{span}  {sched.get('start_time', '')}–{sched.get('end_time', '')}", font=self.f_small,
                     bg=row["bg"], fg=INK_SOFT, anchor="w").pack(fill="x")
            for widget in (row, text, *row.winfo_children(), *text.winfo_children()):
                widget.bind("<Button-1>", lambda _e, gid=group["id"]: self._open_group(gid))

    def _tick(self):
        if self.view.get() == "Week":
            self._redraw()
        self.root.after(60000, self._tick)


def main():
    try:  # sharp text on high-DPI Windows displays
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    root = tk.Tk()
    UnifiedCalendarGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()