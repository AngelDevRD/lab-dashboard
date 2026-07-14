import asyncio
import logging
import time

from . import config, events
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
        self.internet_down_since: float | None = None
        self.internet_last_outage: float | None = None
        self._task: asyncio.Task | None = None
        self._running = False

    async def _poll_server(self, server: dict) -> None:
        conn = pool.get(server)
        try:
            snapshot = await collect_server(server, conn)
        except Exception as exc:  # noqa: BLE001
            logger.exception("collector crashed for %s", server.get("host"))
            snapshot = {
                "name": server.get("name", server["host"]),
                "host": server["host"],
                "online": False,
                "last_update": time.time(),
                "error": str(exc),
            }
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
        except Exception:  # noqa: BLE001
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
            servers = config.load_servers()
            await asyncio.gather(*(self._poll_server(s) for s in servers))
            await self._poll_internet(servers)
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

    def snapshot(self) -> dict:
        servers = list(self.servers_status.values())
        online_count = sum(1 for s in servers if s.get("online"))
        return {
            "servers": servers,
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
            "timestamp": time.time(),
        }


monitor = Monitor()
