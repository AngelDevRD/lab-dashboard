import asyncio
import logging
import os

logger = logging.getLogger("dashboard")

NTFY_TOPIC = os.getenv("NTFY_TOPIC", "lab-dashboard-alerts")
NTFY_URL = os.getenv("NTFY_URL", "https://ntfy.sh")


async def send_ntfy(title: str, message: str, priority: int = 4, tags: list[str] | None = None) -> None:
    try:
        import aiohttp
        # Publish to the bare ntfy root, with the topic inside the JSON body.
        # ntfy only parses a JSON body into separate title/message/tags fields
        # when there's no topic in the URL path -- POSTing JSON to "/{topic}"
        # makes it treat the whole JSON string as literal message text instead.
        url = NTFY_URL
        payload = {
            "topic": NTFY_TOPIC,
            "title": title[:256],
            "message": message[:4096],
            "priority": priority,
            "tags": tags or ["warning"],
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status not in (200, 201):
                    logger.warning("ntfy responded %s: %s", resp.status, await resp.text())
                else:
                    logger.info("ntfy sent: %s", title)
    except ImportError:
        logger.warning("aiohttp no instalado, no se puede enviar ntfy")
    except Exception as e:
        logger.error("Error sending ntfy: %s", e)


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


async def notify_alert(alert) -> None:
    sev = alert.severity.value if hasattr(alert.severity, "value") else str(alert.severity)
    priority = PRIORITY_MAP.get(sev, 3)
    tags = TAG_MAP.get(alert.category, ["warning"])
    if sev in ("CRITICAL", "EMERGENCY"):
        tags.append("rotating_light")

    server = alert.server or alert.server_host or "unknown"
    title = f"[{sev}] {server} - {alert.title}"
    msg = alert.description
    if alert.current_value is not None and alert.threshold_value is not None:
        msg += f"\nActual: {alert.current_value} | Límite: {alert.threshold_value}"

    await send_ntfy(title, msg, priority=priority, tags=tags)
