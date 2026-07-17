import asyncio
import hashlib
import logging
import time
from typing import Optional

from .models import Alert, AlertStatus
from .center import alert_center
from .engine import evaluate_server
from .notifier import notify_alert

logger = logging.getLogger("dashboard")


def _hash_id(server_host: str, category: str, title: str) -> str:
    raw = f"{server_host}:{category}:{title}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


class NotificationService:
    RETRY_CONFIG = {
        "power": {"max_sends": 5, "interval": 600},
    }

    def __init__(self):
        self._active_keys: dict[str, Alert] = {}
        self._recently_resolved: dict[str, float] = {}
        self._send_count: dict[str, int] = {}
        self._last_sent: dict[str, float] = {}

    def process_server(self, server: dict) -> list[Alert]:
        host = server.get("host", "unknown")
        if not server.get("online"):
            self._resolve_all_for_host(host)
            return []

        raw_alerts = evaluate_server(server)
        current_keys: set[str] = set()
        result: list[Alert] = []
        cooldown = 300

        for raw in raw_alerts:
            alert_id = _hash_id(host, raw.get("category", ""), raw.get("title", ""))
            current_keys.add(alert_id)

            existing = self._active_keys.get(alert_id)
            if existing:
                retry = self.RETRY_CONFIG.get(raw.get("category"))
                if retry and existing.severity.value in ("CRITICAL", "EMERGENCY"):
                    now = time.time()
                    last = self._last_sent.get(alert_id, 0)
                    count = self._send_count.get(alert_id, 0)
                    if count < retry["max_sends"] and now - last >= retry["interval"]:
                        self._last_sent[alert_id] = now
                        self._send_count[alert_id] = count + 1
                        asyncio.ensure_future(notify_alert(existing))
                        logger.info(
                            "Re-enviando alerta %s (%d/%d)",
                            alert_id, count + 1, retry["max_sends"],
                        )
                continue

            resolved_at = self._recently_resolved.get(alert_id)
            if resolved_at is not None:
                if time.time() - resolved_at < cooldown:
                    continue
                self._recently_resolved.pop(alert_id, None)

            alert = Alert(
                id=alert_id,
                server=raw.get("server", server.get("name", host)),
                server_host=host,
                severity=raw["severity"],
                category=raw["category"],
                title=raw["title"],
                description=raw["description"],
                current_value=raw.get("current_value"),
                threshold_value=raw.get("threshold_value"),
            )
            self._active_keys[alert_id] = alert
            self._last_sent[alert_id] = time.time()
            self._send_count[alert_id] = 1
            alert_center.add(alert)
            result.append(alert)
            if alert.severity.value in ("CRITICAL", "EMERGENCY"):
                asyncio.ensure_future(notify_alert(alert))

        for alert_id, alert in list(self._active_keys.items()):
            if alert.server_host != host:
                continue
            if alert_id not in current_keys:
                alert.status = AlertStatus.RESOLVED
                alert.resolved_at = time.time()
                alert_center.update(alert)
                self._recently_resolved[alert_id] = time.time()
                del self._active_keys[alert_id]
                self._send_count.pop(alert_id, None)
                self._last_sent.pop(alert_id, None)
                result.append(alert)

        return result

    def _resolve_all_for_host(self, host: str) -> list[Alert]:
        resolved = []
        for alert_id, alert in list(self._active_keys.items()):
            if alert.server_host == host:
                alert.status = AlertStatus.RESOLVED
                alert.resolved_at = time.time()
                alert_center.update(alert)
                self._recently_resolved[alert_id] = time.time()
                del self._active_keys[alert_id]
                resolved.append(alert)
        return resolved

    def manually_resolve(self, alert_id: str) -> Optional[Alert]:
        alert = self._active_keys.pop(alert_id, None)
        if alert:
            alert.status = AlertStatus.RESOLVED
            alert.resolved_at = time.time()
            alert_center.update(alert)
            self._recently_resolved[alert_id] = time.time()
        return alert

    def get_active_alerts(self) -> list[Alert]:
        return list(self._active_keys.values())

    def get_alerts_for_snapshot(self) -> list[dict]:
        return [a.model_dump() for a in self.get_active_alerts()]

    def get_active_alerts_count(self) -> int:
        return len(self._active_keys)


notification_service = NotificationService()
