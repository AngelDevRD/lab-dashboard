import logging
import time
from collections import deque

from . import config

logger = logging.getLogger("dashboard")

config.LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

_recent: deque[dict] = deque(maxlen=200)


def log_event(kind: str, message: str) -> None:
    event = {"time": time.time(), "kind": kind, "message": message}
    _recent.appendleft(event)
    try:
        with open(config.LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"{event['time']:.0f}\t{kind}\t{message}\n")
    except OSError as exc:
        logger.error("No se pudo escribir al log %s: %s", config.LOG_FILE, exc)


def recent_events(limit: int = 50) -> list[dict]:
    return list(_recent)[:limit]
