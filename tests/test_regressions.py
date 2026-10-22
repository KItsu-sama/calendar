"""Regression tests: solver collisions, day_time_windows, split-session
review dedup, and review output without a profile. Self-contained (no examples/)."""
import json
from datetime import date

import pytest

from cal_engine import parse_command
from cal_engine.conflict import resolve_conflict_locally
from cal_engine.derive import coalesce_occurrences, expand_occurrences, generate_derived
from cal_engine.operations import add_activity, preview_activity
from cal_engine.review import run_review
from cal_engine.storage import CalendarState

REF = date(2026, 10, 12)  # Monday
MON, THU = date(2026, 10, 12), date(2026, 10, 15)


def _state(*cmds):
    st = CalendarState()
    for c in cmds:
        st.add_rule(parse_command(c, REF))
    return st


# ----------------------------------------------------------- solver collision
def test_two_moved_items_never_share_a_slot():
    st = _state(
        "--fixed math --mon --time 14:00-15:00 --changeable --move-to sat",
        "--fixed art --mon --time 15:00-16:00 --changeable --move-to sat",
        "--fixed busy --sat --time 13:00-17:00",   # original times unavailable on Saturday
    )
    new = parse_command("--fixed new --mon --time 14:00-16:00", REF)
    res = resolve_conflict_locally(st.resolver(), new, MON)
    assert res.status == "SOLVED_LOCALLY"
    spans = sorted((c.new_start, c.new_end) for c in res.changes)
    assert len(spans) == 2
    assert spans[0][1] <= spans[1][0], "relocated items overlap"
    # and the full add path now succeeds instead of raising OperationError
    assert add_activity(st, new, REF, MON).status == "SOLVED_LOCALLY"


# ------------------------------------------------------- day_time_windows
def _day_window_rule():
    rule = parse_command("--fixed x --mon --time 14:00-16:00", REF)
    rule.time_constraints.day_time_windows = {"MON": rule.time_constraints.fixed_interval}
    rule.time_constraints.fixed_interval = None
    return rule


def test_preview_and_add_accept_day_time_windows_only_rule():
    st = _state("--fixed meet --mon --time 15:00-16:00")
    rule = _day_window_rule()
    res = preview_activity(st, rule, MON)
    assert res.status == "REQUIRES_AI" and res.conflicts      # conflict is detected, not skipped
    clear = preview_activity(CalendarState(), rule, MON)
    assert clear.status == "DIRECT_ADD"
    assert add_activity(CalendarState(), rule, REF).status == "DIRECT_ADD"


# -------------------------------------------------------- split-session dedup
def _rules():
    def r(rid, start, end, **extra):
        return {"id": rid, "name": rid, "recurrence": {"type": "WEEKLY", "days_of_week": ["THU"]},
                "time_constraints": {"fixed_interval": {"start_time": start, "end_time": end}}, **extra}
    return [
        r("school__THU__ANH_VAN_1", "09:00", "09:45", tags=["school"]),
        r("school__THU__ANH_VAN_2", "09:50", "10:35", tags=["school"]),   # 5-min break
        r("school__THU__TOAN_1", "10:40", "11:25", tags=["school"]),
    ]


def _occ():
    return expand_occurrences(_rules(), THU, THU)


def _rel(**kw):
    return {"id": "rev", "from": {"match_tags": ["school"]}, "relation": "AFTER", **kw}


def test_split_periods_produce_one_review_per_subject():
    derived, matched = generate_derived([_rel()], _occ())
    assert matched["rev"] == 3                                  # raw anchors still reported
    assert len(derived) == 2                                    # ANH_VAN (1+2) and TOAN
    anh = next(d for d in derived if d["anchor_rule"] == "school__THU__ANH_VAN_1")
    assert anh["anchor_members"] == ["school__THU__ANH_VAN_1@2026-10-15", "school__THU__ANH_VAN_2@2026-10-15"]
    assert anh["near_lo"].strftime("%H:%M") == "10:35"          # AFTER the whole session


def test_before_relation_anchors_on_session_start():
    derived, _ = generate_derived([_rel(relation="BEFORE")], _occ())
    anh = next(d for d in derived if d["anchor_rule"] == "school__THU__ANH_VAN_1")
    assert anh["near_hi"].strftime("%H:%M") == "09:00"


def test_merge_can_be_disabled_or_widened():
    assert len(generate_derived([_rel(merge=False)], _occ())[0]) == 3
    assert len(generate_derived([_rel(merge="adjacent")], _occ())[0]) == 1   # one block 09:00-11:25
    assert len(generate_derived([_rel(merge_gap="PT2M")], _occ())[0]) == 3   # 5-min break > 2-min gap


def test_explicit_group_merges_ids_without_period_suffix():
    rules = _rules()[:2]
    rules[0]["id"], rules[1]["id"] = "lecture", "tutorial"      # no shared base name
    names = lambda occs: [len(a["members"]) for a in occs]
    assert names(coalesce_occurrences(expand_occurrences(rules, THU, THU))) == [1, 1]
    for r in rules:
        r["group"] = "english"
    assert names(coalesce_occurrences(expand_occurrences(rules, THU, THU))) == [2]


def test_unknown_merge_mode_is_rejected():
    with pytest.raises(ValueError):
        generate_derived([_rel(merge="sometimes")], _occ())


# ------------------------------------------------- review without a profile
def test_review_without_profile_still_lists_derived(tmp_path):
    (tmp_path / "s.json").write_text(json.dumps({"version": 1, "rules": _rules(), "exceptions": []}))
    (tmp_path / "r.json").write_text(json.dumps({"relations": [_rel(), {**_rel(), "id": "ghost", "from": {"match_tags": ["nope"]}}]}))
    out = run_review(tmp_path / "s.json", None, tmp_path / "r.json", on=THU, save_snapshot=False)
    assert "[DERIVED ACTIVITIES]" in out
    assert "relation 'ghost' matched 0 anchors" in out
    assert "availability unknown" in out
    ids = [l.split(" | ")[0] for l in out.splitlines() if l.startswith("- rev:")]
    assert any("ANH_VAN_1@" in i for i in ids) and not any("ANH_VAN_2@" in i for i in ids)
