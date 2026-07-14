import time

from .. import config
from ..ssh_client import SSHConnection
from . import parsers
from .commands import COMMANDS, INTERNET_COMMANDS


async def collect_server(server: dict, conn: SSHConnection) -> dict:
    host = server["host"]
    now = time.time()
    results = await conn.run_many(COMMANDS)

    online = results.get("hostname", (False, ""))[0]
    if not online:
        return {
            "name": server.get("name", host),
            "host": host,
            "online": False,
            "last_update": now,
            "error": results.get("hostname", (False, "unreachable"))[1],
        }

    def out(key: str) -> str:
        return results.get(key, (False, ""))[1]

    snapshot = {
        "name": server.get("name", host),
        "host": host,
        "online": True,
        "last_update": now,
        "uptime": parsers.parse_uptime(out("uptime")),
        "load": parsers.parse_loadavg(out("loadavg")),
        "cpu": {
            **parsers.parse_cpu(out("cpu"), host),
            "temp": parsers.parse_cpu_temp(out("cpu_temp")),
        },
        "mem": parsers.parse_mem(out("mem")),
        "disk": parsers.parse_disk(out("disk")),
        "net": {
            "ip": out("net_ip").strip() or server["host"],
            **parsers.parse_net_io(out("net_io"), host, now),
        },
        "docker": parsers.parse_docker(out("docker")),
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
