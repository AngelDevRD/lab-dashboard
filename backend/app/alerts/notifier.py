import asyncio
import logging
import os
import time
from collections import deque

logger = logging.getLogger("dashboard")

NTFY_TOPIC = os.getenv("NTFY_TOPIC", "lab-dashboard-alerts")
NTFY_URL = os.getenv("NTFY_URL", "https://ntfy.sh")

# Simple sliding-window rate limiter so a burst of simultaneous alerts (e.g.
# many servers going down at once) can't flood the ntfy topic / phone.
NTFY_MAX_PER_WINDOW = int(os.getenv("NTFY_MAX_PER_WINDOW", "20"))
NTFY_RATE_WINDOW_SECONDS = float(os.getenv("NTFY_RATE_WINDOW_SECONDS", "60"))

NTFY_MAX_RETRIES = 3
NTFY_BACKOFF_BASE = 1.0

_send_timestamps: deque[float] = deque()
_rate_lock = asyncio.Lock()


async def _rate_limit_ok() -> bool:
    async with _rate_lock:
        now = time.time()
        while _send_timestamps and now - _send_timestamps[0] > NTFY_RATE_WINDOW_SECONDS:
            _send_timestamps.popleft()
        if len(_send_timestamps) >= NTFY_MAX_PER_WINDOW:
            return False
        _send_timestamps.append(now)
        return True


async def send_ntfy(title: str, message: str, priority: int = 4, tags: list[str] | None = None) -> None:
    """Send a push notification via ntfy.

    Never raises: a failure here must not be able to stop the monitoring
    loop. Retries transient failures with exponential backoff; gives up
    (logging why) rather than blocking indefinitely if ntfy is down.
    """
    title = (title or "(sin título)").strip()[:256]
    message = (message or "(sin mensaje)").strip()[:4096]
    priority = max(1, min(5, priority if isinstance(priority, int) else 3))
    tags = list(dict.fromkeys(tags or ["warning"]))  # de-dup while preserving order

    if not await _rate_limit_ok():
        logger.warning(
            "ntfy rate limit alcanzado (%d/%.0fs), notificación descartada: %s",
            NTFY_MAX_PER_WINDOW, NTFY_RATE_WINDOW_SECONDS, title,
        )
        return

    try:
        import aiohttp
    except ImportError:
        logger.warning("aiohttp no instalado, no se puede enviar ntfy: %s", title)
        return

    # Publish to the bare ntfy root, with the topic inside the JSON body.
    # ntfy only parses a JSON body into separate title/message/tags fields
    # when there's no topic in the URL path — POSTing JSON to "/{topic}"
    # makes it treat the whole JSON string as literal message text instead.
    url = NTFY_URL
    payload = {
        "topic": NTFY_TOPIC,
        "title": title,
        "message": message,
        "priority": priority,
        "tags": tags,
    }

    for attempt in range(1, NTFY_MAX_RETRIES + 1):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status in (200, 201):
                        logger.info("ntfy enviado: %s", title)
                        return
                    body = await resp.text()
                    if 400 <= resp.status < 500:
                        # Bad request / auth / not-found — retrying won't help.
                        logger.error("ntfy rechazó la notificación (%s): %s", resp.status, body)
                        return
                    logger.warning(
                        "ntfy respondió %s (intento %d/%d): %s",
                        resp.status, attempt, NTFY_MAX_RETRIES, body,
                    )
        except (asyncio.TimeoutError, TimeoutError) as exc:
            logger.warning("ntfy timeout (intento %d/%d): %s", attempt, NTFY_MAX_RETRIES, exc)
        except Exception as exc:
            logger.warning("Error enviando ntfy (intento %d/%d): %s", attempt, NTFY_MAX_RETRIES, exc)

        if attempt < NTFY_MAX_RETRIES:
            await asyncio.sleep(NTFY_BACKOFF_BASE * (2 ** (attempt - 1)))

    logger.error("ntfy: se agotaron los reintentos, notificación perdida: %s", title)


PRIORITY_MAP = {
    "INFO": 2,
    "WARNING": 3,
    "CRITICAL": 4,
    "EMERGENCY": 5,
}

TAG_MAP = {
    "cpu": ["computer"],
    "ram": ["floppy_disk"],
    "disk": ["cd"],
    "docker": ["whale"],
    "temp": ["thermometer"],
    "services": ["service_dog"],
    "power": ["battery"],
    "network": ["globe_with_meridians"],
}

RECOVERY_TAGS = ["white_check_mark"]


def _build_tags(category: str, severity: str, recovered: bool) -> list[str]:
    # Copy the list — TAG_MAP values must stay untouched, otherwise appending
    # here would permanently grow the shared list for every future alert in
    # this category (this used to duplicate "rotating_light" indefinitely).
    tags = list(TAG_MAP.get(category, ["warning"]))
    if recovered:
        tags = RECOVERY_TAGS + tags
    elif severity in ("CRITICAL", "EMERGENCY"):
        tags.append("rotating_light")
    return tags


def _format_message(alert) -> str:
    msg = alert.description or ""
    if alert.current_value is not None and alert.threshold_value is not None:
        msg += f"\nActual: {alert.current_value} | Límite: {alert.threshold_value}"
    return msg


async def notify_alert(alert) -> None:
    """Notify that an alert became active."""
    if alert is None or not alert.title or not alert.server_host:
        logger.warning("notify_alert: alerta inválida, descartada: %r", alert)
        return

    sev = alert.severity.value if hasattr(alert.severity, "value") else str(alert.severity)
    priority = PRIORITY_MAP.get(sev, 3)
    tags = _build_tags(alert.category, sev, recovered=False)
    server = alert.server or alert.server_host or "unknown"
    title = f"[{sev}] {server} - {alert.title}"

    await send_ntfy(title, _format_message(alert), priority=priority, tags=tags)


async def notify_recovery(alert) -> None:
    """Notify that a previously active alert has cleared."""
    if alert is None or not alert.title or not alert.server_host:
        logger.warning("notify_recovery: alerta inválida, descartada: %r", alert)
        return

    tags = _build_tags(alert.category, "INFO", recovered=True)
    server = alert.server or alert.server_host or "unknown"
    title = f"[OK] {server} - {alert.title} resuelto"
    msg = f"{alert.description}\nEl problema ya no está presente."

    await send_ntfy(title, msg, priority=PRIORITY_MAP["INFO"], tags=tags)
