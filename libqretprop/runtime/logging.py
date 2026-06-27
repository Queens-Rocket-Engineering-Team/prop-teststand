from __future__ import annotations
import logging as stdlib_logging
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from colorama import Fore, Style, just_fix_windows_console

from libqretprop.runtime.log_stream import LogStream, strip_ansi


LOGGER_NAME = __name__.partition(".")[0]
LEVEL_COLORS = {
    stdlib_logging.DEBUG: Fore.LIGHTYELLOW_EX,
    stdlib_logging.INFO: Fore.LIGHTBLACK_EX,
    stdlib_logging.WARNING: Fore.LIGHTYELLOW_EX,
    stdlib_logging.ERROR: Fore.LIGHTRED_EX,
    stdlib_logging.CRITICAL: Fore.LIGHTRED_EX,
}
TIMESTAMP_COLOR = Fore.LIGHTBLACK_EX


_project_logger = stdlib_logging.getLogger(LOGGER_NAME)
if not _project_logger.handlers:
    _project_logger.addHandler(stdlib_logging.NullHandler())
_project_logger.propagate = False


def _apply_color(message: str, color: str) -> str:
    if not color:
        return message
    return f"{color}{message}{Style.RESET_ALL}"


def _timestamp() -> str:
    return datetime.now(ZoneInfo("America/New_York")).strftime("%H:%M:%S")


class LogFormatter(stdlib_logging.Formatter):
    """Format records with timestamp and optional ANSI color."""

    def __init__(self, *, color: bool) -> None:
        super().__init__()
        self._color = color

    def format(self, record: stdlib_logging.LogRecord) -> str:
        message = record.getMessage()
        timestamp_text = f"[{_timestamp()}]"
        if self._color:
            timestamp_text = _apply_color(timestamp_text, TIMESTAMP_COLOR)
            message = _apply_color(message, LEVEL_COLORS.get(record.levelno, ""))
        return f"{timestamp_text} {message}"


class WebSocketLogHandler(stdlib_logging.Handler):
    """Logging handler that hands formatted records to LogStream's thread-safe ingress."""

    def __init__(self, stream: LogStream) -> None:
        super().__init__()
        self._stream = stream

    def emit(self, record: stdlib_logging.LogRecord) -> None:
        try:
            data = self.format(record)
            self._stream.enqueue(
                {
                    "level": record.levelname,
                    "data": strip_ansi(data),
                    "timestamp_ws": _timestamp(),
                },
            )
        except Exception:
            self.handleError(record)


def configure_logging(stream: LogStream, *, stdout_level: int | str | None = None) -> None:
    """Configure project logging for stdout and WebSocket fan-out."""
    just_fix_windows_console()

    if stdout_level is None:
        stdout_level = os.getenv("PROP_LOG_LEVEL", "INFO")

    logger = stdlib_logging.getLogger(LOGGER_NAME)
    logger.handlers.clear()
    logger.setLevel(stdlib_logging.DEBUG)
    logger.propagate = False

    stdout_handler = stdlib_logging.StreamHandler(sys.stdout)
    stdout_handler.setLevel(stdout_level)
    stdout_handler.setFormatter(LogFormatter(color=True))

    ws_handler = WebSocketLogHandler(stream)
    ws_handler.setLevel(stdlib_logging.DEBUG)
    ws_handler.setFormatter(LogFormatter(color=True))

    logger.addHandler(stdout_handler)
    logger.addHandler(ws_handler)
