import json
import logging
import time
from pathlib import Path
from typing import Optional

from .models import Alert, AlertStatus

logger = logging.getLogger("dashboard")

ALERTS_FILE = Path("/logs/alerts_history.json")


class AlertCenter:
    def __init__(self, max_history: int = 500):
        self._alerts: dict[str, Alert] = {}
        self._order: list[str] = []
        self._max = max_history
        self._load()

    def _load(self) -> None:
        if not ALERTS_FILE.exists():
            return
        try:
            with open(ALERTS_FILE) as f:
                data = json.load(f)
            for item in data:
                try:
                    alert = Alert(**item)
                    self._alerts[alert.id] = alert
                    self._order.append(alert.id)
                except Exception:
                    continue
            self._order = self._order[-self._max:]
        except Exception as e:
            logger.error("Failed to load alert history: %s", e)

    def _save(self) -> None:
        try:
            data = [a.model_dump() for a in self.get_all()]
            with open(ALERTS_FILE, "w") as f:
                json.dump(data[-self._max:], f, indent=2, default=str)
        except Exception as e:
            logger.error("Failed to save alert history: %s", e)

    def add(self, alert: Alert) -> None:
        self._alerts[alert.id] = alert
        self._order.append(alert.id)
        if len(self._order) > self._max:
            old = self._order.pop(0)
            self._alerts.pop(old, None)
        self._save()

    def update(self, alert: Alert) -> None:
        self._alerts[alert.id] = alert
        self._save()

    def get(self, alert_id: str) -> Optional[Alert]:
        return self._alerts.get(alert_id)

    def get_all(self) -> list[Alert]:
        return [self._alerts[aid] for aid in self._order if aid in self._alerts]

    def get_active(self) -> list[Alert]:
        return [a for a in self.get_all() if a.status == AlertStatus.ACTIVE]

    def get_by_server(self, host: str) -> list[Alert]:
        return [a for a in self.get_all() if a.server_host == host]

    def get_by_severity(self, severity: str) -> list[Alert]:
        return [a for a in self.get_all() if a.severity.value == severity.upper()]

    def count(self) -> dict:
        all_alerts = self.get_all()
        active = sum(1 for a in all_alerts if a.status == AlertStatus.ACTIVE)
        return {
            "total": len(all_alerts),
            "active": active,
            "resolved": sum(1 for a in all_alerts if a.status == AlertStatus.RESOLVED),
            "info": sum(1 for a in all_alerts if a.severity.value == "INFO"),
            "warning": sum(1 for a in all_alerts if a.severity.value == "WARNING"),
            "critical": sum(1 for a in all_alerts if a.severity.value == "CRITICAL"),
            "emergency": sum(1 for a in all_alerts if a.severity.value == "EMERGENCY"),
        }


alert_center = AlertCenter()
