import asyncio
import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Optional

from .models import Alert, AlertStatus
from .center import alert_center
from .engine import evaluate_server
from .notifier import notify_alert, notify_recovery
from .sound_alarm import play_battery_alarm
from .thresholds import threshold_manager

logger = logging.getLogger("dashboard")

NOTIFIABLE_SEVERITIES = ("CRITICAL", "EMERGENCY")

STATE_FILE = Path("/logs/notification_state.json")


def _hash_id(server_host: str, category: str, title: str) -> str:
    raw = f"{server_host}:{category}:{title}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


class NotificationService:
    """Turns raw rule hits into deduplicated, debounced Alert lifecycle events.

    Per alert_id (server+category+title) state machine:
      absent -> pending (breach seen, below hysteresis threshold)
      pending -> active (breach held for `hysteresis_cycles` consecutive polls
                 -> Alert created, notified once if severity warrants it)
      active -> active (still breaching -> no repeat notification, except the
                 explicit retry schedule for a few high-value categories)
      active -> clearing (condition absent, below hysteresis threshold)
      clearing -> resolved (absence held for `hysteresis_cycles` consecutive
                 polls -> single recovery notification, then cooldown before
                 the same alert_id may fire again)

    Note on offline hosts: rules.py's network_rule is the one that yields the
    "Servidor desconectado" alert, and it fires precisely when
    server["online"] is False — so evaluate_server() must always run, even for
    offline snapshots, or that alert (and its notification) would never be
    produced. All other rules already guard on missing keys and simply don't
    yield when the metrics aren't available, so calling evaluate_server
    unconditionally is safe.
    """

    RETRY_CONFIG = {
        "power": {"max_sends": 5, "interval": 600},
    }

    def __init__(self):
        self._active_keys: dict[str, Alert] = {}
        self._recently_resolved: dict[str, float] = {}
        self._send_count: dict[str, int] = {}
        self._last_sent: dict[str, float] = {}
        self._breach_counts: dict[str, int] = {}
        self._clear_counts: dict[str, int] = {}
        self._load_state()

    def _load_state(self) -> None:
        """Restore dedup state across restarts.

        Without this, every container restart (redeploy, crash, the 30-min
        auto-update timer) forgets which alerts were already notified and
        re-sends a fresh notification for a problem that never stopped being
        active — the exact duplicate-notification bug this class exists to
        prevent, just triggered by a restart instead of a noisy metric.
        """
        if not STATE_FILE.exists():
            return
        try:
            data = json.loads(STATE_FILE.read_text())
            for item in data.get("active", []):
                try:
                    alert_id = item["alert"]["id"]
                    self._active_keys[alert_id] = Alert(**item["alert"])
                    self._last_sent[alert_id] = item.get("last_sent", time.time())
                    self._send_count[alert_id] = item.get("send_count", 1)
                except Exception:
                    continue
            self._recently_resolved = {
                k: v for k, v in data.get("recently_resolved", {}).items()
            }
        except Exception as e:
            logger.error("Error loading notification state: %s", e)

    def _save_state(self) -> None:
        try:
            data = {
                "active": [
                    {
                        "alert": self._active_keys[aid].model_dump(mode="json"),
                        "last_sent": self._last_sent.get(aid, 0),
                        "send_count": self._send_count.get(aid, 1),
                    }
                    for aid in self._active_keys
                ],
                "recently_resolved": self._recently_resolved,
            }
            STATE_FILE.write_text(json.dumps(data, indent=2, default=str))
        except Exception as e:
            logger.error("Error saving notification state: %s", e)

    def process_server(self, server: dict) -> list[Alert]:
        host = server.get("host", "unknown")
        online = bool(server.get("online"))
        settings = threshold_manager.settings
        cooldown = settings.alert_cooldown_seconds
        hysteresis = max(1, settings.hysteresis_cycles)

        raw_alerts = evaluate_server(server)
        current_keys: set[str] = set()
        result: list[Alert] = []

        for raw in raw_alerts:
            alert_id = _hash_id(host, raw.get("category", ""), raw.get("title", ""))
            current_keys.add(alert_id)
            self._clear_counts.pop(alert_id, None)

            existing = self._active_keys.get(alert_id)
            if existing:
                self._maybe_retry(alert_id, existing)
                continue

            breach_count = self._breach_counts.get(alert_id, 0) + 1
            self._breach_counts[alert_id] = breach_count
            if breach_count < hysteresis:
                continue  # still debouncing: not enough consecutive breaches yet

            resolved_at = self._recently_resolved.get(alert_id)
            if resolved_at is not None:
                if time.time() - resolved_at < cooldown:
                    continue
                self._recently_resolved.pop(alert_id, None)

            result.append(self._activate(alert_id, host, server, raw))

        # Anything not seen this cycle resets its breach debounce so a future
        # flap needs a fresh run of consecutive hits, not a stale partial count.
        for alert_id in list(self._breach_counts):
            if alert_id not in current_keys and alert_id not in self._active_keys:
                self._breach_counts.pop(alert_id, None)

        result.extend(self._process_clearing(host, current_keys, hysteresis, online))
        return result

    def _activate(self, alert_id: str, host: str, server: dict, raw: dict) -> Alert:
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
        self._breach_counts.pop(alert_id, None)
        self._last_sent[alert_id] = time.time()
        self._send_count[alert_id] = 1
        alert_center.add(alert)
        self._save_state()
        if alert.severity.value in NOTIFIABLE_SEVERITIES:
            self._fire(notify_alert(alert), "notificación", alert.title)
            if alert.category == "power":
                self._fire(play_battery_alarm(host, alert.current_value), "alarma sonora", alert.title)
        return alert

    def _maybe_retry(self, alert_id: str, existing: Alert) -> None:
        retry = self.RETRY_CONFIG.get(existing.category)
        if not retry or existing.severity.value not in NOTIFIABLE_SEVERITIES:
            return
        now = time.time()
        last = self._last_sent.get(alert_id, 0)
        count = self._send_count.get(alert_id, 0)
        if count < retry["max_sends"] and now - last >= retry["interval"]:
            self._last_sent[alert_id] = now
            self._send_count[alert_id] = count + 1
            self._save_state()
            self._fire(notify_alert(existing), "reintento", existing.title)
            if existing.category == "power":
                self._fire(
                    play_battery_alarm(existing.server_host, existing.current_value),
                    "alarma sonora", existing.title,
                )
            logger.info(
                "Re-enviando alerta %s (%d/%d)", alert_id, count + 1, retry["max_sends"],
            )

    def _process_clearing(
        self, host: str, current_keys: set[str], hysteresis: int, online: bool
    ) -> list[Alert]:
        resolved: list[Alert] = []
        for alert_id, alert in list(self._active_keys.items()):
            if alert.server_host != host or alert_id in current_keys:
                continue

            if not online and alert.category != "network":
                # Host unreachable: we have no data to confirm this metric
                # actually recovered, so clear it immediately without a
                # misleading "recovered" push — the "network" alert itself
                # (created above via network_rule) is what pages for the
                # outage instead.
                resolved.append(self._resolve(alert_id, alert, notify=False))
                continue

            clear_count = self._clear_counts.get(alert_id, 0) + 1
            self._clear_counts[alert_id] = clear_count
            if clear_count < hysteresis:
                continue  # debouncing the recovery too, avoids flapping resolves
            resolved.append(self._resolve(alert_id, alert, notify=True))
        return resolved

    def _resolve(self, alert_id: str, alert: Alert, notify: bool = True) -> Alert:
        alert.status = AlertStatus.RESOLVED
        alert.resolved_at = time.time()
        alert_center.update(alert)
        self._recently_resolved[alert_id] = time.time()
        self._active_keys.pop(alert_id, None)
        self._send_count.pop(alert_id, None)
        self._last_sent.pop(alert_id, None)
        self._clear_counts.pop(alert_id, None)
        self._save_state()
        if notify and alert.severity.value in NOTIFIABLE_SEVERITIES:
            self._fire(notify_recovery(alert), "recuperación", alert.title)
        return alert

    @staticmethod
    def _fire(coro, kind: str, title: str) -> None:
        try:
            asyncio.ensure_future(coro)
        except RuntimeError:
            coro.close()
            logger.warning("No hay event loop activo, %s no enviada: %s", kind, title)

    def manually_resolve(self, alert_id: str) -> Optional[Alert]:
        alert = self._active_keys.get(alert_id)
        if not alert:
            return None
        return self._resolve(alert_id, alert)

    def get_active_alerts(self) -> list[Alert]:
        return list(self._active_keys.values())

    def get_alerts_for_snapshot(self) -> list[dict]:
        return [a.model_dump() for a in self.get_active_alerts()]

    def get_active_alerts_count(self) -> int:
        return len(self._active_keys)


notification_service = NotificationService()
