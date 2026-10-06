import json
from datetime import date

import pytest

from cal_engine import (
    CalendarState,
    MockLLMClient,
    OperationError,
    ResolutionEngine,
    TransactionLog,
    digest_schedule_state,
    parse_command,
)

REF = date(2026, 9, 21)
WED = date(2026, 9, 23)
SAT = date(2026, 9, 26)
NEW = "cal --fixed exam-review --wed --time 15:00-18:00"


def _state():
    state = CalendarState()
    for command in [
        "cal --fixed school --daily --time 07:00-11:30 --except sunday",
        "cal --fixed lunch --daily --time 13:00-14:00",
        "cal --fixed math --wed --time 15:00-16:00 --changeable --move-to sat",
        "cal --fixed exercise --wed --time 17:00-18:00 --flexible",
        "cal --optional gaming --wed --time 16:00-17:00",
    ]:
        state.add_rule(parse_command(command, REF))
    return state


def _response():
    return json.dumps(
        {
            "proposed_schedule_changes": [
                {
                    "activity_id": "math",
                    "action": "MOVE",
                    "new_start": "2026-09-26 15:00",
                    "new_end": "2026-09-26 16:00",
                    "reasoning": "Move to its allowed Saturday destination.",
                },
                {
                    "activity_id": "exercise",
                    "action": "SHIFT",
                    "new_start": "2026-09-23 18:00",
                    "new_end": "2026-09-23 19:00",
                    "reasoning": "Use the free block after the new activity.",
                },
                {
                    "activity_id": "gaming",
                    "action": "REMOVE",
                    "new_start": None,
                    "new_end": None,
                    "reasoning": "Drop the optional activity under pressure.",
                },
            ],
            "unscheduled_items": [],
            "conflict_summary": "Moved Math, shifted Exercise, and dropped Gaming.",
            "trade_off_explanation": "No protected activity was changed.",
        }
    )


def test_deterministic_resolution_commits_immediately():
    engine = ResolutionEngine(CalendarState(), REF)
    outcome = engine.propose_add("cal --fixed lunch --mon --time 13:00-14:00")
    assert outcome.transaction.user_feedback["decision"] == "ACCEPT"
    assert outcome.transaction.llm_interaction is None
    assert "lunch" in [item.activity_id for item in engine.calendar.resolver().build_actual_schedule(REF, REF)]


def test_digest_is_canonical_and_state_sensitive():
    state = _state()
    first = digest_schedule_state(state.resolver(), WED, SAT)
    second = digest_schedule_state(state.copy().resolver(), WED, SAT)
    assert first == second
    state.remove_rule("lunch")
    assert digest_schedule_state(state.resolver(), WED, SAT) != first


def test_mock_ai_transaction_is_pending_then_accepted(tmp_path):
    log_path = tmp_path / "transactions.jsonl"
    engine = ResolutionEngine(_state(), REF, TransactionLog(log_path))
    outcome = engine.propose_add(NEW, MockLLMClient(_response()))
    record = outcome.transaction

    assert outcome.pending
    assert "exam-review" not in [item.activity_id for item in engine.calendar.resolver().build_actual_schedule(WED, WED)]
    assert set(record.to_dict()) == {
        "transaction_id",
        "timestamp",
        "input_state_digest",
        "new_change_request",
        "conflict_detection",
        "local_solver_result",
        "llm_interaction",
        "user_feedback",
        "final_accepted_schedule_digest",
    }
    assert record.user_feedback["decision"] == "PENDING"
    assert record.final_accepted_schedule_digest is None
    assert "math" in record.llm_interaction["prompt"]
    assert record.new_change_request["raw_command"] == NEW

    accepted = engine.accept(record.transaction_id, comment="Looks good")
    assert accepted.transaction.user_feedback["decision"] == "ACCEPT"
    assert accepted.transaction.final_accepted_schedule_digest
    assert "exam-review" in [item.activity_id for item in engine.calendar.resolver().build_actual_schedule(WED, WED)]
    assert "math" not in [item.activity_id for item in engine.calendar.resolver().build_actual_schedule(WED, WED)]
    assert "math" in [item.activity_id for item in engine.calendar.resolver().build_actual_schedule(SAT, SAT)]
    assert "gaming" not in [item.activity_id for item in engine.calendar.resolver().build_actual_schedule(WED, WED)]
    assert TransactionLog(log_path).latest(record.transaction_id).user_feedback["decision"] == "ACCEPT"
    assert len(TransactionLog(log_path).read_all()) == 2


def test_modify_replaces_the_ai_diff_for_the_same_activity(tmp_path):
    engine = ResolutionEngine(_state(), REF, TransactionLog(tmp_path / "transactions.jsonl"))
    proposed = engine.propose_add(NEW, MockLLMClient(_response()))
    manual = [
        {
            "activity_id": "math",
            "action": "MOVE",
            "new_start": "2026-09-26 16:00",
            "new_end": "2026-09-26 17:00",
            "reasoning": "User chose a later Saturday slot.",
        }
    ]
    modified = engine.modify(proposed.transaction.transaction_id, manual)
    saturday = modified.state.resolver().build_actual_schedule(SAT, SAT)
    math = [item for item in saturday if item.activity_id == "math"]
    assert len(math) == 1
    assert math[0].start.strftime("%H:%M") == "16:00"
    assert modified.transaction.user_feedback["decision"] == "MODIFY"
    assert modified.transaction.user_feedback["manual_diffs"] == manual


def test_reject_keeps_original_schedule_and_records_digest(tmp_path):
    engine = ResolutionEngine(_state(), REF, TransactionLog(tmp_path / "transactions.jsonl"))
    before = engine.calendar.to_dict()
    proposed = engine.propose_add(NEW, MockLLMClient(_response()))
    rejected = engine.reject(proposed.transaction.transaction_id, "Keep the original plan.")
    assert engine.calendar.to_dict() == before
    assert rejected.transaction.user_feedback["decision"] == "REJECT"
    assert rejected.transaction.final_accepted_schedule_digest == proposed.transaction.input_state_digest


def test_ai_cannot_remove_protected_activity(tmp_path):
    log = TransactionLog(tmp_path / "transactions.jsonl")
    engine = ResolutionEngine(_state(), REF, log)
    response = json.dumps(
        {
            "proposed_schedule_changes": [
                {
                    "activity_id": "school",
                    "action": "REMOVE",
                    "new_start": None,
                    "new_end": None,
                    "reasoning": "invalid",
                }
            ],
            "unscheduled_items": [],
            "conflict_summary": "invalid",
            "trade_off_explanation": "invalid",
        }
    )
    with pytest.raises(OperationError, match="protected"):
        engine.propose_add(NEW, MockLLMClient(response))
    assert log.read_all() == []

