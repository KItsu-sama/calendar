"""Phase 5 end-to-end resolution transactions and user feedback."""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, timedelta
from typing import List, Optional
from uuid import uuid4

from .ai_escalation import AIEscalationResult, LLMClient, escalate_to_ai
from .conflict import LocalSolveResult, resolve_conflict_locally
from .models import ActivityRule, CalendarException, activity_rule_from_dict
from .operations import (
    OperationError,
    add_activity,
    apply_schedule_changes,
    assert_no_overlaps,
    first_occurrence,
    is_protected,
    proposed_change_to_dict,
)
from .parser import parse_command
from .storage import CalendarState
from .transactions import (
    TransactionError,
    TransactionLog,
    TransactionRecord,
    digest_schedule_state,
    utc_timestamp,
)


class ResolutionError(ValueError):
    """A requested resolution cannot be completed safely."""


@dataclass
class ResolutionOutcome:
    transaction: TransactionRecord
    state: CalendarState
    ai_result: Optional[AIEscalationResult] = None

    @property
    def pending(self) -> bool:
        return self.transaction.user_feedback["decision"] == "PENDING"


def _local_result_dict(result: LocalSolveResult) -> dict:
    return {
        "status": result.status,
        "changes": [proposed_change_to_dict(change) for change in result.changes],
        "reason": result.reason,
    }


def _change_dates(changes: List[dict]) -> List[date]:
    values = []
    for change in changes:
        raw = change.get("new_start")
        if raw:
            values.append(date.fromisoformat(str(raw)[:10]))
    return values


class ResolutionEngine:
    """Resolve a new activity without mutating state until user feedback arrives."""

    def __init__(
        self,
        calendar: CalendarState,
        reference_date: date,
        transaction_log: Optional[TransactionLog] = None,
    ):
        self.calendar = calendar
        self.reference_date = reference_date
        self.transaction_log = transaction_log or TransactionLog()
        self._records: dict[str, TransactionRecord] = {}

    def _window(self, target: date) -> tuple[date, date]:
        return target, target + timedelta(days=6)

    def _remember(self, record: TransactionRecord) -> None:
        self._records[record.transaction_id] = record
        self.transaction_log.append(record)

    def _materialize_ai(
        self,
        base: CalendarState,
        rule: ActivityRule,
        target: date,
        ai_result: AIEscalationResult,
        skip_activity_ids: Optional[set[str]] = None,
    ) -> CalendarState:
        skipped = skip_activity_ids or set()
        for item in ai_result.parsed_response.get("unscheduled_items", []):
            unscheduled = base.get_rule(str(item))
            if unscheduled is None:
                raise OperationError(f"AI response unschedules unknown activity '{item}'.")
            if is_protected(unscheduled):
                raise OperationError(f"AI response unschedules protected activity '{unscheduled.id}'.")
        changes = [
            change
            for change in ai_result.parsed_response["proposed_schedule_changes"]
            if change["activity_id"] not in skipped
        ]
        candidate = apply_schedule_changes(base, changes, target)
        candidate.add_rule(rule)
        assert_no_overlaps(candidate, [target] + _change_dates(changes))
        return candidate


    def propose_add(
        self,
        raw_command: str,
        llm_client: Optional[LLMClient] = None,
        target_date: Optional[date] = None,
        task_pool: Optional[List[ActivityRule]] = None,
    ) -> ResolutionOutcome:
        parsed = parse_command(raw_command, self.reference_date)
        if isinstance(parsed, CalendarException):
            raise ResolutionError("The transaction request must add an ActivityRule, not an exception.")
        if parsed.time_constraints.fixed_interval is not None:
            effective_date = first_occurrence(parsed, self.reference_date, target_date)
            local = resolve_conflict_locally(self.calendar.resolver(), parsed, effective_date)
        else:
            effective_date = target_date or self.reference_date
            local = LocalSolveResult(status="DIRECT_ADD")

        start, end = self._window(effective_date)
        input_digest = digest_schedule_state(self.calendar.resolver(), start, end)
        ai_result = None
        llm_interaction = None
        final_digest = None

        if local.status == "REQUIRES_AI":
            if llm_client is None:
                raise ResolutionError(
                    "Conflict requires AI. Supply an LLM client or use the CLI generate-prompt command."
                )
            pool = task_pool if task_pool is not None else [
                item for item in self.calendar.rules if item.time_constraints.fixed_interval is None
            ]
            ai_result = escalate_to_ai(
                self.calendar.resolver(), parsed, effective_date, local, llm_client, task_pool=pool
            )
            candidate = self._materialize_ai(self.calendar, parsed, effective_date, ai_result)
            llm_interaction = {
                "prompt": ai_result.prompt,
                "raw_response": ai_result.raw_response,
                "parsed_response": ai_result.parsed_response,
            }
            decision = "PENDING"
        else:
            candidate = add_activity(
                self.calendar, parsed, self.reference_date, effective_date
            ).state
            final_digest = digest_schedule_state(candidate.resolver(), start, end)
            decision = "ACCEPT"
            self.calendar = candidate

        now = utc_timestamp()
        record = TransactionRecord(
            transaction_id=f"txn_{uuid4().hex}",
            timestamp=now,
            input_state_digest=input_digest,
            new_change_request={
                "raw_command": raw_command,
                "parsed_object": parsed.to_dict(),
                "target_date": effective_date.isoformat(),
            },
            conflict_detection={
                "status": "CONFLICTS" if local.conflicts else "CLEAR",
                "conflicts": [item.to_dict() for item in local.conflicts],
            },
            local_solver_result=_local_result_dict(local),
            llm_interaction=llm_interaction,
            user_feedback={
                "decision": decision,
                "recorded_at": now,
                "manual_diffs": [],
                "comment": None,
            },
            final_accepted_schedule_digest=final_digest,
        )
        self._remember(record)
        return ResolutionOutcome(record, candidate, ai_result)



    def _get_record(self, transaction_id: str) -> TransactionRecord:
        record = self._records.get(transaction_id)
        if record is None and self.transaction_log.path is not None:
            record = self.transaction_log.latest(transaction_id)
        if record is None:
            raise TransactionError(f"Unknown pending transaction '{transaction_id}'.")
        return record

    def _verify_input(self, record: TransactionRecord) -> date:
        target = date.fromisoformat(record.new_change_request["target_date"])
        start, end = self._window(target)
        current_digest = digest_schedule_state(self.calendar.resolver(), start, end)
        if current_digest != record.input_state_digest:
            raise ResolutionError("Calendar changed after this transaction was proposed; review it again.")
        return target

    def _ai_result(self, record: TransactionRecord) -> AIEscalationResult:
        interaction = record.llm_interaction
        if interaction is None:
            raise ResolutionError("This transaction has no AI proposal to accept.")
        return AIEscalationResult(
            prompt=interaction["prompt"],
            raw_response=interaction["raw_response"],
            parsed_response=interaction["parsed_response"],
        )

    def accept(
        self,
        transaction_id: str,
        manual_diffs: Optional[List[dict]] = None,
        comment: Optional[str] = None,
    ) -> ResolutionOutcome:
        record = self._get_record(transaction_id)
        if record.user_feedback["decision"] != "PENDING":
            raise ResolutionError(f"Transaction '{transaction_id}' is no longer pending.")
        target = self._verify_input(record)
        rule = activity_rule_from_dict(record.new_change_request["parsed_object"])
        ai_result = self._ai_result(record)
        candidate = self._materialize_ai(self.calendar, rule, target, ai_result)
        diffs = manual_diffs or []

        if diffs:
            # Apply manual diffs against the original source occurrence first,
            # then apply only the AI changes for activities the user did not edit.
            manual_base = apply_schedule_changes(self.calendar, diffs, target)
            manual_ids = {str(item.get("activity_id")) for item in diffs}
            candidate = self._materialize_ai(
                manual_base, rule, target, ai_result, skip_activity_ids=manual_ids
            )
            assert_no_overlaps(candidate, [target] + _change_dates(diffs))

        start, end = self._window(target)
        final_digest = digest_schedule_state(candidate.resolver(), start, end)
        decision = "MODIFY" if diffs else "ACCEPT"
        feedback = {
            "decision": decision,
            "recorded_at": utc_timestamp(),
            "manual_diffs": diffs,
            "comment": comment,
        }
        updated = replace(
            record,
            user_feedback=feedback,
            final_accepted_schedule_digest=final_digest,
        )
        self.calendar = candidate
        self._remember(updated)
        return ResolutionOutcome(updated, candidate, ai_result)

    def modify(
        self,
        transaction_id: str,
        manual_diffs: List[dict],
        comment: Optional[str] = None,
    ) -> ResolutionOutcome:
        if not manual_diffs:
            raise ResolutionError("MODIFY feedback requires at least one manual diff.")
        return self.accept(transaction_id, manual_diffs=manual_diffs, comment=comment)

    def reject(self, transaction_id: str, comment: Optional[str] = None) -> ResolutionOutcome:
        record = self._get_record(transaction_id)
        if record.user_feedback["decision"] != "PENDING":
            raise ResolutionError(f"Transaction '{transaction_id}' is no longer pending.")
        self._verify_input(record)
        updated = replace(
            record,
            user_feedback={
                "decision": "REJECT",
                "recorded_at": utc_timestamp(),
                "manual_diffs": [],
                "comment": comment,
            },
            final_accepted_schedule_digest=record.input_state_digest,
        )
        self._remember(updated)
        return ResolutionOutcome(updated, self.calendar.copy(), self._ai_result(record))

