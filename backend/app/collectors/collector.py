import time

from .. import config
from ..ssh_client import SSHConnection, classify_ssh_error
from . import parsers
from .commands import CLOCK_CMD, COMMANDS, HOSTNAME_CMD, INTERNET_COMMANDS, SLOW_COMMAND_TTLS

# host -> {command_key: (fetched_at, (ok, output))}
_slow_cache: dict[str, dict[str, tuple[float, tuple[bool, str]]]] = {}

# host -> (fetched_at, offset_s). Separado de _slow_cache porque esta
# medicion necesita su propio round-trip cronometrado (ver
# _measure_clock_offset), no puede compartir el "now" del batch de comandos.
_clock_cache: dict[str, tuple[float, float | None]] = {}
CLOCK_CHECK_TTL_S = 300


async def _measure_clock_offset(host: str, conn: SSHConnection, now: float) -> float | None:
    """Offset de reloj compensado por RTT (estilo NTP): mide t0 justo antes
    de mandar el comando y t1 justo despues de la respuesta, y usa el punto
    medio como "cuando" se tomo la lectura remota. Un round-trip dedicado,
    separado del batch de COMMANDS, para no mezclar el desfasaje de reloj
    real con el tiempo que tarda el resto de la tanda (revision de
    precision de la auditoria)."""
    cached = _clock_cache.get(host)
    if cached and now - cached[0] < CLOCK_CHECK_TTL_S:
        return cached[1]
    t0 = time.time()
    ok, raw = await conn.run(CLOCK_CMD)
    t1 = time.time()
    offset = parsers.parse_clock(raw, (t0 + t1) / 2) if ok else None
    _clock_cache[host] = (now, offset)
    return offset


async def collect_server(server: dict, conn: SSHConnection) -> dict:
    host = server["host"]
    now = time.time()

    # Connectivity and latency are measured from this single, cheap round-trip
    # only. It must never be conflated with the time spent running the rest of
    # the (potentially slow) data-collection commands below — otherwise a slow
    # local command (e.g. smartctl, apt) or a connect timeout gets reported as
    # "network latency", which produces bogus values like 6000+ ms and false
    # "high latency" alerts instead of a clear timeout/unreachable state.
    started = time.monotonic()
    conn_ok, hostname_out = await conn.run(HOSTNAME_CMD)
    latency_ms = round((time.monotonic() - started) * 1000)

    if not conn_ok:
        reason = classify_ssh_error(hostname_out)
        return {
            "name": server.get("name", host),
            "host": host,
            "online": False,
            "last_update": now,
            "error": hostname_out,
            "offline_reason": reason,
            # No latency_ms here on purpose: a failed/timed-out probe has no
            # meaningful latency value and must not be treated as one.
        }

    clock_offset_s = await _measure_clock_offset(host, conn, now)

    cache = _slow_cache.setdefault(host, {})
    commands = {
        key: cmd
        for key, cmd in COMMANDS.items()
        if key not in SLOW_COMMAND_TTLS
        or key not in cache
        or now - cache[key][0] >= SLOW_COMMAND_TTLS[key]
    }
    # Hosts con un stack distinto al Docker estandar del lab (ej. .7, PC de IA
    # local) declaran su propia lista de servicios en servers.json en vez de
    # heredar KNOWN_SERVICES -- si no, systemd_rule reporta "caido" para
    # servicios que nunca existieron ahi (docker, nginx, postgresql, ...) y
    # nunca se entera de los que si corren (llm-api, llm-runtime, ...).
    service_names = server.get("services") or config.KNOWN_SERVICES
    if "services" in commands:
        commands["services"] = "systemctl is-active " + " ".join(service_names) + " 2>/dev/null"
    results = await conn.run_many(commands)

    for key in SLOW_COMMAND_TTLS:
        if key in results:
            if results[key][0]:
                cache[key] = (now, results[key])
        elif key in cache:
            results[key] = cache[key][1]

    def out(key: str) -> str:
        return results.get(key, (False, ""))[1]

    cpu_temp = parsers.parse_cpu_temp(out("cpu_temp"))
    rapl_power_w = parsers.parse_cpu_power_rapl(out("cpu_power_rapl"))
    # Fallback de vatios cuando no hay bateria funcional para medirlos:
    # Intel (RAPL) primero, AMD (fam15h_power, ya viene en cpu_temp) despues.
    # No reemplaza power_now_w real de bateria -- monitor.py decide cuando
    # usar cada uno.
    cpu_power_fallback_w = rapl_power_w if rapl_power_w is not None else cpu_temp.get("amd_package_power_w")
    power = parsers.parse_battery(out("battery"), host)
    power["cpu_power_w"] = cpu_power_fallback_w
    snapshot = {
        "name": server.get("name", host),
        "host": host,
        "online": True,
        "last_update": now,
        "latency_ms": latency_ms,
        "uptime": parsers.parse_uptime(out("uptime")),
        "load": parsers.parse_loadavg(out("loadavg")),
        "cpu": {
            **parsers.parse_cpu(out("cpu"), host),
            "temp": cpu_temp["value"],
            "temp_per_core": cpu_temp["per_core"],
        },
        "mem": parsers.parse_mem(out("mem")),
        "disk": parsers.parse_disk(out("disk")),
        "net": {
            "ip": out("net_ip").strip() or server["host"],
            **parsers.parse_net_io(out("net_io"), host, now),
        },
        "docker": parsers.parse_docker(out("docker")),
        "docker_disk": parsers.parse_docker_disk(out("docker_disk")),
        "monitor_docker": server.get("monitor_docker", True),
        "updates_pending": parsers.parse_updates(out("updates")),
        "services": parsers.parse_services(out("services"), service_names),
        "power": power,
        "disk_temp": parsers.parse_disk_temp(out("disk_temp")),
        "clock_offset_s": clock_offset_s,
        "top_cpu": parsers.parse_top_procs(out("top_cpu")),
        "top_mem": parsers.parse_top_procs(out("top_mem")),
        "network": parsers.parse_network_status(out("network_status")),
    }
    return snapshot


async def collect_internet(conn: SSHConnection) -> dict:
    """Uses the first reachable server as a vantage point to test outbound internet."""
    results = await conn.run_many(INTERNET_COMMANDS)
    google_ok, google_raw = results.get("ping_google", (False, ""))
    cf_ok, cf_raw = results.get("ping_cloudflare", (False, ""))
    return {
        "google_ms": parsers.parse_ping(google_raw) if google_ok else None,
        "cloudflare_ms": parsers.parse_ping(cf_raw) if cf_ok else None,
        "online": bool(parsers.parse_ping(google_raw) or parsers.parse_ping(cf_raw)),
    }
