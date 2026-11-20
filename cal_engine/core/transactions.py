"""Canonical hashing and transaction records for dataset capture."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

from jsonschema import validate as _jsonschema_validate

from .resolver import ScheduleResolver

_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_TIMESTAMP_PATTERN = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"


class TransactionError(ValueError):
    """A transaction record or transaction log is invalid."""


def canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest_state(value: object) -> str:
    """Return a stable SHA-256 digest for any JSON-compatible state object."""
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def schedule_state_snapshot(
    resolver: ScheduleResolver, start_date: date, end_date: date
) -> dict:
    """Serialize rules, exceptions, materialized intervals, and the query window."""
    return {
        "version": 1,
        "window": {"start_date": start_date.isoformat(), "end_date": end_date.isoformat()},
        "rules": sorted((rule.to_dict() for rule in resolver.rules), key=lambda item: item["id"]),
        "exceptions": sorted(
            (exception.to_dict() for exception in resolver.exceptions),
            key=lambda item: (item["rule_id"], item["target_date"], item["id"]),
        ),
        "schedule": [item.to_dict() for item in resolver.build_actual_schedule(start_date, end_date)],
    }


def digest_schedule_state(resolver: ScheduleResolver, start_date: date, end_date: date) -> str:
    return digest_state(schedule_state_snapshot(resolver, start_date, end_date))


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


_TRANSACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "transaction_id": {"type": "string", "minLength": 1},
        "timestamp": {"type": "string", "pattern": _TIMESTAMP_PATTERN},
        "input_state_digest": {"type": "string", "pattern": _SHA256_PATTERN},
        "new_change_request": {
            "type": "object",
            "properties": {
                "raw_command": {"type": "string"},
                "parsed_object": {"type": "object"},
                "target_date": {"type": "string", "format": "date"},
            },
            "required": ["raw_command", "parsed_object", "target_date"],
        },
        "conflict_detection": {
            "type": "object",
            "properties": {
                "status": {"type": "string"},
                "conflicts": {"type": "array", "items": {"type": "object"}},
            },
            "required": ["status", "conflicts"],
        },
        "local_solver_result": {
            "type": "object",
            "properties": {
                "status": {"type": "string"},
                "changes": {"type": "array"},
                "reason": {"type": ["string", "null"]},
            },
            "required": ["status", "changes", "reason"],
        },
        "llm_interaction": {
            "oneOf": [
                {"type": "null"},
                {
                    "type": "object",
                    "properties": {
                        "prompt": {"type": "string"},
                        "raw_response": {"type": "string"},
                        "parsed_response": {"type": "object"},
                    },
                    "required": ["prompt", "raw_response", "parsed_response"],
                },
            ]
        },
        "user_feedback": {
            "type": "object",
            "properties": {
                "decision": {"enum": ["PENDING", "ACCEPT", "MODIFY", "REJECT"]},
                "recorded_at": {"type": "string", "pattern": _TIMESTAMP_PATTERN},
                "manual_diffs": {"type": "array"},
                "comment": {"type": ["string", "null"]},
            },
            "required": ["decision", "recorded_at", "manual_diffs", "comment"],
        },
        "final_accepted_schedule_digest": {
            "oneOf": [{"type": "null"}, {"type": "string", "pattern": _SHA256_PATTERN}]
        },
    },
    "required": [
        "transaction_id",
        "timestamp",
        "input_state_digest",
        "new_change_request",
        "conflict_detection",
        "local_solver_result",
        "llm_interaction",
        "user_feedback",
        "final_accepted_schedule_digest",
    ],
}


@dataclass
class TransactionRecord:
    transaction_id: str
    timestamp: str
    input_state_digest: str
    new_change_request: dict
    conflict_detection: dict
    local_solver_result: dict
    llm_interaction: Optional[dict]
    user_feedback: dict
    final_accepted_schedule_digest: Optional[str]

    def to_dict(self) -> dict:
        return {
            "transaction_id": self.transaction_id,
            "timestamp": self.timestamp,
            "input_state_digest": self.input_state_digest,
            "new_change_request": self.new_change_request,
            "conflict_detection": self.conflict_detection,
            "local_solver_result": self.local_solver_result,
            "llm_interaction": self.llm_interaction,
            "user_feedback": self.user_feedback,
            "final_accepted_schedule_digest": self.final_accepted_schedule_digest,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TransactionRecord":
        validate_transaction_record(data)
        return cls(
            transaction_id=data["transaction_id"],
            timestamp=data["timestamp"],
            input_state_digest=data["input_state_digest"],
            new_change_request=data["new_change_request"],
            conflict_detection=data["conflict_detection"],
            local_solver_result=data["local_solver_result"],
            llm_interaction=data["llm_interaction"],
            user_feedback=data["user_feedback"],
            final_accepted_schedule_digest=data["final_accepted_schedule_digest"],
        )


def validate_transaction_record(data: dict) -> None:
    try:
        _jsonschema_validate(instance=data, schema=_TRANSACTION_SCHEMA)
    except Exception as error:
        raise TransactionError(f"Invalid transaction record: {error}") from None


class TransactionLog:
    """Append-only JSONL log. A later line supersedes an earlier feedback state."""

    def __init__(self, path: Optional[str | Path] = None):
        self.path = Path(path) if path is not None else None

    def append(self, record: TransactionRecord) -> None:
        data = record.to_dict()
        validate_transaction_record(data)
        if self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(data, sort_keys=True) + "\n")
        except OSError as error:
            raise TransactionError(f"Could not append transaction log '{self.path}': {error}") from None

    def read_all(self) -> list[TransactionRecord]:
        if self.path is None or not self.path.exists():
            return []
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError as error:
            raise TransactionError(f"Could not read transaction log '{self.path}': {error}") from None
        records: list[TransactionRecord] = []
        for number, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            try:
                records.append(TransactionRecord.from_dict(json.loads(line)))
            except (json.JSONDecodeError, TransactionError) as error:
                raise TransactionError(f"Invalid transaction log line {number}: {error}") from None
        return records

    def latest(self, transaction_id: str) -> TransactionRecord:
        matches = [item for item in self.read_all() if item.transaction_id == transaction_id]
        if not matches:
            raise TransactionError(f"Unknown transaction '{transaction_id}'.")
        return matches[-1]

