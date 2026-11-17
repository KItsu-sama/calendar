from __future__ import annotations

import logging
import os
import sys
from typing import TextIO


_LOGGER_NAME = "cal_engine.debuger"

_RESET = "\033[0m"
_LEVEL_COLORS = {
    logging.DEBUG: "\033[36m",
    logging.INFO: "\033[32m",
    logging.WARNING: "\033[33m",
    logging.ERROR: "\033[31m",
    logging.CRITICAL: "\033[1;31m",
}


def _enable_windows_ansi(stream: TextIO) -> None:
    if os.name != "nt":
        return
    try:
        import ctypes

        handle = ctypes.windll.kernel32.GetStdHandle(-12)  # STD_ERROR_HANDLE
        mode = ctypes.c_uint32()
        if ctypes.windll.kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            ctypes.windll.kernel32.SetConsoleMode(handle, mode.value | 0x0004)
    except Exception:
        return


class _ColorFormatter(logging.Formatter):
    def __init__(self, use_color: bool) -> None:
        super().__init__("%(levelname)s %(message)s")
        self.use_color = use_color

    def format(self, record: logging.LogRecord) -> str:
        message = super().format(record)
        if not self.use_color:
            return message
        color = _LEVEL_COLORS.get(record.levelno, "")
        if not color:
            return message
        return f"{color}{message}{_RESET}"


def get_logger() -> logging.Logger:
    return logging.getLogger(_LOGGER_NAME)


def configure_logging(enabled: bool, stream: TextIO | None = None) -> logging.Logger:
    """Configure debuger without changing the application's root logger."""
    logger = get_logger()
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
        handler.close()
    logger.propagate = False
    logger.disabled = not enabled

    if enabled:
        target = stream or sys.stderr
        use_color = bool(getattr(target, "isatty", lambda: False)()) and "NO_COLOR" not in os.environ
        if use_color:
            _enable_windows_ansi(target)
        handler = logging.StreamHandler(target)
        handler.setFormatter(_ColorFormatter(use_color))
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)
    else:
        logger.addHandler(logging.NullHandler())
        logger.setLevel(logging.CRITICAL + 1)

    return logger
