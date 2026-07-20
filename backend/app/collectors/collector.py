import time

from .. import config
from ..ssh_client import SSHConnection, classify_ssh_error
from . import parsers
from .commands import COMMANDS, HOSTNAME_CMD, INTERNET_COMMANDS, SLOW_COMMAND_TTLS

# host -> {command_key: (fetched_at, (ok, output))}
_slow_cache: dict[str, dict[str, tuple[float, tuple[bool, str]]]] = {}


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

    cache = _slow_cache.setdefault(host, {})
    commands = {
        key: cmd
        for key, cmd in COMMANDS.items()
        if key not in SLOW_COMMAND_TTLS
        or key not in cache
        or now - cache[key][0] >= SLOW_COMMAND_TTLS[key]
    }
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
        "updates_pending": parsers.parse_updates(out("updates")),
        "services": parsers.parse_services(out("services"), config.KNOWN_SERVICES),
        "power": parsers.parse_battery(out("battery")),
        "disk_temp": parsers.parse_disk_temp(out("disk_temp")),
        "top_cpu": parsers.parse_top_procs(out("top_cpu")),
        "top_mem": parsers.parse_top_procs(out("top_mem")),
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
