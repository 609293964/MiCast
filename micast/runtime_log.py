"""Bounded in-memory application logs for the diagnostics UI.

Nothing is written to disk: the buffer lives for the lifetime of the process
and the diagnostics page polls it.
"""

from __future__ import annotations

import logging
from collections import deque
from datetime import datetime
from threading import Lock


class RuntimeLogHandler(logging.Handler):
    def __init__(self, capacity: int = 2000):
        super().__init__(logging.INFO)
        self._records: deque[dict[str, str]] = deque(maxlen=capacity)
        self._lock = Lock()

    def emit(self, record: logging.LogRecord) -> None:
        item = {
            "time": datetime.fromtimestamp(record.created).strftime("%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "message": self.format(record),
        }
        with self._lock:
            self._records.append(item)

    def snapshot(self, limit: int = 500) -> list[dict[str, str]]:
        with self._lock:
            return list(self._records)[-limit:]


runtime_logs = RuntimeLogHandler()
runtime_logs.setFormatter(logging.Formatter("%(message)s"))


def install_runtime_log() -> None:
    root = logging.getLogger()
    if runtime_logs not in root.handlers:
        root.addHandler(runtime_logs)
    root.setLevel(min(root.level or logging.INFO, logging.INFO))


def install_asyncio_exception_filter(loop) -> None:
    """Hide harmless Windows connection resets while preserving real loop errors."""

    def handle_exception(_loop, context: dict) -> None:
        error = context.get("exception")
        if isinstance(error, ConnectionResetError) and getattr(error, "winerror", None) == 10054:
            logging.getLogger("micast.network").debug(
                "Peer closed a network connection during cleanup"
            )
            return
        _loop.default_exception_handler(context)

    loop.set_exception_handler(handle_exception)
