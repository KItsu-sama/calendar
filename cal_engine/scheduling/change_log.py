
"""Append-only log of calendar mutations."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Iterable


def log_change(
    action: str,
    *,
    activities: Iterable[str] = (),
    details: str = "",
    log_path: str | Path = "cal_changes.jsonl",
) -> None:
    """Record a successful mutation, never a read-only operation."""
    record = {
        "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
        "action": action,
        "activities": list(activities),
    }

    if details:
        record["details"] = details

    destination = Path(log_path)
    destination.parent.mkdir(parents=True, exist_ok=True)

    with destination.open("a", encoding="utf-8") as file:
        file.write(json.dumps(record, ensure_ascii=False) + "\n")
