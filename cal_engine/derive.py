"""Derived activities (v0.2b): prep-before / review-after generated from rules.

Derived instances are NEVER stored. They are recomputed from rules.json
(state) + relations.json on every run, which is exactly why re-running the
engine after you edit a rule "picks up the new thing" automatically.

`expand_occurrences` is a standalone fallback so this module works on plain
dict rules. If cal_engine already has a "build actual schedule" function that
honours recurrence + exceptions, feed its output in instead (same dict shape).
"""
from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta

from .availability import DAYS, to_min

_ISO_DUR = re.compile(r"^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?)?$")


def parse_duration(text: str) -> timedelta:
    """ISO-8601 subset: P1D, PT30M, PT1H30M, P1DT2H."""
    m = _ISO_DUR.match(text or "")
    if not m or not any(g is not None for g in m.groups()):
        raise ValueError(f"bad ISO-8601 duration: {text!r}")
    d, h, mi = (int(x or 0) for x in m.groups())
    return timedelta(days=d, hours=h, minutes=mi)


# ---------------------------------------------------------------- occurrences

def _on_day(rule: dict, d: date) -> bool:
    rec = rule.get("recurrence") or {"type": "NONE"}
    typ = rec.get("type", "NONE")
    day = DAYS[d.weekday()]
    vf, vu = rec.get("valid_from"), rec.get("valid_until")
    if vf and d < date.fromisoformat(vf):
        return False
    if vu and d > date.fromisoformat(vu):
        return False
    if typ == "DAILY":
        return day not in rec.get("except_days", [])
    if typ == "WEEKLY":
        return day in rec.get("days_of_week", [])
    if typ == "NONE":
        return (rec.get("date") or rule.get("date")) == d.isoformat()
    return False


def _window(rule: dict, d: date) -> tuple[int, int] | None:
    tc = rule.get("time_constraints") or {}
    day = DAYS[d.weekday()]
    w = (tc.get("day_time_windows") or {}).get(day) or tc.get("fixed_interval")
    if not w:
        return None
    return to_min(w["start_time"]), (to_min(w["end_time"]) or 1440)


def _apply_new_time(s: int, e: int, new_time: str) -> tuple[int, int]:
    if "-" in new_time:
        a, b = new_time.split("-", 1)
        return to_min(a), (to_min(b) or 1440)
    ns = to_min(new_time)
    return ns, ns + (e - s)


def expand_occurrences(rules: list[dict], start: date, end: date, exceptions=()) -> list[dict]:
    """Concrete, time-pinned occurrences of every rule in [start, end].
    Honours CANCEL / MOVE / MODIFY exceptions. Rules without a fixed time
    (flexible tasks) are skipped -- they are placed later, not anchored."""
    ex = {(e["rule_id"], e["target_date"]): e for e in exceptions}
    out: list[dict] = []
    d = start - timedelta(days=7)  # a MOVE can land inside the window from outside
    while d <= end + timedelta(days=7):
        for r in rules:
            if not _on_day(r, d):
                continue
            w = _window(r, d)
            if w is None:
                continue
            s, e = w
            occ_date = d
            x = ex.get((r["id"], d.isoformat()))
            if x:
                act = (x.get("action") or "").upper()
                if act == "CANCEL":
                    continue
                if act == "MOVE" and x.get("new_date"):
                    occ_date = date.fromisoformat(x["new_date"])
                if x.get("new_time"):
                    s, e = _apply_new_time(s, e, x["new_time"])
            if start <= occ_date <= end:
                out.append({
                    "occ_id": f"{r['id']}@{occ_date.isoformat()}",
                    "rule_id": r["id"],
                    "name": r.get("name", r["id"]),
                    "group": r.get("group") or r["id"],
                    "tags": list(r.get("tags", [])),
                    "date": occ_date,
                    "start": s,
                    "end": e,
                })
        d += timedelta(days=1)
    return sorted(out, key=lambda o: (o["date"], o["start"], o["rule_id"]))


# ------------------------------------------------------------------- relations

def matches(selector: dict, occ: dict) -> bool:
    if "match_ids" in selector and occ["rule_id"] not in selector["match_ids"]:
        return False
    if "match_group" in selector and occ["group"] != selector["match_group"]:
        return False
    if "match_tags" in selector and not set(selector["match_tags"]) <= set(occ["tags"]):
        return False
    return True


def _instances(rel: dict, occ: dict) -> list[dict]:
    base = datetime.combine(occ["date"], time.min)
    a_start = base + timedelta(minutes=occ["start"])
    a_end = base + timedelta(minutes=occ["end"])
    dur = {"min": 15, "ideal": 30, "max": 45, **(rel.get("duration") or {})}
    gap = rel.get("gap") or {}
    g0 = parse_duration(gap.get("min", "PT0M"))
    g1 = parse_duration(gap.get("max", "P1D"))
    common = {
        "rule_id": rel["id"],
        "anchor_id": occ["occ_id"],
        "anchor_rule": occ["rule_id"],
        "group": rel.get("group") or occ["group"],
        "tags": sorted(set(occ["tags"]) | set(rel.get("tags", [])) | {"derived"}),
        "flexibility": rel.get("flexibility", "FLEXIBLE"),
        "priority": rel.get("priority", "NORMAL"),
        "duration": dur,
    }
    kind = rel["relation"].upper()
    if kind == "BEFORE":  # derived.end in [anchor.start - gap.max, anchor.start - gap.min]
        return [{**common, "instance_id": f"{rel['id']}:{occ['occ_id']}", "relation": "BEFORE",
                 "near_edge": "end", "near_lo": a_start - g1, "near_hi": a_start - g0}]
    if kind == "AFTER":   # derived.start in [anchor.end + gap.min, anchor.end + gap.max]
        return [{**common, "instance_id": f"{rel['id']}:{occ['occ_id']}", "relation": "AFTER",
                 "near_edge": "start", "near_lo": a_end + g0, "near_hi": a_end + g1}]
    if kind == "AFTER_OFFSETS":  # one instance per offset, on that calendar day
        out = []
        for off in rel["offsets"]:
            day0 = datetime.combine((a_end + parse_duration(off)).date(), time.min)
            out.append({**common, "instance_id": f"{rel['id']}:{occ['occ_id']}+{off}",
                        "relation": f"AFTER+{off}", "near_edge": "start",
                        "near_lo": day0, "near_hi": day0 + timedelta(days=1) - timedelta(minutes=dur["min"])})
        return out
    raise ValueError(f"relation {rel['id']!r}: unknown relation {rel['relation']!r}")


def generate_derived(relations: list[dict], occurrences: list[dict]) -> tuple[list[dict], dict[str, int]]:
    """Returns (instances, anchors_matched_per_relation)."""
    out: list[dict] = []
    stats: dict[str, int] = {}
    for rel in relations:
        sel = rel.get("from") or {}
        if not any(k in sel for k in ("match_tags", "match_group", "match_ids")):
            raise ValueError(f"relation {rel['id']!r}: 'from' needs match_tags, match_group or match_ids")
        n = 0
        for occ in occurrences:
            if matches(sel, occ):
                n += 1
                out.extend(_instances(rel, occ))
        stats[rel["id"]] = n
    return out, stats


# ------------------------------------------------------------------ candidates

def candidate_slots(inst: dict, usable_by_date: dict[date, list[tuple[int, int]]], step: int = 15):
    """All (start, end) datetimes where the instance fits inside usable blocks
    and its anchor-side edge respects the relation window. Tries the ideal
    duration first and only falls back to the minimum if nothing fits."""
    dur = inst["duration"]
    for length in dict.fromkeys((dur["ideal"], dur["min"])):
        found = []
        for d, blocks in sorted(usable_by_date.items()):
            base = datetime.combine(d, time.min)
            for s, e in blocks:
                t = -(-s // step) * step
                while t + length <= e:
                    st = base + timedelta(minutes=t)
                    en = st + timedelta(minutes=length)
                    edge = en if inst["near_edge"] == "end" else st
                    if inst["near_lo"] <= edge <= inst["near_hi"]:
                        found.append((st, en))
                    t += step
        if found:
            return found
    return []
