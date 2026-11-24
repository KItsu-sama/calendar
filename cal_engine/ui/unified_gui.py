# cal_engine/ui/unified_gui.py
"""The calendar window: one place for events, timetables and groups.

Design notes
- The grid is drawn like an exercise book page: pale ruled paper, a red margin
  line at the hour gutter, and the same line marking "now".
- Everything is edited in place. Drag on the grid to block out time, click a
  block to edit it, click a timetable or a group on the left to change it.
- This window replaces the separate group/timetable editor. Timetables, their
  breaks, place tags and slots, and groups with their members, are all edited
  here, against the same grid.
- Every change is validated, then written to both files in one transaction
  (calendar and group/timetable configuration), so they can never disagree.
  Ctrl+Z undoes it, so nothing needs a confirmation dialog.
- The grid is keyboard-operable: Tab into it, then use the arrow keys.
"""
from __future__ import annotations

import calendar as month_calendar
import copy
import json
import re
import shutil
import tkinter as tk
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from tkinter import font as tkfont
from typing import Any, Callable

from ..core.models import (
    ActivityRule,
    Flexibility,
    Priority,
    Recurrence,
    RecurrenceType,
    TimeConstraints,
    TimeWindow,
)
from ..core.storage import CalendarState
from ..core.transactional_persistence import CoordinatedStore
from ..groups.group_schedule import (
    CONFIG_VERSION,
    GENERATED_PREFIX,
    apply_timetables,
    export_to_calendar,
    load_config,
    save_config,
    validate_config,
)
from ..scheduling.review import export_commands

# ── Palette ────────────────────────────────────────────────────────────────
# Text and control colours were measured against WCAG 2.1 AA: body and soft
# text are at least 4.5:1 on every surface they sit on, control borders and
# graphics at least 3:1.
PAPER = "#FAFBFE"         # grid paper
PANEL = "#EEF2F9"         # side panels and toolbar
WHITE = "#FFFFFF"
INK = "#1C2744"
INK_SOFT = "#4B5776"      # secondary text (6.4:1 on panel)
INK_FAINT = "#5B6786"     # tertiary text (5.0:1 on panel)
RULE = "#E0E7F3"          # hour lines
RULE_STRONG = "#C6D1E6"   # dividers, decorative borders
CONTROL_EDGE = "#7A86A6"  # borders of inputs and buttons (3.2:1 on panel)
MARGIN = "#D4505C"        # the red margin line and the "now" line
TODAY_TINT = "#F1F5FD"
BREAK_FILL = "#CBD3E3"
ERROR = "#B3333F"
WARN = "#7A4F00"

# priority -> (soft fill, strong edge). White text on every edge is at least 4.8:1.
PRIORITY = {
    "CRITICAL": ("#F7D6D9", "#B93A45"),
    "HIGH": ("#FBEAB9", "#8F6200"),
    "NORMAL": ("#D9E5F9", "#3F6BB3"),
    "LOW": ("#DDEBE2", "#4A7C61"),
}
PRIORITY_LABELS = [("CRITICAL", "Critical"), ("HIGH", "High"), ("NORMAL", "Normal"), ("LOW", "Low")]
PRIORITY_LABEL = dict(PRIORITY_LABELS)

DAYS = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]
DAY_SHORT = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
DAY_LONG = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
GROUP_PREFIX = GENERATED_PREFIX            # rules written by the timetable exporter
LEGACY_PREFIX = "group_schedule__"         # what the version-1 exporter wrote
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


def clean_id(text: str, what: str) -> str:
    """An ID is part of generated rule names, so keep it simple and unambiguous."""
    value = text.strip()
    if not value:
        raise ValueError(f"Give the {what} an ID.")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_\-]*", value) or "__" in value:
        raise ValueError(f"A {what} ID uses letters, digits, “-” and single “_” only (no spaces).")
    return value


def unique_id(base: str, taken: set[str]) -> str:
    n, candidate = 1, f"{base}_1"
    while candidate in taken:
        n += 1
        candidate = f"{base}_{n}"
    return candidate


def slot_layout(timetable: dict[str, Any]) -> list[dict[str, Any]]:
    """The ordered slots and breaks that make up one day of a timetable."""
    breaks = sorted(timetable.get("breaks", []), key=lambda b: _minutes(b["start_time"]))
    cursor = _minutes(timetable["start_time"])
    finish = _minutes(timetable["end_time"])
    length = max(1, int(timetable.get("period_minutes", 45)))
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


def empty_config() -> dict[str, Any]:
    return {"version": CONFIG_VERSION, "groups": [], "timetables": []}


def _school_template() -> dict[str, Any]:
    """Explicit opt-in starter template; never loaded as an implicit fallback."""
    return {
        "groups": [{"id": "school", "name": "School", "description": "Weekday school timetable"}],
        "timetables": [{
            "id": "school_week", "name": "School week", "description": "", "place_tag": "",
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
    tt: dict | None = None      # the timetable this block belongs to
    act: dict | None = None     # the timetable slot (activity) behind a "slot" block
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
    """A row of colour-coded radio chips (used for priority). Keyboard: Tab, then Space/Enter."""

    def __init__(self, master, options, variable, font, colors=None):
        super().__init__(master, bg=PANEL)
        self.variable = variable
        self.colors = colors or PRIORITY
        self.chips: dict[str, tk.Label] = {}
        for value, label in options:
            chip = tk.Label(
                self, text=label, font=font, padx=6, pady=4, cursor="hand2",
                takefocus=True, highlightthickness=2, highlightbackground=PANEL, highlightcolor=INK,
            )
            chip.pack(side="left", padx=(0, 3))
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


class ScrollFrame(tk.Frame):
    """A vertically scrolling container. Put children in ``.body``."""

    def __init__(self, master, bg=PANEL):
        super().__init__(master, bg=bg)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0)
        self.bar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.bar.set)
        self.body = tk.Frame(self.canvas, bg=bg)
        self._window = self.canvas.create_window(0, 0, anchor="nw", window=self.body)
        self.bar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.body.bind("<Configure>", lambda _e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self._window, width=e.width))
        for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.bind_all(sequence, self._wheel, add="+")

    def _over_me(self, event) -> bool:
        widget = self.winfo_containing(event.x_root, event.y_root)
        while widget is not None:
            if widget is self:
                return True
            widget = widget.master
        return False

    def _wheel(self, event):
        if not self._over_me(event) or self.canvas.yview() == (0.0, 1.0):
            return
        if event.num == 4:
            step = -1
        elif event.num == 5:
            step = 1
        else:
            step = -1 if event.delta > 0 else 1
        self.canvas.yview_scroll(step * 2, "units")

    def to_top(self):
        self.canvas.yview_moveto(0)


# ── Application ────────────────────────────────────────────────────────────
class UnifiedCalendarGUI:
    def __init__(
        self,
        root: tk.Tk,
        calendar_path: str | Path | None = None,
        config_path: str | Path | None = None,
        focus: str | None = None,
    ):
        self.root = root
        root.title("cal")
        root.geometry("1360x840")
        root.minsize(1100, 660)
        self.k = max(1.0, root.winfo_fpixels("1i") / 96.0)

        self.calendar_path = Path(calendar_path) if calendar_path else Path("data/calendar.json")
        self.config_path = Path(config_path) if config_path else Path("groups_schedules.json")
        self.state = CalendarState()
        self.config: dict[str, Any] = empty_config()

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
        self.sl_groups: list[str] = []      # group ids the open slot will belong to
        self._group_choice: dict[str, str] = {}
        self.tt_id: str | None = None       # timetable open in the inspector
        self.group_sel: str | None = None   # group open in the inspector
        self.hour_range = (6, 22)
        self._undo: list[tuple] = []
        self._press: tuple[int, int] | None = None
        self._dragged = False
        self._redraw_job = None
        self._break_rows: list[dict[str, Any]] = []
        self._member_refs: list[tuple[str, str]] = []

        self._make_fonts()
        self._make_styles()
        self._build()
        self._bind_keys()
        self.view.trace_add("write", lambda *_: self._on_view_change())
        self.show_free.trace_add("write", lambda *_: self._redraw())
        self._load_data()
        if focus == "timetables" and self.config["timetables"]:
            self._open_timetable(self.config["timetables"][0]["id"], focus=False)
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
        style.configure(".", background=PANEL, foreground=INK, font=self.f_ui, bordercolor=CONTROL_EDGE, focuscolor=INK)
        style.configure("TFrame", background=PANEL)
        style.configure("TLabel", background=PANEL, foreground=INK)
        style.configure("TEntry", fieldbackground=WHITE, padding=5, bordercolor=CONTROL_EDGE, lightcolor=WHITE, darkcolor=WHITE)
        style.map("TEntry", bordercolor=[("focus", INK)])
        style.configure("TCombobox", fieldbackground=WHITE, background=WHITE, padding=5, arrowcolor=INK_SOFT,
                        bordercolor=CONTROL_EDGE, lightcolor=WHITE, darkcolor=WHITE)
        style.map("TCombobox", bordercolor=[("focus", INK)], fieldbackground=[("readonly", WHITE)],
                  selectbackground=[("readonly", WHITE)], selectforeground=[("readonly", INK)])
        style.configure("TButton", padding=(10, 5), width=0, background=WHITE, bordercolor=CONTROL_EDGE, relief="flat")
        style.map("TButton", background=[("active", "#E3EAF6")], bordercolor=[("focus", INK)])
        style.configure("Accent.TButton", background=INK, foreground=WHITE, bordercolor=INK)
        style.map("Accent.TButton", background=[("active", "#2D3D69")])
        style.configure("Danger.TButton", background=WHITE, foreground=ERROR, bordercolor=ERROR)
        style.map("Danger.TButton", background=[("active", "#F7E4E6")])
        style.configure("Quiet.TButton", background=PANEL, bordercolor=PANEL, foreground=INK_SOFT)
        style.map("Quiet.TButton", background=[("active", "#E1E8F4")], foreground=[("active", INK)],
                  bordercolor=[("focus", INK)])
        style.configure("Nav.TButton", padding=(9, 5), background=WHITE, bordercolor=CONTROL_EDGE)
        style.configure("Seg.Toolbutton", padding=(14, 5), width=0, background=WHITE, foreground=INK_SOFT, bordercolor=CONTROL_EDGE)
        style.map("Seg.Toolbutton", background=[("selected", INK), ("active", "#E3EAF6")],
                  foreground=[("selected", WHITE)])
        style.configure("Day.Toolbutton", padding=(4, 4), width=0, background=WHITE, foreground=INK_SOFT, bordercolor=CONTROL_EDGE)
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
        menu.add_command(label="Open timetables and groups…", command=self._browse_config)
        menu.add_command(label="Reload from disk", command=self._load_data)
        menu.add_separator()
        menu.add_command(label="Show calendar as commands…", command=self._show_commands)
        menu.add_command(label="Export timetables to another calendar…", command=self._export_calendar_as)
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
        self.side = ScrollFrame(main, PANEL)
        self.side.configure(width=self.S(244))
        self.side.grid(row=0, column=0, sticky="ns")
        self.side.pack_propagate(False)
        tk.Frame(main, bg=RULE_STRONG, width=1).grid(row=0, column=1, sticky="ns")
        center = tk.Frame(main, bg=PAPER)
        center.grid(row=0, column=2, sticky="nsew")
        tk.Frame(main, bg=RULE_STRONG, width=1).grid(row=0, column=3, sticky="ns")
        self.inspector = ScrollFrame(main, PANEL)
        self.inspector.configure(width=self.S(344))
        self.inspector.grid(row=0, column=4, sticky="ns")
        self.inspector.pack_propagate(False)

        self._build_sidebar(self.side.body)
        self._build_grid(center)
        self._build_inspector()

        tk.Frame(root, bg=RULE_STRONG, height=1).grid(row=3, column=0, sticky="ew")
        foot = tk.Frame(root, bg=PANEL, padx=self.S(14), pady=self.S(5))
        foot.grid(row=4, column=0, sticky="ew")
        tk.Label(foot, textvariable=self.status_var, font=self.f_small, bg=PANEL, fg=INK_SOFT).pack(side="left")
        self.files_var = tk.StringVar()
        tk.Label(foot, textvariable=self.files_var, font=self.f_small, bg=PANEL, fg=INK_FAINT).pack(side="right")

    def _build_sidebar(self, side):
        pad = self.S(14)
        self.mini = tk.Canvas(side, bg=PANEL, highlightthickness=0, width=self.S(204), height=self.S(196))
        self.mini.pack(padx=pad, pady=(pad, 6), anchor="w")
        self.mini.bind("<Button-1>", self._mini_click)

        tk.Frame(side, bg=RULE_STRONG, height=1).pack(fill="x", padx=pad, pady=8)
        head = tk.Frame(side, bg=PANEL)
        head.pack(fill="x", padx=pad)
        tk.Label(head, text="Timetables", font=self.f_bold, bg=PANEL, fg=INK).pack(side="left")
        add = ttk.Menubutton(head, text="Add", style="Quiet.TButton")
        add_menu = tk.Menu(add, tearoff=False, font=self.f_ui)
        add_menu.add_command(label="School template", command=self._add_school_template)
        add_menu.add_command(label="Blank timetable", command=self._add_blank_timetable)
        add["menu"] = add_menu
        add.pack(side="right")
        self.tt_rows = tk.Frame(side, bg=PANEL)
        self.tt_rows.pack(fill="x", padx=pad, pady=(4, 0))
        ttk.Checkbutton(side, text="Show free slots on the grid", variable=self.show_free).pack(
            anchor="w", padx=pad, pady=(8, 0))

        tk.Frame(side, bg=RULE_STRONG, height=1).pack(fill="x", padx=pad, pady=12)
        head = tk.Frame(side, bg=PANEL)
        head.pack(fill="x", padx=pad)
        tk.Label(head, text="Groups", font=self.f_bold, bg=PANEL, fg=INK).pack(side="left")
        ttk.Button(head, text="Add", style="Quiet.TButton", command=self._add_group).pack(side="right")
        self.group_rows = tk.Frame(side, bg=PANEL)
        self.group_rows.pack(fill="x", padx=pad, pady=(4, 0))

        tk.Frame(side, bg=RULE_STRONG, height=1).pack(fill="x", padx=pad, pady=12)
        legend = tk.Canvas(side, bg=PANEL, highlightthickness=0, width=self.S(204), height=self.S(150))
        legend.pack(padx=pad, pady=(0, pad), anchor="w")
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
        legend.create_rectangle(0, y, self.S(18), y + self.S(14), fill=PAPER, outline=CONTROL_EDGE, dash=(3, 2))
        legend.create_text(self.S(28), y + self.S(7), text="Free timetable slot", anchor="w", font=self.f_small, fill=INK_SOFT)

    def _build_grid(self, parent):
        parent.rowconfigure(1, weight=1)
        parent.columnconfigure(0, weight=1)
        # Both canvases reserve the same 2px border so day columns line up; the
        # body's border becomes the visible focus ring while the grid has focus.
        self.head = tk.Canvas(parent, bg=PAPER, highlightthickness=2, highlightbackground=PAPER, height=self.S(66))
        self.head.grid(row=0, column=0, sticky="ew")
        self.body = tk.Canvas(parent, bg=PAPER, highlightthickness=2, highlightbackground=PAPER,
                              highlightcolor=INK, takefocus=True)
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
        self.body.bind("<FocusIn>", lambda _e: self._grid_focus_in())
        for key, step in (("<Up>", ("move", -1)), ("<Down>", ("move", 1)), ("<Left>", ("hop", -1)), ("<Right>", ("hop", 1))):
            self.body.bind(key, lambda e, s=step: self._grid_key(s))
        for key in ("<Return>", "<space>"):
            self.body.bind(key, lambda _e: self._grid_activate())

    # ── inspector panels ───────────────────────────────────────────────────
    def _label(self, parent, text):
        return tk.Label(parent, text=text, font=self.f_small, bg=PANEL, fg=INK_SOFT, anchor="w")

    def _field(self, parent, label, var, values=None, side=None, submit=None):
        box = tk.Frame(parent, bg=PANEL)
        self._label(box, label).pack(anchor="w")
        if values:
            widget = ttk.Combobox(box, textvariable=var, values=values, state="readonly", font=self.f_ui)
        else:
            widget = ttk.Entry(box, textvariable=var, font=self.f_ui)
            if submit:
                widget.bind("<Return>", lambda _e: submit())
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

    def _message(self, parent, var, color):
        label = tk.Label(parent, textvariable=var, font=self.f_small, bg=PANEL, fg=color, anchor="w",
                         wraplength=self.S(276), justify="left")
        label.pack(fill="x", pady=(0, 6))
        return label

    def _build_inspector(self):
        pad = self.S(16)
        wrap = self.S(276)
        host = self.inspector.body
        self.panels: dict[str, tk.Frame] = {}
        for name in ("empty", "event", "slot", "timetable", "group"):
            self.panels[name] = tk.Frame(host, bg=PANEL, padx=pad, pady=pad)

        # empty
        p = self.panels["empty"]
        tk.Label(p, text="Nothing selected", font=self.f_head, bg=PANEL, fg=INK, anchor="w").pack(fill="x")
        tk.Label(
            p, justify="left", anchor="w", wraplength=wrap, font=self.f_ui, bg=PANEL, fg=INK_SOFT,
            text=("Drag across the grid to block out time.\nClick an event, a timetable slot or a break to edit it. "
                  "Click a timetable or a group on the left to change its rules.\n\n"
                  "Keyboard\n"
                  "Tab  into the grid\n"
                  "↑ ↓  next / previous entry\n"
                  "← →  same time on the next day\n"
                  "Esc  leave the entry\n"
                  "PgUp PgDn  previous / next period\n"
                  "N  new event    T  today\n"
                  "W  M  week / month\nCtrl+Z  undo"),
        ).pack(fill="x", pady=(8, 0))

        # event
        p = self.panels["event"]
        self.ev_title = tk.StringVar(value="New event")
        self.ev_name, self.ev_date = tk.StringVar(), tk.StringVar()
        self.ev_start, self.ev_end = tk.StringVar(), tk.StringVar()
        self.ev_repeat, self.ev_priority = tk.StringVar(value="Once"), tk.StringVar(value="NORMAL")
        self.ev_hint, self.ev_warn, self.ev_error = tk.StringVar(), tk.StringVar(), tk.StringVar()
        tk.Label(p, textvariable=self.ev_title, font=self.f_head, bg=PANEL, fg=INK, anchor="w").pack(fill="x", pady=(0, 10))
        self.ev_name_entry = self._field(p, "Name", self.ev_name, submit=lambda: self._save_event())
        self._field(p, "Date", self.ev_date, submit=lambda: self._save_event())
        row = self._pair(p)
        self._field(row, "Starts", self.ev_start, side="first", submit=lambda: self._save_event())
        self._field(row, "Ends", self.ev_end, side="last", submit=lambda: self._save_event())
        self._field(p, "Repeats", self.ev_repeat, values=["Once", "Every week", "Every day"])
        self._message(p, self.ev_hint, INK_FAINT)
        self._label(p, "Priority").pack(anchor="w")
        Chips(p, PRIORITY_LABELS, self.ev_priority, self.f_small).pack(anchor="w", pady=(3, 14))
        self._message(p, self.ev_warn, WARN)
        self._message(p, self.ev_error, ERROR)
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
        self.sl_priority = tk.StringVar(value="")
        self.sl_warn, self.sl_error = tk.StringVar(), tk.StringVar()
        tk.Label(p, textvariable=self.sl_title, font=self.f_head, bg=PANEL, fg=INK, anchor="w",
                 wraplength=wrap, justify="left").pack(fill="x")
        tk.Label(p, textvariable=self.sl_sub, font=self.f_small, bg=PANEL, fg=INK_SOFT, anchor="w").pack(fill="x", pady=(0, 12))
        self.sl_name_entry = self._field(p, "Subject", self.sl_name, submit=lambda: self._save_slot())
        row = self._pair(p)
        self._field(row, "Starts", self.sl_start, side="first", submit=lambda: self._save_slot())
        self._field(row, "Ends", self.sl_end, side="last", submit=lambda: self._save_slot())
        self._label(p, "Priority").pack(anchor="w")
        colors = dict(PRIORITY)
        colors[""] = (WHITE, INK_SOFT)
        Chips(p, [("", "Default")] + PRIORITY_LABELS, self.sl_priority, self.f_small, colors).pack(anchor="w", pady=(3, 14))

        self._label(p, "Groups").pack(anchor="w", pady=(0, 3))
        self.sl_groups_box = tk.Frame(p, bg=PANEL)
        self.sl_groups_box.pack(fill="x")
        add_row = tk.Frame(p, bg=PANEL)
        add_row.pack(fill="x", pady=(4, 10))
        self.sl_group_var = tk.StringVar()
        self.sl_group_combo = ttk.Combobox(add_row, textvariable=self.sl_group_var, state="readonly", font=self.f_small)
        self.sl_group_combo.pack(side="left", fill="x", expand=True)
        ttk.Button(add_row, text="Add", style="Quiet.TButton", command=self._slot_add_group).pack(
            side="left", padx=(6, 0))

        self._message(p, self.sl_warn, WARN)
        self._message(p, self.sl_error, ERROR)
        btns = tk.Frame(p, bg=PANEL)
        btns.pack(fill="x")
        ttk.Button(btns, text="Save slot", style="Accent.TButton", command=self._save_slot).pack(side="left")
        self.sl_clear = ttk.Button(btns, text="Clear subject", command=self._clear_slot)
        self.sl_clear.pack(side="left", padx=6)
        ttk.Button(btns, text="Close", style="Quiet.TButton", command=self._close_panel).pack(side="right")
        ttk.Button(p, text="Edit this timetable", style="Quiet.TButton", command=self._open_timetable_from_slot).pack(
            anchor="w", pady=(10, 0))

        # timetable rules
        p = self.panels["timetable"]
        self.tt_title = tk.StringVar(value="Timetable")
        self.tt_name, self.tt_id_var = tk.StringVar(), tk.StringVar()
        self.tt_desc, self.tt_place = tk.StringVar(), tk.StringVar()
        self.tt_start, self.tt_end, self.tt_period = tk.StringVar(), tk.StringVar(), tk.StringVar()
        self.tt_days = {d: tk.BooleanVar() for d in DAYS}
        self.tt_warn, self.tt_error = tk.StringVar(), tk.StringVar()
        tk.Label(p, textvariable=self.tt_title, font=self.f_head, bg=PANEL, fg=INK, anchor="w").pack(fill="x", pady=(0, 10))
        self.tt_name_entry = self._field(p, "Name", self.tt_name, submit=lambda: self._save_timetable())
        self._field(p, "ID", self.tt_id_var, submit=lambda: self._save_timetable())
        self._field(p, "Description", self.tt_desc, submit=lambda: self._save_timetable())
        self._field(p, "Place tag", self.tt_place, submit=lambda: self._save_timetable())
        self._label(p, "Active days").pack(anchor="w")
        days = tk.Frame(p, bg=PANEL)
        days.pack(fill="x", pady=(3, 10))
        for i, d in enumerate(DAYS):
            ttk.Checkbutton(days, text=DAY_SHORT[i], variable=self.tt_days[d], style="Day.Toolbutton").pack(
                side="left", padx=(0, 3))
        row = self._pair(p)
        self._field(row, "Day starts", self.tt_start, side="first", submit=lambda: self._save_timetable())
        self._field(row, "Day ends", self.tt_end, side="last", submit=lambda: self._save_timetable())
        self._field(p, "Slot length (minutes)", self.tt_period, submit=lambda: self._save_timetable())
        head = tk.Frame(p, bg=PANEL)
        head.pack(fill="x", pady=(4, 4))
        tk.Label(head, text="Breaks", font=self.f_bold, bg=PANEL, fg=INK).pack(side="left")
        ttk.Button(head, text="Add break", style="Quiet.TButton", command=lambda: self._add_break_row()).pack(side="right")
        self.tt_breaks_box = tk.Frame(p, bg=PANEL)
        self.tt_breaks_box.pack(fill="x")
        self._message(p, self.tt_warn, WARN)
        self._message(p, self.tt_error, ERROR)
        btns = tk.Frame(p, bg=PANEL)
        btns.pack(fill="x")
        ttk.Button(btns, text="Save timetable", style="Accent.TButton", command=self._save_timetable).pack(side="left")
        ttk.Button(btns, text="Delete", style="Danger.TButton", command=self._delete_timetable).pack(side="left", padx=6)
        ttk.Button(btns, text="Close", style="Quiet.TButton", command=self._close_panel).pack(side="right")

        # group
        p = self.panels["group"]
        self.gp_name, self.gp_id_var, self.gp_desc = tk.StringVar(), tk.StringVar(), tk.StringVar()
        self.gp_error = tk.StringVar()
        tk.Label(p, text="Group", font=self.f_head, bg=PANEL, fg=INK, anchor="w").pack(fill="x", pady=(0, 4))
        tk.Label(p, text="Groups only label timetable slots. They never change how anything is scheduled.",
                 font=self.f_small, bg=PANEL, fg=INK_SOFT, anchor="w", wraplength=wrap, justify="left").pack(
            fill="x", pady=(0, 10))
        self.gp_name_entry = self._field(p, "Name", self.gp_name, submit=lambda: self._save_group())
        self._field(p, "ID", self.gp_id_var, submit=lambda: self._save_group())
        self._field(p, "Description", self.gp_desc, submit=lambda: self._save_group())
        self.gp_members_lbl = self._label(p, "Members")
        self.gp_members_lbl.pack(anchor="w")
        box = tk.Frame(p, bg=PANEL)
        box.pack(fill="x", pady=(2, 4))
        self.gp_members = tk.Listbox(box, height=7, exportselection=False, font=self.f_small, activestyle="dotbox",
                                     bg=WHITE, fg=INK, relief="flat", highlightthickness=1,
                                     highlightbackground=CONTROL_EDGE, highlightcolor=INK,
                                     selectbackground=INK, selectforeground=WHITE)
        self.gp_members.pack(side="left", fill="x", expand=True)
        self.gp_members.bind("<Double-Button-1>", lambda _e: self._jump_to_member())
        self.gp_members.bind("<Return>", lambda _e: self._jump_to_member())
        tk.Label(p, text="Double-click or press Enter on a member to show it on the grid.", font=self.f_small,
                 bg=PANEL, fg=INK_FAINT, anchor="w", wraplength=wrap, justify="left").pack(fill="x", pady=(0, 10))
        self._message(p, self.gp_error, ERROR)
        btns = tk.Frame(p, bg=PANEL)
        btns.pack(fill="x")
        ttk.Button(btns, text="Save group", style="Accent.TButton", command=self._save_group).pack(side="left")
        ttk.Button(btns, text="Delete", style="Danger.TButton", command=self._delete_group).pack(side="left", padx=6)
        ttk.Button(btns, text="Close", style="Quiet.TButton", command=self._close_panel).pack(side="right")

        self._show_panel("empty")

    def _show_panel(self, name: str):
        for panel in self.panels.values():
            panel.pack_forget()
        self.panels[name].pack(fill="both", expand=True)
        self.inspector.to_top()

    def _close_panel(self):
        self.sel_key = None
        self.draft = None
        self.edit_rule_id = None
        self.slot_ctx = None
        self.tt_id = None
        self.group_sel = None
        self._show_panel("empty")
        self._refresh_sidebar()
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
        r.bind("<Escape>", self._escape)
        for key, fn in [("<Left>", lambda: self._step(-1)), ("<Right>", lambda: self._step(1)),
                        ("<Prior>", lambda: self._step(-1)), ("<Next>", lambda: self._step(1)),
                        ("t", self._today), ("n", self._new_event),
                        ("w", lambda: self.view.set("Week")), ("m", lambda: self.view.set("Month")),
                        ("<Delete>", self._delete_selected)]:
            r.bind(key, lambda e, f=fn: None if self._typing(e) else f())

    def _typing(self, event) -> bool:
        return event.widget.winfo_class() in ("Entry", "TEntry", "TCombobox", "Text", "Listbox")

    def _escape(self, _event=None):
        self._close_panel()
        self.body.focus_set()

    # ── data ───────────────────────────────────────────────────────────────
    def _read_config(self) -> tuple[dict[str, Any], bool]:
        """Load the timetable/group file; a version-1 file is upgraded in memory."""
        if not self.config_path.exists():
            return empty_config(), False
        config = load_config(self.config_path)
        try:
            upgraded = json.loads(self.config_path.read_text(encoding="utf-8")).get("version") == 1
        except (OSError, ValueError, AttributeError):
            upgraded = False
        if upgraded:
            backup = self.config_path.with_name(f"{self.config_path.stem}.v1-backup{self.config_path.suffix}")
            if not backup.exists():
                shutil.copy2(self.config_path, backup)
        return config, upgraded

    @staticmethod
    def _reconcile(state: CalendarState, config: dict[str, Any]) -> CalendarState:
        """The calendar's generated timetable rules, rebuilt from the configuration.

        Leaves the calendar alone when the configuration has no timetables, so a
        missing or empty configuration file can never hide existing entries.
        """
        if not config["timetables"]:
            return state
        candidate = state.copy()
        legacy = {r.id for r in candidate.rules if r.id.startswith(LEGACY_PREFIX)}
        candidate.rules = [r for r in candidate.rules if r.id not in legacy]
        candidate.exceptions = [x for x in candidate.exceptions if x.rule_id not in legacy]
        apply_timetables(config, candidate)
        return candidate

    def _load_data(self):
        try:
            state = CalendarState.load(self.calendar_path) if self.calendar_path.exists() else CalendarState()
            config, upgraded = self._read_config()
            reconciled = self._reconcile(state, config)
        except Exception as exc:
            self.status_var.set(f"Couldn’t load: {exc}")
            return
        stale = reconciled.to_dict() != state.to_dict()
        self.state, self.config = reconciled, config
        self._undo.clear()
        self.root.title(f"cal — {self.calendar_path.name}")
        self.files_var.set(f"{self.calendar_path.name}  ·  {self.config_path.name}")
        message = f"Loaded {self.calendar_path.name} and {self.config_path.name}"
        if upgraded:
            message += f"  ·  upgraded from version 1 (original kept as {self.config_path.stem}.v1-backup{self.config_path.suffix})"
        elif stale:
            message += "  ·  timetable entries refreshed from the rules; saved on your next change"
        self.status_var.set(message)
        self._close_panel()

    def _browse_calendar(self):
        name = filedialog.askopenfilename(title="Open calendar", filetypes=[("Calendar JSON", "*.json"), ("All files", "*.*")])
        if name:
            self.calendar_path = Path(name)
            self._load_data()

    def _browse_config(self):
        name = filedialog.askopenfilename(title="Open timetables and groups", filetypes=[("JSON", "*.json"), ("All files", "*.*")])
        if name:
            self.config_path = Path(name)
            self._load_data()

    def _export_calendar_as(self):
        name = filedialog.asksaveasfilename(
            title="Export timetables into a calendar file", defaultextension=".json",
            filetypes=[("Calendar JSON", "*.json"), ("All files", "*.*")],
            initialfile=self.calendar_path.name)
        if not name:
            return
        try:
            count = export_to_calendar(self.config, name)
        except Exception as exc:
            messagebox.showerror("Export failed", str(exc))
            return
        self.status_var.set(f"Exported {count} timetable rules into {Path(name).name}. Its other entries were kept.")

    def _show_commands(self):
        """The calendar written back out as the commands that would rebuild it."""
        try:
            script = export_commands(self.calendar_path)
        except Exception as exc:
            messagebox.showerror("Calendar as commands", f"Couldn’t read {self.calendar_path.name}:\n{exc}")
            return
        win = tk.Toplevel(self.root)
        win.title("Calendar as commands")
        win.geometry(f"{self.S(820)}x{self.S(520)}")
        win.configure(bg=PANEL)
        win.transient(self.root)
        tk.Label(win, text="Run these to rebuild the calendar. Timetable slots are not commands; they live in the "
                           "timetable file.", font=self.f_small, bg=PANEL, fg=INK_SOFT, anchor="w",
                 justify="left", wraplength=self.S(780)).pack(fill="x", padx=self.S(14), pady=(self.S(12), 6))
        frame = tk.Frame(win, bg=PANEL)
        frame.pack(fill="both", expand=True, padx=self.S(14))
        text = tk.Text(frame, wrap="none", font=tkfont.nametofont("TkFixedFont"), bg=WHITE, fg=INK, relief="flat",
                       highlightthickness=1, highlightbackground=CONTROL_EDGE, highlightcolor=INK, padx=8, pady=6)
        bar = ttk.Scrollbar(frame, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        text.pack(side="left", fill="both", expand=True)
        text.insert("1.0", script)
        text.configure(state="disabled")

        def copy_all():
            self.root.clipboard_clear()
            self.root.clipboard_append(script)
            self.status_var.set("Copied the commands to the clipboard.")

        def save_as():
            name = filedialog.asksaveasfilename(parent=win, title="Save commands", defaultextension=".txt",
                                                filetypes=[("Text", "*.txt"), ("PowerShell", "*.ps1"), ("All files", "*.*")])
            if name:
                Path(name).write_text(script, encoding="utf-8")
                self.status_var.set(f"Saved the commands to {Path(name).name}.")

        row = tk.Frame(win, bg=PANEL)
        row.pack(fill="x", padx=self.S(14), pady=self.S(10))
        ttk.Button(row, text="Copy all", style="Accent.TButton", command=copy_all).pack(side="left")
        ttk.Button(row, text="Save as…", command=save_as).pack(side="left", padx=6)
        ttk.Button(row, text="Close", style="Quiet.TButton", command=win.destroy).pack(side="right")
        win.bind("<Escape>", lambda _e: win.destroy())
        text.focus_set()

    # ── saving: validate, write both files in one transaction, then swap ───
    def _workspace(self) -> Path:
        import os

        paths = [self.calendar_path.resolve().parent, self.config_path.resolve().parent]
        try:
            return Path(os.path.commonpath([str(p) for p in paths]))
        except ValueError:  # different drives
            return paths[1]

    def _write(self, config: dict[str, Any] | None, state: CalendarState | None, what: str) -> None:
        """Write the given files atomically: all of them, or none."""
        store = CoordinatedStore(self._workspace())
        store.begin(description=what)
        try:
            if state is not None:
                target = self.calendar_path.resolve()
                target.parent.mkdir(parents=True, exist_ok=True)
                store.stage(target, json.dumps(state.to_dict(), indent=2) + "\n", as_json=False)
            if config is not None:
                target = self.config_path.resolve()
                target.parent.mkdir(parents=True, exist_ok=True)
                store.stage(target, json.dumps(config, ensure_ascii=False, indent=2) + "\n", as_json=False)
            store.commit()
        except BaseException:
            if store.is_active():
                store.rollback()
            raise

    def _commit(self, mutate: Callable[[dict, CalendarState], Any], what: str, *, timetables: bool = True) -> Any:
        """Apply ``mutate`` to copies, validate, save, and only then adopt the result.

        Nothing in memory or on disk changes unless the whole edit is valid and
        written. ``timetables=True`` also rebuilds the calendar's generated
        timetable rules from the configuration and saves both files together.
        """
        config, state = copy.deepcopy(self.config), self.state.copy()
        result = mutate(config, state)
        if timetables:
            validate_config(config)
            legacy = {r.id for r in state.rules if r.id.startswith(LEGACY_PREFIX)}
            state.rules = [r for r in state.rules if r.id not in legacy]
            state.exceptions = [x for x in state.exceptions if x.rule_id not in legacy]
            apply_timetables(config, state)
        state._validate()
        self._write(config if timetables else None, state, what)
        self._undo.append((self.state, self.config))
        del self._undo[:-30]
        self.state, self.config = state, config
        self._refresh_sidebar()
        return result

    def _saved(self, what: str):
        self.status_var.set(f"{what}  ·  saved {datetime.now():%H:%M}  ·  Ctrl+Z to undo")

    def _undo_last(self):
        if not self._undo:
            self.status_var.set("Nothing to undo.")
            return
        state, config = self._undo[-1]
        try:
            self._write(config, state, "undo")
        except Exception as exc:
            self.status_var.set(f"Couldn’t save the undo: {exc}")
            return
        self._undo.pop()
        self.state, self.config = state, config
        self._close_panel()
        self.status_var.set(f"Undone  ·  saved {datetime.now():%H:%M}")

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
        if self.root.focus_get() is self.body:
            self._grid_focus_in()

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

    # ── lookups ────────────────────────────────────────────────────────────
    def _group(self, group_id: str, config: dict | None = None) -> dict | None:
        return next((g for g in (config or self.config)["groups"] if g["id"] == group_id), None)

    def _tt(self, tid: str | None, config: dict | None = None) -> dict | None:
        return next((t for t in (config or self.config)["timetables"] if t["id"] == tid), None)

    def _group_name(self, group_id: str) -> str:
        group = self._group(group_id)
        return group["name"] if group else group_id

    def _parse_generated(self, rule_id: str, config: dict | None = None):
        """Map a generated rule id back to (timetable, slot, break) in the configuration."""
        if not rule_id.startswith(GROUP_PREFIX):
            return None
        rest = rule_id[len(GROUP_PREFIX):]
        for tt in sorted((config or self.config)["timetables"], key=lambda t: -len(t["id"])):
            head = tt["id"] + "__"
            if not rest.startswith(head):
                continue
            tail = rest[len(head):]
            act = next((a for a in tt.get("activities", []) if a["id"] == tail), None)
            if act:
                return tt, act, None
            brk = next((b for b in tt.get("breaks", []) if tail.startswith(b.get("id", "break") + "__")), None)
            return tt, None, brk
        return None

    # ── block collection ───────────────────────────────────────────────────
    def _collect(self, start: date, end: date) -> list[Block]:
        blocks: list[Block] = []
        rules = {r.id: r for r in self.state.rules}
        for item in self.state.resolver().build_actual_schedule(start, end):
            rule = rules.get(item.activity_id) if item.activity_id else None
            if rule is None:
                rule = next((r for r in self.state.rules if r.name == item.name), None)
            parsed = self._parse_generated(rule.id) if rule else None
            if parsed:
                tt, act, brk = parsed
                if brk is not None:
                    continue  # breaks are drawn from the timetable layout below
                if act is not None:
                    blocks.append(Block("slot", item.start, item.end, act["name"],
                                        act.get("priority") or rule.priority.value, rule, tt, act,
                                        int(act.get("period") or 0)))
                    continue
            blocks.append(Block("custom", item.start, item.end, item.name,
                                rule.priority.value if rule else "NORMAL", rule))

        day = start
        while day <= end:
            code = DAYS[day.weekday()]
            midnight = datetime.combine(day, datetime.min.time())
            for tt in self.config["timetables"]:
                if code not in tt.get("days", []):
                    continue
                try:
                    layout = slot_layout(tt)
                except (KeyError, ValueError):
                    continue
                for entry in layout:
                    s, e = midnight + timedelta(minutes=entry["start"]), midnight + timedelta(minutes=entry["end"])
                    if entry["kind"] == "break":
                        blocks.append(Block("break", s, e, entry["name"], "NORMAL", tt=tt))
                    elif self.show_free.get():
                        taken = any(a.get("day") == code and int(a.get("period", 0)) == entry["period"]
                                    for a in tt.get("activities", []))
                        if not taken:
                            blocks.append(Block("ghost", s, e, "Free slot", "NORMAL", tt=tt, period=entry["period"]))
            day += timedelta(days=1)
        return blocks

    def _overlap_note(self, day: date, s: int, e: int, ignore_rule_id: str | None) -> str:
        """Plain-language overlaps for one occurrence. Overlaps are allowed, so this only informs."""
        names = []
        for b in self._collect(day, day):
            if b.kind not in ("custom", "slot") or b.start.date() != day:
                continue
            if ignore_rule_id and b.rule is not None and b.rule.id == ignore_rule_id:
                continue
            if b.start_min < e and s < b.end_min:
                names.append(f"{b.name} {_clock(b.start_min)}–{_clock(b.end_min)}")
        if not names:
            return ""
        more = f" and {len(names) - 3} more" if len(names) > 3 else ""
        return "Overlaps " + "; ".join(names[:3]) + more + "."

    # ── drawing ────────────────────────────────────────────────────────────
    def _schedule_redraw(self):
        if self._redraw_job:
            self.root.after_cancel(self._redraw_job)
        self._redraw_job = self.root.after(30, self._redraw)

    def _canvas_width(self, canvas: tk.Canvas) -> int:
        return max(canvas.winfo_width() - 2 * int(float(canvas.cget("highlightthickness"))), 400)

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
        width = self._canvas_width(c)
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
                              font=self.f_small, fill=INK)
        elif b.kind == "ghost":
            c.create_rectangle(x0, y0, x1, y1, fill=PAPER, outline=INK if selected else CONTROL_EDGE,
                               dash=() if selected else (3, 3), width=2 if selected else 1)
            if y1 - y0 > S(18):
                c.create_text(x0 + S(8), y0 + S(3), anchor="nw", text=self._fit(f"Slot {b.period}", self.f_small, int(x1 - x0 - S(12))),
                              font=self.f_small, fill=INK_SOFT)
        else:
            body = WHITE if b.kind == "slot" else fill
            c.create_rectangle(x0, y0, x1, y1, fill=body, outline=INK if selected else (CONTROL_EDGE if b.kind == "slot" else fill),
                               width=2 if selected else 1)
            c.create_rectangle(x0, y0, x0 + S(4), y1, fill=edge, outline="")
            room = int(x1 - x0 - S(16))
            c.create_text(x0 + S(10), y0 + S(3), anchor="nw", text=self._fit(b.name, self.f_block, room),
                          font=self.f_block, fill=INK)
            if y1 - y0 >= S(38):
                # priority is also written out, so it never depends on colour alone
                # (the word comes first so a narrow column cuts the time, not the priority)
                detail = f"{_clock(b.start_min)}–{_clock(b.end_min)}"
                if b.priority != "NORMAL":
                    detail = f"{PRIORITY_LABEL.get(b.priority, b.priority)}  {detail}"
                c.create_text(x0 + S(10), y0 + S(19), anchor="nw", font=self.f_small, fill=INK_SOFT,
                              text=self._fit(detail, self.f_small, room))
            if y1 - y0 >= S(54) and b.act and b.act.get("group_ids"):
                names = ", ".join(self._group_name(g) for g in b.act["group_ids"])
                c.create_text(x0 + S(10), y0 + S(34), anchor="nw", font=self.f_small, fill=INK_SOFT,
                              text=self._fit(names, self.f_small, room))
        self.hits.append((x0, y0, x1, y1, b))

    def _draw_month(self):
        S = self.S
        c, h = self.body, self.head
        c.delete("all")
        h.delete("all")
        width = self._canvas_width(c)
        height = max(c.winfo_height() - 4, 300)
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
                if d == self.anchor:
                    c.create_rectangle(x0 + 1, y0 + 1, x0 + colw - 1, y0 + rowh - 1, outline=INK, width=2)
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

    # ── grid interaction (pointer) ─────────────────────────────────────────
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
        gutter, width = self.S(58), self._canvas_width(self.body)
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

    # ── grid interaction (keyboard) ────────────────────────────────────────
    def _grid_focus_in(self):
        if self.view.get() == "Month":
            self.status_var.set("Month grid: arrow keys move between days, Enter opens that week.")
        else:
            self.status_var.set("Week grid: ↑ ↓ move between entries, ← → jump to the same time on the next day, Esc leaves.")

    def _nav_blocks(self) -> list[Block]:
        order = {"slot": 0, "custom": 0, "ghost": 1}
        return sorted((b for b in self.blocks if b.kind in order),
                      key=lambda b: (b.start, b.end, order[b.kind], b.name))

    def _selected_block(self) -> Block | None:
        return next((b for b in self.blocks if b.key == self.sel_key), None) if self.sel_key else None

    def _grid_key(self, step: tuple[str, int]):
        """Arrow keys on the focused grid. Returns "break" when the key was used."""
        kind, delta = step
        if self.view.get() == "Month":
            self._set_anchor(self.anchor + timedelta(days={("move", -1): -7, ("move", 1): 7, ("hop", -1): -1, ("hop", 1): 1}[step]))
            return "break"
        current = self._selected_block()
        blocks = self._nav_blocks()
        if current is None:
            if kind == "move" and blocks:
                self._select_block(blocks[0] if delta > 0 else blocks[-1], focus=False)
                return "break"
            return None  # ← → with nothing selected change the week
        if kind == "move":
            index = next((i for i, b in enumerate(blocks) if b.key == current.key), 0)
            self._select_block(blocks[max(0, min(len(blocks) - 1, index + delta))], focus=False)
            return "break"
        wanted = current.start.date() + timedelta(days=delta)
        near = [b for b in blocks if b.start.date() == wanted]
        if near:
            self._select_block(min(near, key=lambda b: abs(b.start_min - current.start_min)), focus=False)
        return "break"

    def _grid_activate(self):
        if self.view.get() == "Month":
            self.view.set("Week")
            return "break"
        current = self._selected_block()
        if current:
            self._select_block(current, focus=True)
        return "break"

    # ── selection → panels ─────────────────────────────────────────────────
    def _reset_context(self):
        self.edit_rule_id = None
        self.slot_ctx = None
        self.tt_id = None
        self.group_sel = None

    def _select_block(self, b: Block, focus: bool = True):
        self.sel_key = b.key
        self.draft = None
        if b.kind == "custom" and b.rule:
            self._open_event(b, focus)
        elif b.kind in ("slot", "ghost"):
            self._open_slot(b, focus)
        elif b.kind == "break":
            if b.tt:
                self._open_timetable(b.tt["id"], focus, keep_selection=True)
            else:
                self._close_panel()
        else:
            self.status_var.set("That entry can’t be matched to a rule, so it can’t be edited here.")
            self._show_panel("empty")
        self._redraw()
        if not focus:
            self._reveal(b)

    def _reveal(self, b: Block):
        """Scroll the week grid so a keyboard-selected entry is on screen."""
        if self.view.get() == "Month":
            return
        try:
            total = float(str(self.body.cget("scrollregion")).split()[3])
        except (IndexError, ValueError):
            return
        top_y = self.S(10) + (b.start_min - self.hour_range[0] * 60) / 60 * self.S(52)
        bottom_y = self.S(10) + (b.end_min - self.hour_range[0] * 60) / 60 * self.S(52)
        view_top = self.body.canvasy(0)
        view_bottom = self.body.canvasy(self.body.winfo_height())
        if total > 0 and (top_y < view_top + self.S(8) or bottom_y > view_bottom - self.S(8)):
            self.body.yview_moveto(max(0.0, (top_y - self.S(60)) / total))

    def _new_event(self):
        day = self.anchor
        now = datetime.now()
        a = (now.hour + 1) * 60 if day == date.today() and now.hour < 22 else 9 * 60
        self._open_new_event(day, a, a + 60)

    def _open_new_event(self, day: date, a: int, z: int):
        self._reset_context()
        self.draft = (day, a, z)
        self.sel_key = None
        self.ev_title.set("New event")
        self.ev_name.set("")
        self.ev_date.set(day.isoformat())
        self.ev_start.set(_clock(a))
        self.ev_end.set(_clock(z))
        self.ev_repeat.set("Once")
        self.ev_priority.set("NORMAL")
        self.ev_warn.set("")
        self.ev_error.set("")
        self.ev_delete.state(["disabled"])
        self._update_hint()
        self._show_panel("event")
        self.ev_name_entry.focus_set()
        self._refresh_sidebar()
        self._redraw()

    def _open_event(self, b: Block, focus: bool = True):
        rule = b.rule
        self._reset_context()
        self.edit_rule_id = rule.id
        self.ev_title.set("Edit event")
        self.ev_name.set(rule.name)
        self.ev_date.set(b.start.date().isoformat())
        self.ev_start.set(_clock(b.start_min))
        self.ev_end.set(_clock(b.end_min))
        kind = rule.recurrence.type
        self.ev_repeat.set("Once" if kind == RecurrenceType.NONE else "Every day" if kind == RecurrenceType.DAILY else "Every week")
        self.ev_priority.set(rule.priority.value)
        self.ev_warn.set("")
        self.ev_error.set("")
        self.ev_delete.state(["!disabled"])
        self._update_hint()
        if kind != RecurrenceType.NONE:
            self.ev_hint.set(self.ev_hint.get() + " Changes apply to every occurrence.")
        self._show_panel("event")
        if focus:
            self.ev_name_entry.focus_set()
        self._refresh_sidebar()

    def _open_slot(self, b: Block, focus: bool = True):
        tt = b.tt
        if tt is None:
            self.status_var.set("This slot’s timetable could not be found in the timetable rules.")
            self._show_panel("empty")
            return
        act = b.act or {}
        code = DAYS[b.start.weekday()]
        self._reset_context()
        self.slot_ctx = {"tid": tt["id"], "day": code, "period": b.period, "act_id": act.get("id"), "date": b.start.date()}
        self.sl_title.set(f"{tt['name']}  ·  slot {b.period}")
        self.sl_sub.set(f"{DAY_LONG[b.start.weekday()]}, repeats every week")
        self.sl_name.set(act.get("name", ""))
        self.sl_start.set(_clock(b.start_min))
        self.sl_end.set(_clock(b.end_min))
        self.sl_priority.set(act.get("priority", ""))
        self.sl_warn.set("")
        self.sl_error.set("")
        self.sl_clear.state(["!disabled"] if act else ["disabled"])
        self.sl_groups = list(act.get("group_ids", []))
        self._refresh_slot_groups()
        self._show_panel("slot")
        if focus:
            self.sl_name_entry.focus_set()
        self._refresh_sidebar()

    def _refresh_slot_groups(self):
        """List the groups this slot belongs to (each removable) and offer the rest."""
        for child in self.sl_groups_box.winfo_children():
            child.destroy()
        if not self.sl_groups:
            tk.Label(self.sl_groups_box, text="Not in any group.", font=self.f_small, bg=PANEL, fg=INK_SOFT,
                     anchor="w").pack(fill="x")
        for gid in self.sl_groups:
            row = tk.Frame(self.sl_groups_box, bg=PANEL)
            row.pack(fill="x", pady=1)
            tk.Label(row, text=self._group_name(gid), font=self.f_ui, bg=PANEL, fg=INK, anchor="w").pack(
                side="left", fill="x", expand=True)
            ttk.Button(row, text="Remove", style="Quiet.TButton", command=lambda g=gid: self._slot_remove_group(g)).pack(side="right")
        available = [g for g in self.config["groups"] if g["id"] not in self.sl_groups]
        self._group_choice = {f"{g['name']}  ({g['id']})": g["id"] for g in available}
        self.sl_group_combo["values"] = list(self._group_choice)
        self.sl_group_var.set(next(iter(self._group_choice), ""))

    def _slot_add_group(self):
        gid = self._group_choice.get(self.sl_group_var.get())
        if not gid:
            self.sl_error.set("There is no other group to add. Create one under Groups on the left.")
            return
        self.sl_error.set("")
        self.sl_groups.append(gid)
        self._slot_groups_changed(f"Added to {self._group_name(gid)}")

    def _slot_remove_group(self, gid: str):
        if gid in self.sl_groups:
            self.sl_groups.remove(gid)
        self.sl_error.set("")
        self._slot_groups_changed(f"Removed from {self._group_name(gid)}")

    def _slot_groups_changed(self, message: str):
        self._refresh_slot_groups()
        if self.slot_ctx and self.slot_ctx.get("act_id"):
            self._save_slot()       # an existing subject saves straight away
        else:
            self.status_var.set(f"{message}. It is saved together with the slot’s subject.")

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
            priority = Priority(self.ev_priority.get())
            edit_id = self.edit_rule_id

            def mutate(_config, state):
                existing = next((r for r in state.rules if r.id == edit_id), None) if edit_id else None
                if existing and existing.id.startswith((GROUP_PREFIX, LEGACY_PREFIX)):
                    raise ValueError("This belongs to a timetable. Edit it from its slot.")
                if existing:
                    existing.name, existing.priority = name, priority
                    existing.recurrence, existing.time_constraints = recurrence, constraints
                    return existing.id
                prefix = {"Once": "custom__", "Every day": "custom_daily__"}.get(repeat, "custom_weekly__")
                stamp, n = target.isoformat().replace("-", ""), 1
                taken = {r.id for r in state.rules}
                while f"{prefix}{stamp}__{n}" in taken:
                    n += 1
                rule_id = f"{prefix}{stamp}__{n}"
                state.rules.append(ActivityRule(
                    id=rule_id, name=name, flexibility=Flexibility.FIXED, priority=priority,
                    recurrence=recurrence, time_constraints=constraints))
                return rule_id

            rule_id = self._commit(mutate, f"Saved {name}", timetables=False)
        except Exception as exc:
            self.ev_error.set(str(exc))
            return
        self.edit_rule_id = rule_id
        self.draft = None
        self.ev_title.set("Edit event")
        self.ev_error.set("")
        self.ev_delete.state(["!disabled"])
        self._set_anchor(target)
        self.ev_warn.set(self._overlap_note(target, s, e, rule_id))
        self._saved(f"Saved “{name}”")

    def _delete_event(self):
        rule_id = self.edit_rule_id
        rule = next((r for r in self.state.rules if r.id == rule_id), None)
        if not rule or rule.id.startswith((GROUP_PREFIX, LEGACY_PREFIX)):
            return

        def mutate(_config, state):
            state.rules = [r for r in state.rules if r.id != rule_id]
            state.exceptions = [x for x in state.exceptions if x.rule_id != rule_id]

        try:
            self._commit(mutate, f"Deleted {rule.name}", timetables=False)
        except Exception as exc:
            self.ev_error.set(str(exc))
            return
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
            groups, priority = list(self.sl_groups), self.sl_priority.get()
            tid, day, period, act_id = ctx["tid"], ctx["day"], ctx["period"], ctx.get("act_id")

            def mutate(config, _state):
                tt = self._tt(tid, config)
                if tt is None:
                    raise ValueError("That timetable no longer exists.")
                activities = tt.setdefault("activities", [])
                act = next((a for a in activities if a["id"] == act_id), None) if act_id else None
                if act is None:
                    act = next((a for a in activities if a["day"] == day and int(a.get("period", 0)) == period), None)
                if act is None:
                    taken = {a["id"] for a in activities}
                    new_id, n = f"{day.lower()}_p{period}", 2
                    while new_id in taken:
                        new_id, n = f"{day.lower()}_p{period}_{n}", n + 1
                    act = {"id": new_id, "day": day, "period": period}
                    activities.append(act)
                act.update({"name": name, "start_time": _clock(s), "end_time": _clock(e), "group_ids": groups})
                if priority:
                    act["priority"] = priority
                else:
                    act.pop("priority", None)
                return act["id"]

            aid = self._commit(mutate, f"Saved {name}")
        except Exception as exc:
            self.sl_error.set(str(exc))
            return
        midnight = datetime.combine(ctx["date"], datetime.min.time())
        self.sel_key = ("slot", (midnight + timedelta(minutes=s)).isoformat(), name)
        ctx["act_id"] = aid
        self.sl_error.set("")
        self.sl_clear.state(["!disabled"])
        self._refresh_slot_groups()
        self.sl_warn.set(self._overlap_note(ctx["date"], s, e, f"{GROUP_PREFIX}{tid}__{aid}"))
        self._redraw()
        self._saved(f"Saved {name} on {DAY_SHORT[DAYS.index(day)]}")

    def _clear_slot(self):
        ctx = self.slot_ctx
        if not ctx or not ctx.get("act_id"):
            return
        tid, act_id = ctx["tid"], ctx["act_id"]

        def mutate(config, _state):
            tt = self._tt(tid, config)
            if tt is None:
                raise ValueError("That timetable no longer exists.")
            tt["activities"] = [a for a in tt.get("activities", []) if a["id"] != act_id]

        try:
            self._commit(mutate, "Cleared a slot")
        except Exception as exc:
            self.sl_error.set(str(exc))
            return
        self._close_panel()
        self.status_var.set("Cleared the subject  ·  Ctrl+Z to undo")

    def _open_timetable_from_slot(self):
        if self.slot_ctx:
            self._open_timetable(self.slot_ctx["tid"])

    # ── timetable rules ────────────────────────────────────────────────────
    def _open_timetable(self, tid: str, focus: bool = True, keep_selection: bool = False):
        tt = self._tt(tid)
        if not tt:
            return
        self._reset_context()
        if not keep_selection:
            self.sel_key = None
        self.draft = None
        self.tt_id = tid
        self.tt_name.set(tt.get("name", ""))
        self.tt_id_var.set(tt["id"])
        self.tt_desc.set(tt.get("description", ""))
        self.tt_place.set(tt.get("place_tag", ""))
        for d in DAYS:
            self.tt_days[d].set(d in tt.get("days", []))
        self.tt_start.set(tt["start_time"])
        self.tt_end.set(tt["end_time"])
        self.tt_period.set(str(tt.get("period_minutes", 45)))
        self._load_break_rows(tt.get("breaks", []))
        self.tt_error.set("")
        hidden = [a for a in tt.get("activities", []) if a.get("day") not in tt.get("days", [])]
        self.tt_warn.set(f"{len(hidden)} slot subject{'s' if len(hidden) != 1 else ''} fall on days that are off, so they are hidden." if hidden else "")
        self._show_panel("timetable")
        self._refresh_sidebar()
        if focus:
            self.tt_name_entry.focus_set()
        self._redraw()

    def _load_break_rows(self, breaks: list[dict[str, Any]]):
        for row in self._break_rows:
            row["frame"].destroy()
        self._break_rows = []
        for brk in breaks:
            self._add_break_row(brk)

    def _add_break_row(self, brk: dict[str, Any] | None = None):
        brk = brk or {}
        frame = tk.Frame(self.tt_breaks_box, bg=PANEL, highlightthickness=1, highlightbackground=RULE_STRONG, padx=8, pady=6)
        frame.pack(fill="x", pady=(0, 6))
        row = {
            "frame": frame,
            "extra": {k: v for k, v in brk.items() if k not in ("name", "start_time", "end_time")},
            "name": tk.StringVar(value=brk.get("name", "")),
            "start": tk.StringVar(value=brk.get("start_time", "")),
            "end": tk.StringVar(value=brk.get("end_time", "")),
        }
        self._field(frame, "Break name", row["name"], submit=self._save_timetable)
        pair = self._pair(frame)
        self._field(pair, "Starts", row["start"], side="first", submit=self._save_timetable)
        self._field(pair, "Ends", row["end"], side="last", submit=self._save_timetable)
        ttk.Button(frame, text="Remove break", style="Quiet.TButton", command=lambda r=row: self._remove_break_row(r)).pack(anchor="w")
        self._break_rows.append(row)

    def _remove_break_row(self, row: dict[str, Any]):
        row["frame"].destroy()
        self._break_rows = [r for r in self._break_rows if r is not row]

    def _read_breaks(self, day_start: int, day_end: int) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        spans: list[tuple[int, int, str]] = []
        taken = {r["extra"]["id"] for r in self._break_rows if r["extra"].get("id")}
        for row in self._break_rows:
            name, bs, be = (row[k].get().strip() for k in ("name", "start", "end"))
            if not (name or bs or be):
                continue
            if not (name and bs and be):
                raise ValueError("A break needs a name, a start and an end. Fill in all three, or remove the break.")
            s, e = parse_time(bs), parse_time(be)
            if s >= e:
                raise ValueError(f"“{name}” has to end after it starts.")
            if s < day_start or e > day_end:
                raise ValueError(f"“{name}” has to fall inside the day ({_clock(day_start)}–{_clock(day_end)}).")
            item = dict(row["extra"])
            if not item.get("id"):
                item["id"] = unique_id("break", taken)
                taken.add(item["id"])
            item.update({"name": name, "start_time": _clock(s), "end_time": _clock(e)})
            item.setdefault("priority", "HIGH")
            item.setdefault("override_policy", "never")
            out.append(item)
            spans.append((s, e, name))
        spans.sort()
        for (_, end_a, name_a), (start_b, _, name_b) in zip(spans, spans[1:]):
            if start_b < end_a:
                raise ValueError(f"“{name_a}” and “{name_b}” overlap.")
        return sorted(out, key=lambda b: _minutes(b["start_time"]))

    def _save_timetable(self):
        tid = self.tt_id
        if not tid:
            return
        try:
            name = self.tt_name.get().strip()
            if not name:
                raise ValueError("Give the timetable a name.")
            new_id = clean_id(self.tt_id_var.get(), "timetable")
            days = [d for d in DAYS if self.tt_days[d].get()]
            if not days:
                raise ValueError("Pick at least one active day.")
            start, end = parse_time(self.tt_start.get()), parse_time(self.tt_end.get())
            if start >= end:
                raise ValueError("The day has to end after it starts.")
            try:
                period = int(self.tt_period.get().strip())
            except ValueError:
                raise ValueError("Slot length is a whole number of minutes.") from None
            if not 1 <= period <= 1440:
                raise ValueError("Slot length must be between 1 and 1440 minutes.")
            breaks = self._read_breaks(start, end)
            description, place = self.tt_desc.get().strip(), self.tt_place.get().strip()

            def mutate(config, _state):
                tt = self._tt(tid, config)
                if tt is None:
                    raise ValueError("That timetable no longer exists.")
                if new_id != tid and self._tt(new_id, config):
                    raise ValueError("Another timetable already uses that ID.")
                tt.update({"id": new_id, "name": name, "description": description, "place_tag": place, "days": days,
                           "start_time": _clock(start), "end_time": _clock(end), "period_minutes": period, "breaks": breaks})
                return len([a for a in tt.get("activities", []) if a.get("day") not in days])

            hidden = self._commit(mutate, f"Saved {name}")
        except Exception as exc:
            self.tt_error.set(str(exc))
            return
        self.tt_id = new_id
        self.tt_error.set("")
        self.tt_warn.set(f"{hidden} slot subject{'s' if hidden != 1 else ''} fall on days that are off, so they are hidden." if hidden else "")
        self._load_break_rows(breaks)
        self._refresh_sidebar()
        self._redraw()
        self._saved(f"Saved {name}")

    def _delete_timetable(self):
        tt = self._tt(self.tt_id)
        if not tt:
            return
        count = len(tt.get("activities", []))
        if not messagebox.askyesno(
                "Delete timetable",
                f"Delete “{tt['name']}” and its {count} slot subject{'s' if count != 1 else ''}?\n\n"
                "Ctrl+Z undoes this until you close the app.", parent=self.root):
            return
        tid = tt["id"]

        def mutate(config, _state):
            config["timetables"] = [t for t in config["timetables"] if t["id"] != tid]

        try:
            self._commit(mutate, f"Deleted {tt['name']}")
        except Exception as exc:
            self.tt_error.set(str(exc))
            return
        self._close_panel()
        self.status_var.set(f"Deleted “{tt['name']}”  ·  Ctrl+Z to undo")

    def _add_school_template(self):
        if self._tt("school_week"):
            self._open_timetable("school_week")
            self.status_var.set("A School week timetable already exists, so it’s open instead.")
            return
        template = _school_template()

        def mutate(config, _state):
            for group in template["groups"]:
                if not self._group(group["id"], config):
                    config["groups"].append(group)
            config["timetables"].extend(template["timetables"])

        try:
            self._commit(mutate, "Added the School timetable")
        except Exception as exc:
            self.status_var.set(f"Couldn’t add the template: {exc}")
            return
        self._open_timetable("school_week")
        self._saved("Added the School timetable")

    def _add_blank_timetable(self):
        tid = unique_id("timetable", {t["id"] for t in self.config["timetables"]})
        number = tid.rsplit("_", 1)[1]

        def mutate(config, _state):
            config["timetables"].append({
                "id": tid, "name": f"Timetable {number}", "description": "", "place_tag": "",
                "days": ["MON", "TUE", "WED", "THU", "FRI"], "start_time": "09:00", "end_time": "17:00",
                "period_minutes": 60, "breaks": [], "activities": [],
            })

        try:
            self._commit(mutate, f"Added Timetable {number}")
        except Exception as exc:
            self.status_var.set(f"Couldn’t add a timetable: {exc}")
            return
        self._open_timetable(tid)
        self._saved(f"Added Timetable {number}")

    # ── groups ─────────────────────────────────────────────────────────────
    def _members_of(self, gid: str) -> list[tuple[str, str, str]]:
        out = []
        for tt in self.config["timetables"]:
            for act in tt.get("activities", []):
                if gid in act.get("group_ids", []):
                    out.append((tt["id"], act["id"],
                                f"{tt['name']}  ·  {DAY_SHORT[DAYS.index(act['day'])]} slot {act.get('period', '?')}  ·  {act['name']}"))
        return out

    def _open_group(self, gid: str, focus: bool = True):
        group = self._group(gid)
        if not group:
            return
        self._reset_context()
        self.sel_key = None
        self.draft = None
        self.group_sel = gid
        self.gp_name.set(group.get("name", ""))
        self.gp_id_var.set(group["id"])
        self.gp_desc.set(group.get("description", ""))
        self.gp_error.set("")
        members = self._members_of(gid)
        self._member_refs = [(t, a) for t, a, _ in members]
        self.gp_members.delete(0, tk.END)
        for _, _, label in members:
            self.gp_members.insert(tk.END, label)
        if not members:
            self.gp_members.insert(tk.END, "No slots yet. Add this group from a slot’s panel.")
        self.gp_members_lbl.configure(text=f"Members ({len(members)})")
        self._show_panel("group")
        self._refresh_sidebar()
        if focus:
            self.gp_name_entry.focus_set()
        self._redraw()

    def _jump_to_member(self):
        picked = self.gp_members.curselection()
        if not picked or picked[0] >= len(self._member_refs):
            return
        tid, aid = self._member_refs[picked[0]]
        tt = self._tt(tid)
        act = next((a for a in (tt or {}).get("activities", []) if a["id"] == aid), None)
        if not act:
            return
        target = week_start(self.anchor) + timedelta(days=DAYS.index(act["day"]))
        self.view.set("Week")
        self._set_anchor(target)
        block = next((b for b in self.blocks if b.kind == "slot" and b.tt and b.tt["id"] == tid
                      and b.act and b.act["id"] == aid and b.start.date() == target), None)
        if block:
            self._select_block(block, focus=False)
        else:
            self.status_var.set("That slot isn’t on the grid this week.")

    def _add_group(self):
        gid = unique_id("group", {g["id"] for g in self.config["groups"]})
        number = gid.rsplit("_", 1)[1]

        def mutate(config, _state):
            config["groups"].append({"id": gid, "name": f"New group {number}", "description": ""})

        try:
            self._commit(mutate, f"Added group {number}")
        except Exception as exc:
            self.status_var.set(f"Couldn’t add a group: {exc}")
            return
        self._open_group(gid)
        self._saved(f"Added New group {number}")

    def _save_group(self):
        gid = self.group_sel
        if not gid:
            return
        try:
            name = self.gp_name.get().strip()
            if not name:
                raise ValueError("Give the group a name.")
            new_id = clean_id(self.gp_id_var.get(), "group")
            description = self.gp_desc.get().strip()

            def mutate(config, _state):
                group = self._group(gid, config)
                if group is None:
                    raise ValueError("That group no longer exists.")
                if new_id != gid and self._group(new_id, config):
                    raise ValueError("Another group already uses that ID.")
                group.update({"id": new_id, "name": name, "description": description})
                if new_id != gid:  # keep every slot's membership pointing at the renamed group
                    for tt in config["timetables"]:
                        for act in tt.get("activities", []):
                            act["group_ids"] = [new_id if x == gid else x for x in act.get("group_ids", [])]

            self._commit(mutate, f"Saved {name}")
        except Exception as exc:
            self.gp_error.set(str(exc))
            return
        self.group_sel = new_id
        self.gp_error.set("")
        self._open_group(new_id, focus=False)
        self._saved(f"Saved {name}")

    def _delete_group(self):
        group = self._group(self.group_sel)
        if not group:
            return
        gid = group["id"]

        def mutate(config, _state):
            config["groups"] = [g for g in config["groups"] if g["id"] != gid]
            for tt in config["timetables"]:  # slots keep their subjects; only the label goes
                for act in tt.get("activities", []):
                    act["group_ids"] = [x for x in act.get("group_ids", []) if x != gid]

        try:
            self._commit(mutate, f"Deleted group {group['name']}")
        except Exception as exc:
            self.gp_error.set(str(exc))
            return
        self._close_panel()
        self.status_var.set(f"Deleted group “{group['name']}”. Its slots were kept  ·  Ctrl+Z to undo")

    # ── sidebar ────────────────────────────────────────────────────────────
    def _row(self, parent, title: str, subtitle: str, edge: str, active: bool, command: Callable[[], None]):
        bg = WHITE if active else PANEL
        row = tk.Frame(parent, bg=bg, padx=8, pady=5, cursor="hand2", takefocus=True, highlightthickness=2,
                       highlightbackground=INK if active else PANEL, highlightcolor=INK)
        row.pack(fill="x", pady=1)
        tk.Frame(row, bg=edge, width=4, height=self.S(26)).pack(side="left", padx=(0, 8))
        text = tk.Frame(row, bg=bg)
        text.pack(side="left", fill="x", expand=True)
        tk.Label(text, text=title, font=self.f_bold, bg=bg, fg=INK, anchor="w", justify="left",
                 wraplength=self.S(160)).pack(fill="x")
        if subtitle:
            tk.Label(text, text=subtitle, font=self.f_small, bg=bg, fg=INK_SOFT, anchor="w", justify="left",
                     wraplength=self.S(160)).pack(fill="x")
        for widget in (row, text, *row.winfo_children(), *text.winfo_children()):
            widget.bind("<Button-1>", lambda _e: command())
        row.bind("<Return>", lambda _e: command())
        row.bind("<space>", lambda _e: command())
        return row

    def _refresh_sidebar(self):
        for frame in (self.tt_rows, self.group_rows):
            for child in frame.winfo_children():
                child.destroy()
        if not self.config["timetables"]:
            tk.Label(self.tt_rows, text="No timetables yet. Use Add to start from the School template or a blank one.",
                     wraplength=self.S(200), justify="left", anchor="w", font=self.f_small, bg=PANEL, fg=INK_SOFT).pack(fill="x", pady=4)
        for tt in self.config["timetables"]:
            days = [d for d in DAYS if d in tt.get("days", [])]
            span = (f"{DAY_SHORT[DAYS.index(days[0])]}–{DAY_SHORT[DAYS.index(days[-1])]}" if len(days) > 1
                    else DAY_SHORT[DAYS.index(days[0])] if days else "No days")
            detail = f"{span}  {tt.get('start_time', '')}–{tt.get('end_time', '')}"
            if tt.get("place_tag"):
                detail += f"\n{tt['place_tag']}"
            self._row(self.tt_rows, tt["name"], detail, PRIORITY["NORMAL"][1], tt["id"] == self.tt_id,
                      lambda tid=tt["id"]: self._open_timetable(tid))
        if not self.config["groups"]:
            tk.Label(self.group_rows, text="No groups yet. A group labels slots that belong together, like Club or Exam.",
                     wraplength=self.S(200), justify="left", anchor="w", font=self.f_small, bg=PANEL, fg=INK_SOFT).pack(fill="x", pady=4)
        for group in self.config["groups"]:
            count = len(self._members_of(group["id"]))
            self._row(self.group_rows, group["name"], f"{count} slot{'s' if count != 1 else ''}", INK_SOFT,
                      group["id"] == self.group_sel, lambda gid=group["id"]: self._open_group(gid))

    def _tick(self):
        if self.view.get() == "Week":
            self._redraw()
        self.root.after(60000, self._tick)


def main(focus: str | None = None):
    try:  # sharp text on high-DPI Windows displays
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    root = tk.Tk()
    UnifiedCalendarGUI(root, focus=focus)
    root.mainloop()


if __name__ == "__main__":
    main_group_schedule()

def main_group_schedule() -> None:
    """Entry point for the group schedule editor."""
    import tkinter as tk
    from tkinter import messagebox
    root = tk.Tk()
    GroupScheduleEditor(root)
    root.mainloop()

# GroupScheduleEditor moved from cal_engine/groups/group_schedule_gui.py
# ---------------------------------------------------------------------------

class GroupScheduleEditor:
    """Tkinter editor for reusable groups and timetables.

    Groups are organizational metadata only (id, name, description); they never
    carry scheduling policy. Timetables hold the scheduling rules (days, times,
    slot length, place tag, slots/activities and breaks).
    """

    def __init__(self, root: tk.Tk, config_path: str | Path | None = None):
        from ..groups.group_schedule import export_to_calendar, load_config, save_config

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
        self.status_var = tk.StringVar()
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

    def _build(self) -> None:
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
            "<<ListboxSelect>>", lambda _e: self._load_timetable_fields()
        )
        ttk.Button(left, text="Add timetable", command=self.add_timetable).pack(
            fill="x", pady=2
        )
        ttk.Button(left, text="Apply timetable edits", command=self.apply_timetable).pack(
            fill="x", pady=2
        )
        ttk.Separator(left).pack(fill="x", pady=10)
        ttk.Label(right, text="Activities").pack(anchor="w")
        self.tree = ttk.Treeview(right, columns=("name", "day", "period", "start", "end"), show="headings")
        self.tree.heading("name", text="Name")
        self.tree.heading("day", text="Day")
        self.tree.heading("period", text="Period")
        self.tree.heading("start", text="Start")
        self.tree.heading("end", text="End")
        self.tree.column("name", width=200)
        self.tree.column("day", width=60)
        self.tree.column("period", width=60)
        self.tree.column("start", width=80)
        self.tree.column("end", width=80)
        self.tree.pack(fill="both", expand=True, pady=4)
        btns = ttk.Frame(right)
        btns.pack(fill="x")
        ttk.Button(btns, text="Add activity", command=self.add_activity).pack(side="left")
        ttk.Button(btns, text="Delete activity", command=self.delete_activity).pack(side="left", padx=4)
        ttk.Button(btns, text="Export to calendar", command=self.export).pack(side="right")

        # status bar
        bottom = ttk.Frame(self.root, padding=(10, 4))
        bottom.pack(fill="x", side="bottom")
        ttk.Label(bottom, textvariable=self.status_var, anchor="w").pack(fill="x")

    def _populate_groups(self) -> None:
        self.group_list.delete(0, tk.END)
        for g in self.config.get("groups", []):
            self.group_list.insert(tk.END, g.get("name", ""))

    def _populate_timetables(self) -> None:
        self.timetable_list.delete(0, tk.END)
        for t in self.config.get("timetables", []):
            self.timetable_list.insert(tk.END, t.get("name", ""))

    def _populate_activities(self) -> None:
        for item in self.tree.get_children():
            self.tree.delete(item)
        t = self._timetable()
        if not t:
            return
        for idx, a in enumerate(t.get("activities", [])):
            self.tree.insert("", "end", iid=str(idx), values=(
                a.get("name", ""),
                a.get("day", ""),
                a.get("period", ""),
                a.get("start_time", ""),
                a.get("end_time", ""),
            ))

    def _load_group_fields(self) -> None:
        selection = self.group_list.curselection()
        if not selection:
            return
        idx = selection[0]
        if idx < len(self.config.get("groups", [])):
            group = self.config["groups"][idx]
            self._status(f"Group: {group.get('name', '')}")

    def _load_timetable_fields(self) -> None:
        selection = self.timetable_list.curselection()
        if not selection:
            return
        idx = selection[0]
        if idx < len(self.config.get("timetables", [])):
            timetable = self.config["timetables"][idx]
            self._status(f"Timetable: {timetable.get('name', '')}")

    def add_group(self) -> None:
        name = tk.simpledialog.askstring("Add Group", "Group name:")
        if name:
            self.config.setdefault("groups", []).append({"id": str(len(self.config.get("groups", []))), "name": name})
            self._populate_groups()
            self._status(f"Added group '{name}'")

    def apply_group(self) -> None:
        self._status("Group edits applied (not saved yet)")

    def add_timetable(self) -> None:
        name = tk.simpledialog.askstring("Add Timetable", "Timetable name:")
        if name:
            self.config.setdefault("timetables", []).append({
                "id": str(len(self.config.get("timetables", []))),
                "name": name,
                "days": ["MON", "TUE", "WED", "THU", "FRI"],
                "start_time": "09:00",
                "end_time": "17:00",
                "period_minutes": 60,
                "breaks": [],
                "activities": [],
            })
            self._populate_timetables()
            self._status(f"Added timetable '{name}'")

    def apply_timetable(self) -> None:
        self._status("Timetable edits applied (not saved yet)")

    def open_config(self) -> None:
        from tkinter import filedialog
        name = filedialog.askopenfilename(
            title="Open group/timetable configuration",
            filetypes=[("JSON", "*.json"), ("All files", "*.*")],
        )
        if name:
            self.path_var.set(name)
            self.load()

    def load(self) -> None:
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

    def save(self) -> None:
        try:
            self.config_path = Path(self.path_var.get().strip())
            save_config(self.config, self.config_path)
            self._status(f"Saved {self.config_path}")
        except Exception as e:
            messagebox.showerror("Save failed", str(e))

    def add_activity(self) -> None:
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
            "start_time": t.get("start_time", ""),
            "end_time": t.get("end_time", ""),
            "group_ids": [],
        })
        self._populate_activities()
        self._status("Activity added; edit its fields.")

    def delete_activity(self) -> None:
        selected = self._selected_activity()
        t = self._timetable()
        if not selected or not t:
            return messagebox.showinfo("Activity", "Select an activity first.")
        idx, a = selected
        if messagebox.askyesno("Delete activity", f"Delete '{a.get('name')}'?"):
            del t["activities"][idx]
            self._populate_activities()
            self._status("Activity deleted (not saved yet).")

    def _selected_activity(self):
        selection = self.tree.selection()
        if not selection:
            return None, None
        idx = int(selection[0])
        t = self._timetable()
        if not t:
            return None, None
        idx = min(idx, len(t.get("activities", [])) - 1)
        a = t["activities"][idx]
        return idx, a

    def export(self) -> None:
        """Export timetable rules to calendar file."""
        try:
            from tkinter import filedialog
            path = filedialog.asksaveasfilename(
                title="Export / update calendar state",
                initialfile="data/calendar.json",
                defaultextension=".json",
            )
            if not path:
                return
            from ..groups.group_schedule import export_to_calendar
            count = export_to_calendar(self.config, path)
            self._status(f"Exported {count} timetable rules to {path}")
            messagebox.showinfo(
                "Export complete",
                f"Exported {count} timetable rules. Existing non-timetable rules were retained. Groups are organizational metadata only; scheduling policy lives on the timetable and its slots.",
            )
        except Exception as e:
            messagebox.showerror("Export failed", str(e))

    def _timetable(self):
        selection = self.timetable_list.curselection()
        if not selection:
            return None
        idx = selection[0]
        if idx < len(self.config.get("timetables", [])):
            return self.config["timetables"][idx]
        return None

    def _status(self, message: str) -> None:
        self.status_var.set(message) if hasattr(self, "status_var") else None
