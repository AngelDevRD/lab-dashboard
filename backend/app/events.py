import asyncio
import logging
import time
from collections import deque
from logging.handlers import RotatingFileHandler

from . import config

logger = logging.getLogger("dashboard")

config.LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

_recent: deque[dict] = deque(maxlen=200)

_file_logger = logging.getLogger("dashboard.events_file")
_file_logger.setLevel(logging.INFO)
_file_logger.propagate = False
_file_handler = RotatingFileHandler(
    config.LOG_FILE,
    maxBytes=config.LOG_FILE_MAX_BYTES,
    backupCount=config.LOG_FILE_BACKUP_COUNT,
    encoding="utf-8",
)
_file_handler.setFormatter(logging.Formatter("%(message)s"))
_file_logger.addHandler(_file_handler)


def _write_event(event: dict) -> None:
    try:
        _file_logger.info(f"{event['time']:.0f}\t{event['kind']}\t{event['message']}")
    except OSError as exc:
        logger.error("No se pudo escribir al log %s: %s", config.LOG_FILE, exc)


def log_event(kind: str, message: str, host: str | None = None) -> None:
    event = {"time": time.time(), "kind": kind, "message": message, "host": host}
    _recent.appendleft(event)
    try:
        loop = asyncio.get_running_loop()
        loop.run_in_executor(None, _write_event, event)
    except RuntimeError:
        _write_event(event)


def recent_events(limit: int = 50) -> list[dict]:
    return list(_recent)[:limit]
