from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from time import perf_counter
import traceback
from types import TracebackType
from typing import Iterator
from contextlib import contextmanager


@dataclass
class DiagnosticContext:
    command: str
    arguments: tuple[str, ...]
    started_at: float = field(default_factory=perf_counter)
    current_stage: str | None = None
    stages: list[dict[str, object]] = field(default_factory=list)
    failure: dict[str, str] | None = None

    def record_stage(self, name: str, status: str, duration_ms: float) -> None:
        self.stages.append({
            "name": name,
            "status": status,
            "duration_ms": round(duration_ms, 3),
        })

    def record_failure(
        self,
        error: BaseException,
        stage: str | None,
        tb: TracebackType | None = None,
    ) -> None:
        if self.failure is not None:
            return

        frames = traceback.extract_tb(tb or error.__traceback__)
        location = "unknown"
        for frame in reversed(frames):
            filename = frame.filename.replace("/", "\\").lower()
            if "\\debuger\\" not in filename:
                location = f"{frame.filename}:{frame.lineno} in {frame.name}"
                break

        self.failure = {
            "type": type(error).__name__,
            "message": str(error),
            "stage": stage or "unknown",
            "location": location,
        }

    def report(self) -> dict[str, object]:
        return {
            "command": self.command,
            "arguments": list(self.arguments),
            "duration_ms": round((perf_counter() - self.started_at) * 1000, 3),
            "stages": list(self.stages),
            "failure": self.failure,
        }


_CURRENT_CONTEXT: ContextVar[DiagnosticContext | None] = ContextVar(
    "cal_engine_diagnostic_context",
    default=None,
)


def current_context() -> DiagnosticContext | None:
    return _CURRENT_CONTEXT.get()


@contextmanager
def diagnostic_context(context: DiagnosticContext) -> Iterator[None]:
    token: Token[DiagnosticContext | None] = _CURRENT_CONTEXT.set(context)
    try:
        yield
    finally:
        _CURRENT_CONTEXT.reset(token)
