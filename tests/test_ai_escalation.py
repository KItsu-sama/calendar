import json
from datetime import date

from cal_engine import (
    LLMResponseError,
    MockLLMClient,
    ScheduleResolver,
    escalate_to_ai,
    parse_command,
    parse_llm_response,
)
from cal_engine.ai_escalation import build_conflict_prompt
from cal_engine.conflict import resolve_conflict_locally

REF = date(2026, 9, 21)  # Monday
WED = date(2026, 9, 23)

CANNED_GOOD_RESPONSE = json.dumps(
    {
        "proposed_schedule_changes": [
            {
                "activity_id": "math-class",
                "action": "MOVE",
                "new_start": "2026-09-26 15:00",
                "new_end": "2026-09-26 16:00",
                "reasoning": "Moved to allowed Saturday window.",
            },
            {
                "activity_id": "exercise",
                "action": "SHIFT",
                "new_start": "2026-09-23 18:00",
                "new_end": "2026-09-23 19:00",
                "reasoning": "Shifted down into free block.",
            },
        ],
        "unscheduled_items": [],
        "conflict_summary": "Moved Math to Saturday and shifted Exercise later.",
        "trade_off_explanation": "No fixed or critical activities were touched.",
    }
)


def _complex_conflict_setup():
    rules = [
        parse_command("cal --fixed school --daily --time 07:00-11:30 --except sunday", REF),
        parse_command("cal --fixed lunch --daily --time 13:00-14:00", REF),
        parse_command("cal --fixed math-class --wed --time 15:00-16:00 --changeable --move-to sat", REF),
        parse_command("cal --fixed exercise --wed --time 17:00-18:00 --flexible", REF),
        parse_command("cal --optional gaming --wed --time 16:00-17:00", REF),
    ]
    resolver = ScheduleResolver(rules=rules, exceptions=[])
    new_activity = parse_command("cal --fixed exam-review --wed --time 15:00-18:00", REF)
    local_result = resolve_conflict_locally(resolver, new_activity, WED)
    assert local_result.status == "REQUIRES_AI"
    return resolver, new_activity, local_result


def test_prompt_contains_full_context_not_a_minimal_prompt():
    resolver, new_activity, local_result = _complex_conflict_setup()
    prompt = build_conflict_prompt(resolver, new_activity, WED, local_result.conflicts, task_pool=[])

    for header in [
        "[SYSTEM CONTEXT]",
        "[CURRENT SCHEDULE MATRIX - TARGET WINDOW]",
        "[TASK POOL / UNPLACED TODOs]",
        "[NEW PROPOSED ACTIVITY]",
        "[CONFLICT ANALYSIS]",
        "[SCHEDULING RULES & HIERARCHY]",
        "[REQUIRED OUTPUT FORMAT]",
    ]:
        assert header in prompt

    # Full schedule matrix, not just the conflicting items
    assert "School" in prompt or "school" in prompt
    assert "Lunch" in prompt or "lunch" in prompt
    # The new activity and every conflicting item are named
    assert "exam-review" in prompt
    assert "math-class" in prompt
    assert "gaming" in prompt
    assert "exercise" in prompt


def test_task_pool_included_when_provided():
    resolver, new_activity, local_result = _complex_conflict_setup()
    homework = parse_command("cal --todo math-homework --min 30m --max 90m --due fri 21:00", REF)
    prompt = build_conflict_prompt(
        resolver, new_activity, WED, local_result.conflicts, task_pool=[homework]
    )
    assert "math-homework" in prompt
    assert "Deadline" in prompt


def test_parse_llm_response_valid_json():
    parsed = parse_llm_response(CANNED_GOOD_RESPONSE)
    assert len(parsed["proposed_schedule_changes"]) == 2
    assert parsed["proposed_schedule_changes"][0]["activity_id"] == "math-class"


def test_parse_llm_response_strips_markdown_fences():
    fenced = "```json\n" + CANNED_GOOD_RESPONSE + "\n```"
    parsed = parse_llm_response(fenced)
    assert parsed["conflict_summary"]


def test_parse_llm_response_rejects_missing_required_field():
    bad = json.dumps({"proposed_schedule_changes": [], "unscheduled_items": []})  # missing 2 required keys
    try:
        parse_llm_response(bad)
        assert False, "expected LLMResponseError"
    except LLMResponseError:
        pass


def test_parse_llm_response_rejects_invalid_json():
    try:
        parse_llm_response("this is not json at all")
        assert False, "expected LLMResponseError"
    except LLMResponseError:
        pass


def test_escalate_to_ai_end_to_end_with_mock_client():
    resolver, new_activity, local_result = _complex_conflict_setup()
    client = MockLLMClient(canned_response=CANNED_GOOD_RESPONSE)
    result = escalate_to_ai(resolver, new_activity, WED, local_result, client)

    assert len(client.prompts_received) == 1  # no repair needed
    assert result.parsed_response["proposed_schedule_changes"][0]["activity_id"] == "math-class"
    assert "CONFLICT RESOLUTION REQUEST" in result.prompt


def test_escalate_to_ai_repairs_one_malformed_response_then_succeeds():
    resolver, new_activity, local_result = _complex_conflict_setup()
    bad_then_good = MockLLMClient(canned_response="not json")

    call_count = {"n": 0}
    original_complete = bad_then_good.complete

    def flaky_complete(prompt: str) -> str:
        call_count["n"] += 1
        bad_then_good.prompts_received.append(prompt)
        return "not json" if call_count["n"] == 1 else CANNED_GOOD_RESPONSE

    bad_then_good.complete = flaky_complete
    result = escalate_to_ai(resolver, new_activity, WED, local_result, bad_then_good)

    assert call_count["n"] == 2
    assert result.parsed_response["conflict_summary"]


def test_escalate_to_ai_gives_up_after_repair_attempts_exhausted():
    resolver, new_activity, local_result = _complex_conflict_setup()
    always_bad = MockLLMClient(canned_response="still not json")
    try:
        escalate_to_ai(resolver, new_activity, WED, local_result, always_bad, max_repair_attempts=1)
        assert False, "expected LLMResponseError"
    except LLMResponseError:
        pass
    assert len(always_bad.prompts_received) == 2  # original + 1 repair attempt
