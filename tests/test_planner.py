from datetime import date, datetime

from cal_engine.planner import place_activities

DAY = date(2026, 10, 19)
WINDOW = {
    DAY: [(9 * 60, 10 * 60 + 30)],
}


def activity(instance_id, *, priority="NORMAL", duration=30, lo=None, hi=None):
    return {
        "instance_id": instance_id,
        "priority": priority,
        "flexibility": "FLEXIBLE",
        "duration": {"min": duration, "ideal": duration, "max": duration},
        "near_edge": "start",
        "near_lo": lo or datetime(2026, 10, 19, 9, 0),
        "near_hi": hi or datetime(2026, 10, 19, 10, 30),
    }


def test_plan_places_multiple_activities_without_overlap():
    plan = place_activities([activity("a"), activity("b")], WINDOW)
    assert len(plan["placed"]) == 2
    first, second = plan["placed"]
    assert first["end"] <= second["start"] or second["end"] <= first["start"]


def test_higher_priority_wins_when_only_one_slot_fits():
    only_one_hour = {DAY: [(9 * 60, 10 * 60)]}
    plan = place_activities(
        [activity("low", priority="LOW", duration=60),
         activity("high", priority="HIGH", duration=60)],
        only_one_hour,
    )
    assert [p["instance"]["instance_id"] for p in plan["placed"]] == ["high"]
    assert plan["unplaced"][0]["instance"]["instance_id"] == "low"
    assert "conflicts" in plan["unplaced"][0]["reason"]


def test_buffer_is_enforced_between_proposed_activities():
    short_window = {DAY: [(9 * 60, 10 * 60 + 15)]}
    plan = place_activities(
        [activity("a", duration=30), activity("b", duration=30)],
        short_window,
        buffer_min=15,
    )
    assert len(plan["placed"]) == 1
    assert len(plan["unplaced"]) == 1


def test_no_candidate_is_distinguished_from_scheduling_conflict():
    plan = place_activities([activity("a", lo=datetime(2026, 10, 19, 12, 0),
                                      hi=datetime(2026, 10, 19, 13, 0))], WINDOW)
    assert plan["unplaced"][0]["reason"] == "no candidate slot"
