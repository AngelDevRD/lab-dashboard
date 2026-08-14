import asyncio
import logging
import statistics
import time
from collections import deque
from datetime import date

from . import config, events
from .alerts.service import notification_service
from .collectors import autonomy, confidence, energy
from .collectors.collector import collect_internet, collect_server
from .ssh_client import pool

logger = logging.getLogger("dashboard")

_POWER_ORIGIN_FIELDS = ("power_now_w", "energy_now_wh", "energy_full_wh")

# Suavizado del power_now MOSTRADO en el dashboard (no toca el que usa
# autonomy.py para calibrar -- ese ya tiene su propio promedio de 30s con
# una logica auditada aparte, ver autonomy.rolling_power_avg30). Esto es
# solo para que el numero en pantalla no "baile" muestra a muestra:
#   1) mediana sobre una ventana -- descarta picos espurios de una sola
#      lectura mejor que una media (un pico no mueve la mediana).
#   2) EMA sobre esa mediana -- sigue reaccionando rapido a cambios reales
#      sostenidos, pero sin el salto brusco entre dos muestras consecutivas.
POWER_DISPLAY_WINDOW = 20  # ~60s a POLL_INTERVAL=3s
POWER_DISPLAY_EMA_ALPHA = 0.3


def _extract_power_meta(pwr: dict) -> dict:
    """Arma power.meta (procedencia measured/derived/estimated/unavailable
    por campo) a partir de los flags que parsers.py::parse_battery ya decidio
    al parsear. Capa de ensamblado del contrato de la API -- a proposito
    separada de autonomy.py/confidence.py, que son motores de calculo puros
    y no deben conocer el formato final que ve el frontend (ver
    DISEÑO_ARQUITECTURA_DASHBOARD.md)."""
    meta = {}
    for field in _POWER_ORIGIN_FIELDS:
        origin = pwr.pop(f"{field}_origin", None)
        if origin:
            meta[field] = {"origin": origin}
    return meta


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
        # Ring buffer chico (ok/fail de los ultimos ciclos de poll) para el
        # check de "conectividad estable" del Health Score de telemetria —
        # ver AUDITORIA_PRECISION.md. No es un ping externo nuevo, reusa la
        # misma senal online/offline que ya se calcula cada ciclo.
        self._online_history: dict[str, deque] = {}
        self._autonomy_state: dict[str, autonomy.HostAutonomyState] = {}
        self._power_display_history: dict[str, deque] = {}
        self._power_display_ema: dict[str, float] = {}
        self._power_display_status: dict[str, str] = {}
        self._server_order: list[str] = []
        self.internet_down_since: float | None = None
        self.internet_last_outage: float | None = None
        self._task: asyncio.Task | None = None
        self._running = False
        self._pushed_devices: dict[str, dict] = {}
        # snapshot() corre autonomy.estimate + confidence.for_server + copias de
        # histórico por servidor. Antes se recalculaba entero en cada broadcast
        # (BROADCAST_INTERVAL) y en cada GET /api/status -- o sea varias veces
        # entre dos ciclos de poll, sobre exactamente los mismos datos, y una
        # vez más por cada tablet en modo fallback. Ahora se arma una sola vez
        # por ciclo y se reusa hasta que hay datos nuevos.
        self._snapshot_cache: dict | None = None
        # Se dispara cuando hay datos nuevos: el broadcast por WebSocket espera
        # esta señal en vez de despertarse a intervalo fijo, así los clientes
        # reciben un mensaje por ciclo real en lugar de repeticiones idénticas.
        self.updated = asyncio.Event()

    def _invalidate(self) -> None:
        self._snapshot_cache = None
        self.updated.set()

    def report_device(self, device_id: str, payload: dict) -> None:
        """Stores a self-reported connectivity snapshot pushed by a device that the
        dashboard cannot reach over SSH (Windows PC, Android tablet)."""
        self._pushed_devices[device_id] = {**payload, "last_seen": time.time()}
        self._invalidate()

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
        online_hist = self._online_history.setdefault(server["host"], deque(maxlen=20))
        online_hist.append(is_online)
        if was_online is not None and was_online != is_online:
            if is_online:
                events.log_event(
                    "server_up", f"{snapshot['name']} volvió a estar online",
                    host=server["host"],
                )
            else:
                events.log_event(
                    "server_down", f"{snapshot['name']} dejó de responder",
                    host=server["host"],
                )
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
                    host=alert.server_host,
                )
            else:
                events.log_event(
                    "alert_resolved",
                    f"[{alert.severity.value}] {alert.server}: {alert.title} - Resuelto",
                    host=alert.server_host,
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
            self._update_power_display(host, pwr.get("power_now_w"), pwr.get("status"))
        # kWh acumulado: preferir el vatiaje real de bateria: si no hay
        # (equipo sin bateria o bateria danada, ver parsers.parse_cpu_power_rapl
        # / amd_package_power_w), usar el consumo de CPU/paquete como
        # aproximacion -- mejor eso que no medir nada en esos equipos.
        power_for_energy = pwr.get("power_now_w")
        if power_for_energy is None:
            power_for_energy = pwr.get("cpu_power_w")
        energy.record(host, power_for_energy)

    def _update_power_display(self, host: str, raw_power_w: float | None, status: str | None) -> None:
        """Mediana de ventana + EMA, ver comentario junto a POWER_DISPLAY_WINDOW.

        Se resetea el buffer cuando bat_status cambia (Charging <-> Discharging
        <-> Not charging): un cambio de status es un cambio de regimen real,
        no ruido -- validado contra datos reales (evento AC_DISCONNECTED del
        2026-07-30T13:04:57 en samples.csv), donde sin este reset el valor
        mostrado tardaba ~45-50s en reflejar el cambio real (arrastraba la
        mediana de la ventana anterior). Sin el reset, la suavizacion mejora
        el jitter en regimen estable pero empeora la precision justo en el
        momento mas relevante (conectar/desconectar el cargador)."""
        if raw_power_w is None:
            return
        if self._power_display_status.get(host) != status:
            self._power_display_history.pop(host, None)
            self._power_display_ema.pop(host, None)
            self._power_display_status[host] = status
        hist = self._power_display_history.setdefault(host, deque(maxlen=POWER_DISPLAY_WINDOW))
        hist.append(raw_power_w)
        median = statistics.median(hist)
        prev_ema = self._power_display_ema.get(host)
        ema = median if prev_ema is None else prev_ema + POWER_DISPLAY_EMA_ALPHA * (median - prev_ema)
        self._power_display_ema[host] = ema

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
            self._invalidate()
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

    def get_server_status(self, host: str) -> dict | None:
        return self.servers_status.get(host)

    def get_autonomy_mode(self, host: str) -> str | None:
        state = self._autonomy_state.get(host)
        return state.mode.value if state else None

    def autonomy_metrics(self) -> dict:
        """Observabilidad — punto 3: snapshot de las metricas internas del
        algoritmo de autonomia por host, para el endpoint de diagnostico."""
        return {host: autonomy.metrics(state) for host, state in self._autonomy_state.items()}

    def snapshot(self) -> dict:
        if self._snapshot_cache is None:
            self._snapshot_cache = self._build_snapshot()
        return self._snapshot_cache

    def _build_snapshot(self) -> dict:
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
            autonomy_result = None
            if pwr:
                pwr = dict(pwr)  # copia local: no mutar el dict compartido en servers_status
                power_meta = _extract_power_meta(pwr)
                if state and pwr.get("available"):
                    autonomy_result = autonomy.estimate(
                        state, pwr.get("status"), pwr.get("energy_now_wh"), pwr.get("energy_full_wh"),
                        capacity_pct=pwr.get("percent"),
                    )
                    power_meta["autonomy_seconds"] = {
                        "origin": "estimated",
                        "model": "power_constante_calibrado_por_divergencia (autonomy.py)",
                    }
                    # power_now_w pasa a ser el valor suavizado (mediana+EMA,
                    # ver POWER_DISPLAY_WINDOW) -- es lo que ve el usuario.
                    # power_now_raw_w conserva la lectura instantanea cruda
                    # de esta muestra, por si hace falta compararlas. Ambos
                    # comparten la misma procedencia fisica (measured/derived
                    # segun parse_battery); el mostrado ademas queda marcado
                    # "smoothed" en su meta.
                    raw_power_w = pwr.get("power_now_w")
                    displayed_power = self._power_display_ema.get(h)
                    smoothed_power_w = (
                        round(displayed_power, 2) if displayed_power is not None else raw_power_w
                    )
                    if "power_now_w" in power_meta:
                        power_meta["power_now_raw_w"] = power_meta["power_now_w"]
                        power_meta["power_now_w"] = {**power_meta["power_now_w"], "smoothed": True}
                    pwr.update({
                        "power_now_raw_w": raw_power_w,
                        "power_now_w": smoothed_power_w,
                        "autonomy_seconds": autonomy_result["autonomy_seconds"],
                        "autonomy_mode": autonomy_result["mode"],
                        "age_s": autonomy_result["power_age_s"],
                        "validated": autonomy_result["validated"],
                        "complete_discharges": autonomy_result["complete_discharges"],
                        "reliability": autonomy_result["reliability"],
                    })
                pwr["energy_kwh_total"] = energy.get_kwh(h)
                pwr["meta"] = power_meta
                s["power"] = pwr
            online_hist = self._online_history.get(h, deque())
            online_ratio = (sum(online_hist) / len(online_hist)) if online_hist else None
            s["confidence"] = confidence.for_server(
                s,
                power_age_s=autonomy_result["power_age_s"] if autonomy_result else None,
                autonomy_result=autonomy_result,
                cpu_history=list(self._cpu_history.get(h, []))[-5:],
                mem_history=list(self._mem_history.get(h, []))[-5:],
                clock_offset_s=s.get("clock_offset_s"),
                online_ratio=online_ratio,
            )
            s["telemetry_health"] = confidence.telemetry_health(s["confidence"])
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
