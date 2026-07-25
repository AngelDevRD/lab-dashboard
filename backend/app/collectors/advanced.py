import time

from ..ssh_client import SSHConnection
from . import parsers
from .commands import ADVANCED_COMMANDS


async def collect_advanced(host: str, conn: SSHConnection) -> dict:
    """Runs the expensive/rarely-needed commands for one server's detail panel.

    Called only while that server's panel is open (see /api/servers/{host}/advanced),
    never from the continuous monitor loop.
    """
    now = time.time()
    results = await conn.run_many(ADVANCED_COMMANDS)

    def out(key: str) -> str:
        return results.get(key, (False, ""))[1]

    return {
        "host": host,
        "fetched_at": now,
        "cpu_freq": parsers.parse_cpu_freq(out("cpu_freq")),
        "disk_io": parsers.parse_disk_io(out("disk_stats"), host, now),
        "disk_smart": parsers.parse_smart_health(out("smart")),
        "docker_stats": parsers.parse_docker_stats(out("docker_stats")),
        "network_extra": {
            **parsers.parse_ping_extended(out("ping_loss")),
            "tailscale_ip": parsers.parse_tailscale_ip(out("tailscale_ip")),
        },
        "system": parsers.parse_system_info(out("system_info")),
    }
