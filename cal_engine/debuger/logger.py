from __future__ import annotations

import logging
import sys
from typing import TextIO


_LOGGER_NAME = "cal_engine.diagnostics"


def get_logger() -> logging.Logger:
    return logging.getLogger(_LOGGER_NAME)


def configure_logging(enabled: bool, stream: TextIO | None = None) -> logging.Logger:
    """Configure diagnostics without changing the application's root logger."""
    logger = get_logger()
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
        handler.close()
    logger.propagate = False
    logger.disabled = not enabled

    if enabled:
        handler = logging.StreamHandler(stream or sys.stderr)
        handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)
    else:
        logger.addHandler(logging.NullHandler())
        logger.setLevel(logging.CRITICAL + 1)

    return logger
