import json
import os
import shutil
import threading
import time
from datetime import date, datetime
from pathlib import Path

import pytest

from cal_engine.availability import Profile, load_profile, to_min
from cal_engine.decompile import (reference_parse, roundtrip_check, rule_to_command,
                                  state_to_script, unsupported_fields)
from cal_engine.derive import (candidate_slots, expand_occurrences, generate_derived,
                               parse_duration)
from cal_engine.review import run_review, watch

EX = Path(__file__).resolve().parent.parent / "examples"
MON, SUN = date(2026, 10, 12), date(2026, 10, 18)  # the week from the v0.1 output


@pytest.fixture
def state():
    return json.loads((EX / "state.sample.json").read_text())


@pytest.fixture
def profile():
    return load_profile(EX / "profile.json")


@pytest.fixture
def relations():
    return json.loads((EX / "relations.json").read_text())["relations"]


@pytest.fixture
def workdir(tmp_path):
    for f in ("state.sample.json", "profile.json", "relations.json"):
        shutil.copy(EX / f, tmp_path / f)
    return tmp_path


# ---------------------------------------------------------------- availability

def test_saturday_usable_blocks(profile):
    busy = [(to_min("07:30"), to_min("09:00")), (to_min("15:15"), to_min("17:00")),
            (to_min("17:30"), to_min("19:00"))]
    got = profile.usable_blocks("SAT", busy)
    assert (to_min("06:00"), to_min("07:20")) in got          # sleep ends, 10-min buffer before class
    assert (to_min("09:10"), to_min("15:05")) in got
    assert (to_min("19:30"), to_min("22:30")) in got          # dinner is soft-protected
    assert not any(s == to_min("17:10") for s, _ in got)      # the 10-min sliver is dropped
    soft = profile.usable_blocks("SAT", busy, allow_soft=True)
    assert (to_min("19:10"), to_min("22:30")) in soft


def test_wrapping_sleep_covers_early_morning_of_next_day():
    p = Profile.from_dict({"protected": [{"id": "sleep", "days": ["MON"], "start": "22:30", "end": "06:00"}]})
    assert p.usable_blocks("MON", [])[-1][1] == to_min("22:30")
    assert p.usable_blocks("TUE", [])[0][0] == to_min("06:00")   # Monday's sleep spills into Tuesday
    assert p.usable_blocks("MON", [])[0][0] == 0                 # nothing protected Monday morning


def test_missing_profile_is_empty(tmp_path):
    assert load_profile(tmp_path / "nope.json").is_empty


# ----------------------------------------------------------------- occurrences

def test_week_occurrences_match_v01_output(state):
    occ = expand_occurrences(state["rules"], MON, SUN)
    assert len(occ) == 15  # 5 school + 2 each of math/chems/english/ielts/phy
    assert [o["rule_id"] for o in occ if o["date"] == date(2026, 10, 17)] == \
           ["extra_ielts", "extra_phy", "extra_math"]


def test_cancel_and_move_exceptions(state):
    ex = [{"rule_id": "extra_math", "target_date": "2026-10-13", "action": "CANCEL"},
          {"rule_id": "extra_math", "target_date": "2026-10-17", "action": "MOVE", "new_date": "2026-10-18"}]
    occ = [o for o in expand_occurrences(state["rules"], MON, SUN, ex) if o["rule_id"] == "extra_math"]
    assert [o["date"] for o in occ] == [date(2026, 10, 18)]


def test_valid_until_stops_rule(state):
    state["rules"][1]["recurrence"]["valid_until"] = "2026-10-14"
    occ = [o for o in expand_occurrences(state["rules"], MON, SUN) if o["rule_id"] == "extra_math"]
    assert [o["date"] for o in occ] == [date(2026, 10, 13)]


# --------------------------------------------------------------------- derived

def test_parse_duration():
    assert parse_duration("PT30M").total_seconds() == 1800
    assert parse_duration("P1DT2H").total_seconds() == 26 * 3600
    with pytest.raises(ValueError):
        parse_duration("PT")


def test_derived_counts_and_inheritance(state, relations):
    anchors = expand_occurrences(state["rules"], MON, SUN)
    derived, matched = generate_derived(relations, anchors)
    assert matched == {"review_after_class": 10, "prep_before_class": 10, "spaced_review": 10}
    assert len(derived) == 10 + 10 + 30                      # school is tagged "school", not "class"
    math = [d for d in derived if d["anchor_rule"] == "extra_math"]
    assert {d["group"] for d in math} == {"math"}
    assert "derived" in math[0]["tags"] and "class" in math[0]["tags"]


def test_before_and_after_windows(state, relations):
    anchors = [o for o in expand_occurrences(state["rules"], MON, SUN)
               if o["occ_id"] == "extra_math@2026-10-13"]
    derived, _ = generate_derived(relations, anchors)
    by = {d["rule_id"]: d for d in derived if d["relation"] in ("BEFORE", "AFTER")}
    assert by["prep_before_class"]["near_hi"] == datetime(2026, 10, 13, 17, 15)   # 17:30 - 15 min
    assert by["review_after_class"]["near_lo"] == datetime(2026, 10, 13, 19, 30)   # 19:00 + 30 min


def test_candidates_respect_usable_blocks(state, relations, profile):
    anchors = expand_occurrences(state["rules"], MON, SUN)
    derived, _ = generate_derived(relations, [o for o in anchors if o["occ_id"] == "extra_math@2026-10-13"])
    usable = {d: profile.usable_blocks(
        ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"][d.weekday()],
        [(o["start"], o["end"]) for o in anchors if o["date"] == d])
        for d in (date(2026, 10, 13), date(2026, 10, 14))}
    review = next(d for d in derived if d["rule_id"] == "review_after_class")
    slots = candidate_slots(review, usable)
    assert slots and all(s >= datetime(2026, 10, 13, 19, 30) for s, _ in slots)
    # nothing may start inside the sleep window or on top of dinner (soft)
    assert not any(s.hour >= 22 and s.minute > 0 and s.day == 13 for s, _ in slots)
    assert not any(datetime(2026, 10, 13, 18, 45) < e <= datetime(2026, 10, 13, 19, 30) for _, e in slots)


def test_bad_selector_is_rejected(state):
    with pytest.raises(ValueError):
        generate_derived([{"id": "x", "from": {}, "relation": "AFTER"}],
                         expand_occurrences(state["rules"], MON, SUN))


# ------------------------------------------------------------------- decompile

def test_roundtrip_all_sample_rules(state):
    assert roundtrip_check(state["rules"]) == []


def test_roundtrip_kitchen_sink():
    rule = {"id": "essay", "name": "history essay", "flexibility": "CHANGEABLE", "priority": "NORMAL",
            "group": "history", "tags": ["task", "writing"],
            "recurrence": {"type": "WEEKLY", "days_of_week": ["MON", "WED"],
                           "valid_from": "2026-10-01", "valid_until": "2026-12-01"},
            "time_constraints": {"allowed_windows": [{"start_time": "15:00", "end_time": "18:00"}],
                                 "allowed_days": ["MON", "WED", "SAT"], "min_duration_minutes": 30,
                                 "max_duration_minutes": 90, "deadline": "2026-11-20"},
            "move_options": {"preferred_days": ["SAT"]}}
    assert roundtrip_check([rule]) == []
    assert 'history essay' not in rule_to_command(rule).replace('"history essay"', "")  # quoted, not bare


def test_unexpressible_fields_are_flagged_not_dropped(state):
    r = {**state["rules"][1], "colour": "red"}
    assert unsupported_fields(r) == ["colour"]
    assert "WARNING: extra_math" in state_to_script([r])
    assert roundtrip_check([r])[0].startswith("extra_math: cannot express colour")


def test_wrong_parser_is_reported(state):
    def strict(cmd):
        raise ValueError("unknown option --weekly")
    problems = roundtrip_check(state["rules"], parse=strict)
    assert len(problems) == len(state["rules"]) and "rejected" in problems[0]


def test_script_has_reset_and_one_line_per_rule(state):
    s = state_to_script(state["rules"], state_path="state.json")
    cmds = [l for l in s.splitlines() if l.startswith("python")]
    assert cmds[0].startswith("python main.py --reset")
    assert len(cmds) == 1 + len(state["rules"])


# --------------------------------------------------------------- review/rerun

def _review(wd):
    return run_review(wd / "state.sample.json", wd / "profile.json", wd / "relations.json", on=MON)


def test_review_reports_changes_then_quiet(workdir):
    first = _review(workdir)
    assert "(first run" in first and "[USABLE FREE BLOCKS]" in first and "[DERIVED ACTIVITIES]" in first
    assert "50 derived" in first
    assert "(no changes since last run)" in _review(workdir)

    sp = workdir / "state.sample.json"
    st = json.loads(sp.read_text())
    st["rules"].append({"id": "extra_bio", "name": "extra_bio", "flexibility": "FIXED", "priority": "HIGH",
                        "group": "bio", "tags": ["class", "extra"],
                        "recurrence": {"type": "WEEKLY", "days_of_week": ["WED"]},
                        "time_constraints": {"day_time_windows": {"WED": {"start_time": "16:00", "end_time": "17:30"}}}})
    st["rules"][1]["time_constraints"]["day_time_windows"]["TUE"]["start_time"] = "17:00"
    sp.write_text(json.dumps(st))
    third = _review(workdir)
    assert "+ rules: extra_bio" in third
    assert "~ rules: extra_math (changed: time_constraints)" in third
    assert "55 derived" in third        # new class picked up: +1 review +1 prep +3 spaced reviews
    assert "extra_bio@2026-10-14" in third


def test_review_warns_when_relation_matches_nothing(workdir):
    rp = workdir / "relations.json"
    data = json.loads(rp.read_text())
    data["relations"][0]["from"] = {"match_tags": ["nonexistent"]}
    rp.write_text(json.dumps(data))
    assert "relation 'review_after_class' matched 0 anchors" in _review(workdir)


def test_review_without_profile_says_unknown(workdir):
    out = run_review(workdir / "state.sample.json", None, workdir / "relations.json", on=MON)
    assert "availability unknown" in out


def test_watch_reruns_after_save(workdir):
    calls = []
    sp = workdir / "state.sample.json"

    def touch():
        time.sleep(0.2)
        os.utime(sp, (time.time() + 5, time.time() + 5))

    threading.Thread(target=touch).start()
    runs = watch([str(sp)], lambda: calls.append(1), interval=0.05, max_runs=2)
    assert runs == 2 and len(calls) == 2



from datetime import date, datetime

from cal_engine.planner import plan_activities


def test_planner_never_overlaps_its_own_suggestions():
    day = date(2026, 10, 19)
    instances = [
        {
            "instance_id": "task-a",
            "priority": "HIGH",
            "near_edge": "start",
            "near_lo": datetime(2026, 10, 19, 9, 0),
            "near_hi": datetime(2026, 10, 19, 11, 0),
            "duration": {"min": 60, "ideal": 60, "max": 90},
        },
        {
            "instance_id": "task-b",
            "priority": "NORMAL",
            "near_edge": "start",
            "near_lo": datetime(2026, 10, 19, 9, 0),
            "near_hi": datetime(2026, 10, 19, 11, 0),
            "duration": {"min": 60, "ideal": 60, "max": 90},
        },
    ]

    result = plan_activities(
        instances,
        {day: [(9 * 60, 11 * 60)]},
    )

    placements = result["placements"]
    assert len(placements) == 2

    first, second = placements
    assert first["end"] <= second["start"] or second["end"] <= first["start"]


def test_planner_reports_activity_that_cannot_fit():
    day = date(2026, 10, 19)
    instance = {
        "instance_id": "long-task",
        "priority": "HIGH",
        "near_edge": "start",
        "near_lo": datetime(2026, 10, 19, 9, 0),
        "near_hi": datetime(2026, 10, 19, 10, 0),
        "duration": {"min": 60, "ideal": 60, "max": 90},
    }

    result = plan_activities(
        [instance],
        {day: [(9 * 60, 9 * 60 + 30)]},
    )

    assert result["placements"] == []
    assert len(result["unscheduled"]) == 1
    assert result["unscheduled"][0]["instance"]["instance_id"] == "long-task"

