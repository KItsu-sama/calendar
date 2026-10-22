"""Review + rerun (v0.2d-lite).

    python main.py --load state.json --review            # what changed + regenerated sections
    python main.py --load state.json --watch             # rerun whenever a source file is saved
    python main.py --load state.json --export-commands out.ps1

Why "rerun picks up new things" works: derived activities and usable blocks
are computed from source files on every run and never stored. --review also
keeps a snapshot (state.snapshot.json) so it can tell you exactly what you
added / removed / edited since the last run.
"""
from __future__ import annotations

import hashlib
import json
import os
import time as _time
from datetime import date, datetime, timedelta
from pathlib import Path

from .availability import DAYS, Profile, load_profile, to_hhmm
from .decompile import state_to_script
from .planner import plan_activities
from .derive import candidate_slots, expand_occurrences, generate_derived


# ------------------------------------------------------------------- loading

def _read_json(path) -> dict | list:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def extract_rules(state: dict) -> list[dict]:
    for key in ("rules", "activities", "activity_rules"):
        if key in state:
            return state[key]
    raise KeyError(f"state has no rule list; top-level keys are {sorted(state)} "
                   "(adjust extract_rules in review.py)")


def load_workspace(state_path, profile_path=None, relations_path=None) -> dict:
    state = _read_json(state_path)
    rel = []
    if relations_path and Path(relations_path).exists():
        data = _read_json(relations_path)
        rel = data["relations"] if isinstance(data, dict) else data
    return {
        "state": state,
        "rules": extract_rules(state),
        "exceptions": state.get("exceptions", []),
        "relations": rel,
        "profile_raw": _read_json(profile_path) if profile_path and Path(profile_path).exists() else {},
        "profile": load_profile(profile_path),
    }


# ----------------------------------------------------------------- snapshots

def digest(obj) -> str:
    blob = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _sections(ws: dict) -> dict[str, dict]:
    ex = {(e.get("id") or f"{e.get('rule_id')}@{e.get('target_date')}"): e for e in ws["exceptions"]}
    return {
        "rules": {r["id"]: r for r in ws["rules"]},
        "relations": {r["id"]: r for r in ws["relations"]},
        "exceptions": ex,
        "profile": {"profile": ws["profile_raw"]} if ws["profile_raw"] else {},
    }


def snapshot_path(state_path) -> Path:
    return Path(state_path).with_suffix(".snapshot.json")


def diff_sections(old: dict | None, new: dict) -> list[str]:
    if old is None:
        return ["(first run: no previous snapshot)"]
    lines: list[str] = []
    for sec in ("rules", "relations", "exceptions", "profile"):
        a, b = old.get(sec, {}), new.get(sec, {})
        for k in sorted(b.keys() - a.keys()):
            lines.append(f"+ {sec}: {k}")
        for k in sorted(a.keys() - b.keys()):
            lines.append(f"- {sec}: {k}")
        for k in sorted(a.keys() & b.keys()):
            if a[k] != b[k]:
                fields = sorted(f for f in set(a[k]) | set(b[k])
                                if isinstance(a[k], dict) and isinstance(b[k], dict) and a[k].get(f) != b[k].get(f))
                lines.append(f"~ {sec}: {k}" + (f" (changed: {', '.join(fields)})" if fields else ""))
    return lines or ["(no changes since last run)"]


# -------------------------------------------------------------------- review

def week_of(on: date) -> tuple[date, date]:
    start = on - timedelta(days=on.weekday())
    return start, start + timedelta(days=6)


def _fmt(m: int) -> str:
    return "24:00" if m >= 1440 else to_hhmm(m)


def run_review(state_path, profile_path=None, relations_path=None, on: date | None = None,
               save_snapshot: bool = True, horizon_days: int = 14) -> str:
    """Rebuild everything from source files and return prompt-ready text:
    [CHANGES SINCE LAST RUN] [USABLE FREE BLOCKS] [DERIVED ACTIVITIES]."""
    ws = load_workspace(state_path, profile_path, relations_path)
    profile: Profile = ws["profile"]
    start, end = week_of(on or date.today())
    horizon_end = end + timedelta(days=horizon_days)

    occ_all = expand_occurrences(ws["rules"], start, horizon_end, ws["exceptions"])
    # Generate derived activities from all relevant upcoming anchors.
    # A future event may require preparation during the current week.
    anchors = occ_all
    derived, matched = generate_derived(ws["relations"], anchors)

    usable: dict[date, list[tuple[int, int]]] = {}
    d = start
    while d <= horizon_end:
        busy = [(o["start"], o["end"]) for o in occ_all if o["date"] == d]
        usable[d] = profile.usable_blocks(DAYS[d.weekday()], busy)
        d += timedelta(days=1)

    # --- changes
    snap_file = snapshot_path(state_path)
    old = _read_json(snap_file)["items"] if snap_file.exists() else None
    new_items = _sections(ws)
    out = ["[CHANGES SINCE LAST RUN]", *diff_sections(old, new_items), ""]

    # --- usable blocks
    out.append("[USABLE FREE BLOCKS]")
    if profile.is_empty:
        out.append("(no profile.json: availability unknown, nothing can be treated as usable)")
    else:
        out.append(f"(gaps minus protected time minus {profile.buffer_min}-min buffers; "
                   f"blocks under {profile.min_block_min} min dropped)")
        d = start
        while d <= end:
            day = DAYS[d.weekday()]
            blocks = usable[d]
            soft = [f"{p.id} {_fmt(s)}-{_fmt(e)}" for s, e, p in profile.protected_for_day(day) if p.soft]
            out.append(f"{day} ({d.isoformat()})" + (f"  [soft-protected, avoid: {'; '.join(soft)}]" if soft else ""))
            out += [f"- {_fmt(s)} - {_fmt(e)} | usable" for s, e in blocks] or ["- (none)"]
            d += timedelta(days=1)
    out.append("")

    plan = None
    if not profile.is_empty:
        plan = plan_activities(
            derived,
            usable,
            max_minutes_per_day=profile.max_flexible_min_per_day,
        )

    # --- derived
    # --- derived activities and coordinated placement
    out.append("[DERIVED ACTIVITIES]")

    for rid, n in matched.items():
        if n == 0:
            out.append(
                f"WARNING: relation {rid!r} matched 0 anchors "
                "(check the tags, groups, or IDs selected by this relation)"
            )

    for inst in derived:
        dur = inst["duration"]
        n = (
            len(candidate_slots(inst, usable))
            if not profile.is_empty
            else None
        )
        cand = (
            "availability unknown"
            if n is None
            else ("NO CANDIDATE SLOT" if n == 0 else f"{n} candidate slots")
        )
        out.append(
            f"- {inst['instance_id']} | "
            f"group {inst['group']} | "
            f"{inst['relation']} {inst['anchor_id']} | "
            f"{dur['min']}/{dur['ideal']}/{dur['max']} min | "
            f"{inst['flexibility']} {inst['priority']} | {cand}"
        )

    if not derived:
        out.append("(none)")

    out.append(f"-- {len(derived)} derived activities")

    out.append("")
    out.append("[PROPOSED PLAN]")

    if plan is None:
        out.append(
            "No plan generated: availability is unknown. "
            "Add a profile before treating gaps as usable."
        )
    else:
        for item in plan["placements"]:
            inst = item["instance"]
            out.append(
                f"- {item['start']:%a %Y-%m-%d %H:%M}"
                f"-{item['end']:%H:%M} | "
                f"{inst['instance_id']} | "
                f"{inst['priority']} | "
                f"{item['duration_minutes']} min"
            )

        if not plan["placements"]:
            out.append("(no activities placed)")

        out.append("")
        out.append("[UNSCHEDULED ACTIVITIES]")

        for item in plan["unscheduled"]:
            inst = item["instance"]
            out.append(
                f"- {inst['instance_id']} | "
                f"{inst['priority']} | {item['reason']}"
            )

        if not plan["unscheduled"]:
            out.append("(all generated activities were placed)")

        out.append(
            f"-- {len(plan['placements'])} placed, "
            f"{len(plan['unscheduled'])} unscheduled"
        )

    if save_snapshot:
        snap_file.write_text(json.dumps({
            "saved_at": datetime.now().isoformat(timespec="seconds"),
            "digest": digest(new_items),
            "items": new_items,
        }, indent=2, ensure_ascii=False), encoding="utf-8")
    return "\n".join(out)


def export_commands(state_path, include_reset: bool = True) -> str:
    ws = load_workspace(state_path)
    return state_to_script(ws["rules"], ws["exceptions"], state_path=str(state_path),
                           digest=digest(_sections(ws)["rules"]), include_reset=include_reset)


# --------------------------------------------------------------------- watch

def _mtimes(paths) -> dict:
    return {p: (os.path.getmtime(p) if p and os.path.exists(p) else None) for p in paths}


def watch(paths, fn, interval: float = 1.0, max_runs: int | None = None) -> int:
    """Call fn() now and again whenever any file in `paths` is saved."""
    last = _mtimes(paths)
    fn()
    runs = 1
    while max_runs is None or runs < max_runs:
        _time.sleep(interval)
        cur = _mtimes(paths)
        if cur != last:
            last = cur
            fn()
            runs += 1
    return runs


# ------------------------------------------------------------------ CLI glue

def review_args(parser) -> None:
    """Call once on your existing argparse parser. (--load and --on already exist.)"""
    g = parser.add_argument_group("v0.2 review")
    g.add_argument("--profile", help="profile.json (protected time, buffers)")
    g.add_argument("--relations", help="relations.json (prep/review rules)")
    g.add_argument("--review", action="store_true", help="rebuild derived sections and show what changed")
    g.add_argument("--watch", action="store_true", help="rerun --review whenever a source file is saved")
    g.add_argument("--export-commands", nargs="?", const="-", default=None, metavar="FILE",
                   help="decompile current state to commands (FILE or stdout)")
    g.add_argument("--no-snapshot", action="store_true", help="do not update the change snapshot")


def review_handle(args) -> int | None:
    """Return an exit code if a v0.2 flag was handled, else None (fall through)."""
    wants = getattr(args, "review", False) or getattr(args, "watch", False)
    export = getattr(args, "export_commands", None)
    if not wants and export is None:
        return None
    state = getattr(args, "load", None)
    if not state:
        print("v0.2 commands need --load STATE.json")
        return 2
    if export is not None:
        text = export_commands(state)
        if export == "-":
            print(text, end="")
        else:
            Path(export).write_text(text, encoding="utf-8")
            print(f"wrote {export}")
        if not wants:
            return 0
    on = getattr(args, "on", None)
    on = date.fromisoformat(on) if isinstance(on, str) else (on or date.today())

    def once():
        print(run_review(state, args.profile, args.relations, on=on,
                         save_snapshot=not args.no_snapshot))
        print()

    if args.watch:
        try:
            watch([state, args.profile, args.relations], once)
        except KeyboardInterrupt:
            pass
        return 0
    once()
    return 0
