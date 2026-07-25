import asyncio
import logging
import time
from collections import deque
from datetime import date

from . import config, events
from .alerts.service import notification_service
from .collectors import autonomy
from .collectors.collector import collect_internet, collect_server
from .ssh_client import pool

logger = logging.getLogger("dashboard")


class Monitor:
    def __init__(self):
        self.servers_status: dict[str, dict] = {}
        self.internet_status: dict = {
            "google_ms": None,
            "cloudflare_ms": None,
            "online": False,
        }
        self._prev_online: dict[str, bool] = {}
        self._last_down_since: dict[str, float] = {}
        self._daily_baseline: dict[str, tuple[str, int, int]] = {}
        self._cpu_history: dict[str, deque] = {}
        self._mem_history: dict[str, deque] = {}
        self._autonomy_state: dict[str, autonomy.HostAutonomyState] = {}
        self._server_order: list[str] = []
        self.internet_down_since: float | None = None
        self.internet_last_outage: float | None = None
        self._task: asyncio.Task | None = None
        self._running = False
        self._pushed_devices: dict[str, dict] = {}

    def report_device(self, device_id: str, payload: dict) -> None:
        """Stores a self-reported connectivity snapshot pushed by a device that the
        dashboard cannot reach over SSH (Windows PC, Android tablet)."""
        self._pushed_devices[device_id] = {**payload, "last_seen": time.time()}

    async def _poll_server(self, server: dict) -> None:
        conn = pool.get(server)
        try:
            snapshot = await collect_server(server, conn)
        except Exception as exc:
            logger.exception("collector crashed for %s", server.get("host"))
            snapshot = {
                "name": server.get("name", server["host"]),
                "host": server["host"],
                "online": False,
                "last_update": time.time(),
                "error": str(exc),
            }
        if snapshot.get("online"):
            self._apply_daily_traffic(server["host"], snapshot)
            self._record_history(server["host"], snapshot)
        self.servers_status[server["host"]] = snapshot

        was_online = self._prev_online.get(server["host"])
        is_online = snapshot["online"]
        if was_online is not None and was_online != is_online:
            if is_online:
                events.log_event(
                    "server_up", f"{snapshot['name']} volvió a estar online"
                )
            else:
                events.log_event("server_down", f"{snapshot['name']} dejó de responder")
                self._last_down_since[server["host"]] = time.time()
        self._prev_online[server["host"]] = is_online

        try:
            alert_changes = notification_service.process_server(snapshot)
        except Exception:
            logger.exception("alert processing crashed for %s", server.get("host"))
            alert_changes = []
        for alert in alert_changes:
            if alert.status.value == "active":
                events.log_event(
                    f"alert_{alert.severity.value.lower()}",
                    f"[{alert.severity.value}] {alert.server}: {alert.title}",
                )
            else:
                events.log_event(
                    "alert_resolved",
                    f"[{alert.severity.value}] {alert.server}: {alert.title} - Resuelto",
                )

    def _record_history(self, host: str, snapshot: dict) -> None:
        cpu_pct = snapshot.get("cpu", {}).get("percent")
        mem_pct = snapshot.get("mem", {}).get("percent")
        if cpu_pct is None or mem_pct is None:
            return
        cpu_hist = self._cpu_history.setdefault(host, deque(maxlen=config.HISTORY_LEN))
        mem_hist = self._mem_history.setdefault(host, deque(maxlen=config.HISTORY_LEN))
        cpu_hist.append(cpu_pct)
        mem_hist.append(mem_pct)

        pwr = snapshot.get("power") or {}
        if pwr.get("available"):
            state = self._autonomy_state.setdefault(host, autonomy.HostAutonomyState(host=host))
            autonomy.record_sample(state, time.time(), pwr.get("power_now_w"), pwr.get("energy_now_wh"))

    def _apply_daily_traffic(self, host: str, snapshot: dict) -> None:
        net = snapshot.get("net", {})
        rx_total = net.get("rx_total")
        tx_total = net.get("tx_total")
        if rx_total is None or tx_total is None:
            return
        today = date.today().isoformat()
        baseline = self._daily_baseline.get(host)
        if (
            baseline is None
            or baseline[0] != today
            or rx_total < baseline[1]
            or tx_total < baseline[2]
        ):
            self._daily_baseline[host] = (today, rx_total, tx_total)
            baseline = self._daily_baseline[host]
        _, rx0, tx0 = baseline
        net["daily_download_bytes"] = rx_total - rx0
        net["daily_upload_bytes"] = tx_total - tx0

    async def _poll_internet(self, servers: list[dict]) -> None:
        online_server = next(
            (
                s
                for s in servers
                if self.servers_status.get(s["host"], {}).get("online")
            ),
            None,
        )
        if not online_server:
            self.internet_status = {
                "google_ms": None,
                "cloudflare_ms": None,
                "online": False,
            }
            if self.internet_down_since is None:
                self.internet_down_since = time.time()
            return
        conn = pool.get(online_server)
        try:
            status = await collect_internet(conn)
        except Exception as exc:
            logger.warning("collect_internet falló: %s", exc)
            status = {"google_ms": None, "cloudflare_ms": None, "online": False}
        self.internet_status = status
        if status["online"]:
            if self.internet_down_since is not None:
                events.log_event("internet_up", "Conexión a internet restaurada")
                self.internet_last_outage = self.internet_down_since
                self.internet_down_since = None
        else:
            if self.internet_down_since is None:
                self.internet_down_since = time.time()
                events.log_event("internet_down", "Sin conexión a internet")

    async def _loop(self) -> None:
        while self._running:
            try:
                servers = config.load_servers()
                self._server_order = [s["host"] for s in servers]
                await asyncio.gather(*(self._poll_server(s) for s in servers))
                await self._poll_internet(servers)
            except Exception:
                # A single bad cycle must never kill the whole background loop —
                # that would freeze every server's data forever with no visible
                # error (the task's exception is only surfaced at GC time).
                logger.exception("monitor loop iteration crashed, continuing")
            await asyncio.sleep(config.POLL_INTERVAL)

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        pool.close_all()

    def _connectivity_snapshot(self, servers: list[dict]) -> list[dict]:
        now = time.time()
        devices = []
        for s in servers:
            net = s.get("network")
            if net and net.get("available"):
                devices.append(
                    {"device_id": s["host"], "name": s.get("name", s["host"]), "source": "ssh", **net}
                )
        for device_id, payload in self._pushed_devices.items():
            stale = now - payload.get("last_seen", 0) > config.NETWORK_DEVICE_STALE_SEC
            devices.append(
                {
                    **payload,
                    "device_id": device_id,
                    "source": "push",
                    "status": "stale" if stale else payload.get("status", "ok"),
                }
            )
        return devices

    def autonomy_metrics(self) -> dict:
        """Observabilidad — punto 3: snapshot de las metricas internas del
        algoritmo de autonomia por host, para el endpoint de diagnostico."""
        return {host: autonomy.metrics(state) for host, state in self._autonomy_state.items()}

    def snapshot(self) -> dict:
        ordered_hosts = self._server_order or list(self.servers_status.keys())
        servers = []
        for h in ordered_hosts:
            if h not in self.servers_status:
                continue
            s = dict(self.servers_status[h])
            s["history"] = {
                "cpu": list(self._cpu_history.get(h, [])),
                "mem": list(self._mem_history.get(h, [])),
            }
            state = self._autonomy_state.get(h)
            pwr = s.get("power")
            if state and pwr and pwr.get("available"):
                result = autonomy.estimate(
                    state, pwr.get("status"), pwr.get("energy_now_wh"), pwr.get("energy_full_wh")
                )
                if result["autonomy_seconds"] is not None:
                    s["power"] = {
                        **pwr,
                        "autonomy_seconds": result["autonomy_seconds"],
                        "autonomy_mode": result["mode"],
                    }
            servers.append(s)
        online_count = sum(1 for s in servers if s.get("online"))
        return {
            "servers": servers,
            "connectivity": self._connectivity_snapshot(servers),
            "internet": {
                **self.internet_status,
                "down_since": self.internet_down_since,
                "last_outage": self.internet_last_outage,
            },
            "summary": {
                "total": len(servers),
                "online": online_count,
                "offline": len(servers) - online_count,
            },
            "events": events.recent_events(20),
            "alerts": notification_service.get_alerts_for_snapshot(),
            "alert_count": notification_service.get_active_alerts_count(),
            "timestamp": time.time(),
        }


monitor = Monitor()
