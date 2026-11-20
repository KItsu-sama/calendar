"""Decompile: turn the current calendar state back into the commands that
would rebuild it .

!! The flag spellings in FLAGS are a GUESS. Only `--add`, `--fixed`,
!! `--daily`, `--time` and the README command list were visible to me.
!! Edit FLAGS to match your real grammar, then run roundtrip_check() with
!! YOUR parser: it tells you, rule by rule, what does not survive the trip.

`reference_parse` is the inverse of this module's own grammar. It is the
executable spec for what your parser must accept, and what the default
round-trip check uses until you plug in the real one.
"""
from __future__ import annotations

import re
import shlex

DAY_ORDER = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")

FLAGS = {
    "add": "--add", "id": "--id", "priority": "--priority",
    "fixed": "--fixed", "changeable": "--changeable",
    "flexible": "--flexible", "optional": "--optional",
    "daily": "--daily", "weekly": "--weekly", "once": "--once",
    "except_days": "--except", "valid_from": "--from", "valid_until": "--until",
    "time": "--time", "window": "--window", "allowed_days": "--allowed-days",
    "min_dur": "--min-duration", "max_dur": "--max-duration",
    "deadline": "--deadline", "move_to": "--move-to",
    "group": "--group", "tag": "--tag",
    # exceptions
    "exception": "--exception", "rule": "--rule", "date": "--date",
    "action": "--action", "new_date": "--new-date", "new_time": "--new-time",
}

KNOWN_RULE_KEYS = {"id", "name", "flexibility", "priority", "recurrence",
                   "time_constraints", "move_options", "group", "tags"}
KNOWN_TC_KEYS = {"fixed_interval", "day_time_windows", "allowed_windows", "allowed_days",
                 "min_duration_minutes", "max_duration_minutes", "deadline"}
KNOWN_REC_KEYS = {"type", "except_days", "days_of_week", "valid_from", "valid_until", "date"}


def _q(s: str) -> str:
    """Quote for PowerShell / cmd / bash alike (double quotes only when needed)."""
    s = str(s)
    return s if re.fullmatch(r"[\w.:,=\-/@+]+", s) else '"' + s.replace('"', '\\"') + '"'


def unsupported_fields(rule: dict) -> list[str]:
    """Fields the command grammar cannot express (they would be lost)."""
    bad = [k for k in rule if k not in KNOWN_RULE_KEYS]
    bad += [f"time_constraints.{k}" for k in (rule.get("time_constraints") or {}) if k not in KNOWN_TC_KEYS]
    bad += [f"recurrence.{k}" for k in (rule.get("recurrence") or {}) if k not in KNOWN_REC_KEYS]
    bad += [f"move_options.{k}" for k in (rule.get("move_options") or {}) if k != "preferred_days"]
    return bad


# ---------------------------------------------------------------- rule -> text

def rule_to_tokens(rule: dict) -> list[str]:
    F = FLAGS
    t = [F["add"], rule["name"], F["id"], rule["id"],
         F[rule["flexibility"].lower()], F["priority"], rule["priority"]]
    rec = rule.get("recurrence") or {"type": "NONE"}
    typ = rec.get("type", "NONE")
    if typ == "DAILY":
        t.append(F["daily"])
        if rec.get("except_days"):
            t += [F["except_days"], ",".join(rec["except_days"])]
    elif typ == "WEEKLY":
        t += [F["weekly"], ",".join(rec.get("days_of_week", []))]
    elif rec.get("date"):
        t += [F["once"], rec["date"]]
    if rec.get("valid_from"):
        t += [F["valid_from"], rec["valid_from"]]
    if rec.get("valid_until"):
        t += [F["valid_until"], rec["valid_until"]]

    tc = rule.get("time_constraints") or {}
    if tc.get("fixed_interval"):
        fi = tc["fixed_interval"]
        t += [F["time"], f"{fi['start_time']}-{fi['end_time']}"]
    for day in DAY_ORDER:
        w = (tc.get("day_time_windows") or {}).get(day)
        if w:
            t += [F["time"], f"{day}={w['start_time']}-{w['end_time']}"]
    for w in tc.get("allowed_windows") or []:
        t += [F["window"], f"{w['start_time']}-{w['end_time']}"]
    if tc.get("allowed_days"):
        t += [F["allowed_days"], ",".join(tc["allowed_days"])]
    if tc.get("min_duration_minutes") is not None:
        t += [F["min_dur"], str(tc["min_duration_minutes"])]
    if tc.get("max_duration_minutes") is not None:
        t += [F["max_dur"], str(tc["max_duration_minutes"])]
    if tc.get("deadline"):
        t += [F["deadline"], str(tc["deadline"])]
    mo = (rule.get("move_options") or {}).get("preferred_days")
    if mo:
        t += [F["move_to"], ",".join(mo)]
    if rule.get("group"):
        t += [F["group"], rule["group"]]
    for tag in rule.get("tags", []):
        t += [F["tag"], tag]
    return t


def rule_to_command(rule: dict, prefix: str = "python main.py") -> str:
    return " ".join([prefix] + [_q(x) if not x.startswith("--") else x for x in rule_to_tokens(rule)])


def exception_to_command(ex: dict, prefix: str = "python main.py") -> str:
    F = FLAGS
    t = [F["exception"], F["rule"], ex["rule_id"], F["date"], ex["target_date"], F["action"], ex["action"]]
    if ex.get("new_date"):
        t += [F["new_date"], ex["new_date"]]
    if ex.get("new_time"):
        t += [F["new_time"], ex["new_time"]]
    return " ".join([prefix] + [_q(x) if not x.startswith("--") else x for x in t])


def state_to_script(rules: list[dict], exceptions: list[dict] = (), state_path: str = "state.json",
                    digest: str | None = None, include_reset: bool = True) -> str:
    """One command per line; running the script on an empty calendar rebuilds
    the same rules + exceptions. Anything that cannot be expressed is called
    out in a WARNING comment instead of being silently dropped."""
    persist = f"--load {_q(state_path)} --save {_q(state_path)}"
    lines = ["# cal_engine export (decompiled from current state)"]
    if digest:
        lines.append(f"# state digest: {digest}")
    lines.append("# flag names follow decompile.FLAGS -- verify with roundtrip_check()")
    if include_reset:
        lines.append(f"python main.py --reset --save {_q(state_path)}")
    for r in rules:
        lost = unsupported_fields(r)
        if lost:
            lines.append(f"# WARNING: {r['id']} has fields the grammar cannot express: {', '.join(lost)}")
        lines.append(rule_to_command(r, prefix=f"python main.py {persist}"))
    for ex in exceptions:
        lines.append(exception_to_command(ex, prefix=f"python main.py {persist}"))
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- text -> rule

_BOOL = {FLAGS[k]: k.upper() for k in ("fixed", "changeable", "flexible", "optional")}


def reference_parse(command: str) -> dict:
    """Inverse of rule_to_command for this module's grammar."""
    toks = shlex.split(command)
    i = 0
    while i < len(toks) and not toks[i].startswith("--"):
        i += 1
    toks = toks[i:]
    F = FLAGS
    rule: dict = {}
    rec: dict = {"type": "NONE"}
    tc: dict = {}
    tags: list[str] = []
    i = 0

    def nxt() -> str:
        nonlocal i
        i += 1
        return toks[i]

    while i < len(toks):
        f = toks[i]
        if f in ("--load", "--save"):
            nxt()
        elif f == F["add"]:
            rule["name"] = nxt()
        elif f == F["id"]:
            rule["id"] = nxt()
        elif f in _BOOL:
            rule["flexibility"] = _BOOL[f]
        elif f == F["priority"]:
            rule["priority"] = nxt()
        elif f == F["daily"]:
            rec["type"] = "DAILY"
        elif f == F["weekly"]:
            rec = {**rec, "type": "WEEKLY", "days_of_week": nxt().split(",")}
        elif f == F["once"]:
            rec["date"] = nxt()
        elif f == F["except_days"]:
            rec["except_days"] = nxt().split(",")
        elif f == F["valid_from"]:
            rec["valid_from"] = nxt()
        elif f == F["valid_until"]:
            rec["valid_until"] = nxt()
        elif f == F["time"]:
            v = nxt()
            if "=" in v:
                day, span = v.split("=", 1)
                a, b = span.split("-")
                tc.setdefault("day_time_windows", {})[day] = {"start_time": a, "end_time": b}
            else:
                a, b = v.split("-")
                tc["fixed_interval"] = {"start_time": a, "end_time": b}
        elif f == F["window"]:
            a, b = nxt().split("-")
            tc.setdefault("allowed_windows", []).append({"start_time": a, "end_time": b})
        elif f == F["allowed_days"]:
            tc["allowed_days"] = nxt().split(",")
        elif f == F["min_dur"]:
            tc["min_duration_minutes"] = int(nxt())
        elif f == F["max_dur"]:
            tc["max_duration_minutes"] = int(nxt())
        elif f == F["deadline"]:
            tc["deadline"] = nxt()
        elif f == F["move_to"]:
            rule["move_options"] = {"preferred_days": nxt().split(",")}
        elif f == F["group"]:
            rule["group"] = nxt()
        elif f == F["tag"]:
            tags.append(nxt())
        else:
            raise ValueError(f"unknown flag in command: {f}")
        i += 1
    rule["recurrence"] = rec
    if tc:
        rule["time_constraints"] = tc
    if tags:
        rule["tags"] = tags
    return rule


# ------------------------------------------------------------------ round trip

def normalize(rule: dict) -> dict:
    """Canonical form so absent == empty (e.g. no recurrence == NONE)."""
    r = {k: v for k, v in rule.items() if v not in (None, [], {}, "")}
    r.setdefault("recurrence", {"type": "NONE"})
    return r


def roundtrip_check(rules: list[dict], parse=reference_parse) -> list[str]:
    """Export every rule, parse the command back with `parse`, and report any
    difference. Pass YOUR real parser (command string -> rule dict) to verify
    the FLAGS table against it. Empty list == lossless."""
    problems: list[str] = []
    for r in rules:
        lost = unsupported_fields(r)
        if lost:
            problems.append(f"{r['id']}: cannot express {', '.join(lost)}")
            continue
        cmd = rule_to_command(r)
        try:
            back = parse(cmd)
        except Exception as exc:  # parser rejected it: FLAGS mismatch
            problems.append(f"{r['id']}: parser rejected `{cmd}` ({exc})")
            continue
        a, b = normalize(r), normalize(back)
        if a != b:
            keys = sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))
            problems.append(f"{r['id']}: differs after round trip in {keys}")
    return problems
