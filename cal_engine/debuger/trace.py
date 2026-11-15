from __future__ import annotations

from contextlib import contextmanager
from time import perf_counter
from typing import Iterator

from .context import current_context
from .logger import get_logger


@contextmanager
def trace_stage(name: str) -> Iterator[None]:
    context = current_context()
    if context is None:
        yield
        return

    logger = get_logger()
    previous_stage = context.current_stage
    context.current_stage = name
    started_at = perf_counter()
    logger.debug("TRACE start %s", name)
    try:
        yield
    except BaseException as error:
        duration_ms = (perf_counter() - started_at) * 1000
        context.record_stage(name, "failed", duration_ms)
        first_failure = context.failure is None
        context.record_failure(error, name, error.__traceback__)
        if first_failure:
            logger.exception("TRACE failed %s (%.3f ms)", name, duration_ms)
        else:
            logger.error("TRACE failed %s (%.3f ms)", name, duration_ms)
        raise
    else:
        duration_ms = (perf_counter() - started_at) * 1000
        context.record_stage(name, "completed", duration_ms)
        logger.debug("TRACE complete %s (%.3f ms)", name, duration_ms)
    finally:
        context.current_stage = previous_stage
