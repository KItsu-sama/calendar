from datetime import date, datetime

from cal_engine import ScheduleResolver, parse_command
from cal_engine.conflict import resolve_conflict_locally

REF = date(2026, 9, 21)  # Monday
WED = date(2026, 9, 23)
SAT = date(2026, 9, 26)


def _base_rules():
    return [
        parse_command("cal --fixed school --daily --time 07:00-11:30 --except sunday", REF),
        parse_command("cal --fixed lunch --daily --time 13:00-14:00", REF),
        parse_command(
            "cal --fixed math-class --wed --time 15:00-16:00 --changeable --move-to sat", REF
        ),
        parse_command("cal --fixed exercise --wed --time 17:00-18:00 --flexible", REF),
    ]


def test_direct_add_no_conflict():
    resolver = ScheduleResolver(rules=_base_rules(), exceptions=[])
    extra_class = parse_command("cal --fixed extra-class --wed --time 16:00-17:00", REF)
    result = resolve_conflict_locally(resolver, extra_class, WED)
    assert result.status == "DIRECT_ADD"
    assert result.changes == []


def test_easy_conflict_moves_changeable_math_to_saturday():
    resolver = ScheduleResolver(rules=_base_rules(), exceptions=[])
    extra_class = parse_command("cal --fixed extra-class --wed --time 15:00-17:00", REF)
    result = resolve_conflict_locally(resolver, extra_class, WED)

    assert result.status == "SOLVED_LOCALLY"
    assert len(result.changes) == 1
    change = result.changes[0]
    assert change.activity_id == "math-class"
    assert change.action == "MOVE"
    # Saturday is fully free, so it keeps its original 15:00-16:00 slot
    assert change.new_start == datetime(2026, 9, 26, 15, 0)
    assert change.new_end == datetime(2026, 9, 26, 16, 0)

    # Exercise (17:00-18:00) does not overlap a 15:00-17:00 activity
    assert not any(c.activity_id == "exercise" for c in result.conflicts)


def test_flexible_activity_shifts_within_same_day():
    rules = [
        parse_command("cal --fixed morning-block --wed --time 09:00-12:00", REF),
        parse_command("cal --fixed reading --wed --time 12:00-13:00 --flexible", REF),
    ]
    resolver = ScheduleResolver(rules=rules, exceptions=[])
    # New fixed activity swallows Reading's 12:00-13:00 slot; 13:00-15:00 is free.
    new_meeting = parse_command("cal --fixed team-meeting --wed --time 12:00-13:00", REF)
    result = resolve_conflict_locally(resolver, new_meeting, WED)

    assert result.status == "SOLVED_LOCALLY"
    assert len(result.changes) == 1
    change = result.changes[0]
    assert change.activity_id == "reading"
    assert change.action == "SHIFT"
    assert change.new_start == datetime(2026, 9, 23, 13, 0)


def test_fixed_vs_fixed_requires_ai():
    resolver = ScheduleResolver(rules=_base_rules(), exceptions=[])
    # Overlaps School, which is FIXED -> no local resolution attempted at all
    urgent = parse_command("cal --fixed urgent --daily --time 08:00-09:00", REF)
    result = resolve_conflict_locally(resolver, urgent, WED)
    assert result.status == "REQUIRES_AI"
    assert "FIXED" in result.reason


def test_complex_conflict_with_optional_item_requires_ai_even_if_others_solvable():
    rules = _base_rules() + [
        parse_command("cal --optional gaming --wed --time 16:00-17:00", REF),
    ]
    resolver = ScheduleResolver(rules=rules, exceptions=[])
    # 15:00-18:00 overlaps math-class (CHANGEABLE, solvable), gaming (OPTIONAL,
    # not locally solvable) and exercise (FLEXIBLE, solvable) -- one
    # unsolvable conflict must escalate the whole thing.
    big_activity = parse_command("cal --fixed exam-review --wed --time 15:00-18:00", REF)
    result = resolve_conflict_locally(resolver, big_activity, WED)
    assert result.status == "REQUIRES_AI"
    assert "gaming" in result.reason
    assert len(result.conflicts) == 3
