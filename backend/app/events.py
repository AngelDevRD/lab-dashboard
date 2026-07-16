import asyncio
import logging
import time
from collections import deque

from . import config

logger = logging.getLogger("dashboard")

config.LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

_recent: deque[dict] = deque(maxlen=200)


def _write_event(event: dict) -> None:
    try:
        with open(config.LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"{event['time']:.0f}\t{event['kind']}\t{event['message']}\n")
    except OSError as exc:
        logger.error("No se pudo escribir al log %s: %s", config.LOG_FILE, exc)


def log_event(kind: str, message: str) -> None:
    event = {"time": time.time(), "kind": kind, "message": message}
    _recent.appendleft(event)
    try:
        loop = asyncio.get_running_loop()
        loop.run_in_executor(None, _write_event, event)
    except RuntimeError:
        _write_event(event)


def recent_events(limit: int = 50) -> list[dict]:
    return list(_recent)[:limit]
