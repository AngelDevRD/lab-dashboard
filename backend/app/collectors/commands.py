"""All remote shell commands executed over SSH, kept in one place so they're easy to audit."""

COMMANDS = {
    "uptime": "uptime -p 2>/dev/null; cat /proc/uptime",
    "loadavg": "cat /proc/loadavg",
    "cpu": "grep 'cpu ' /proc/stat; nproc",
    "cpu_temp": (
        "sensors -j 2>/dev/null || "
        "cat /sys/class/thermal/thermal_zone*/temp 2>/dev/null"
    ),
    "mem": "cat /proc/meminfo",
    "disk": "df -B1 --output=size,used,avail,target -x tmpfs -x devtmpfs -x overlay 2>/dev/null | grep -E '/$'",
    "net_ip": "hostname -I 2>/dev/null | awk '{print $1}'",
    "net_io": "cat /proc/net/dev",
    "docker": (
        "docker ps -a --format '{{.Names}}|{{.State}}|{{.Status}}' 2>/dev/null || echo '__NO_DOCKER__'"
    ),
    "docker_disk": (
        "docker system df --format '{{.Type}}|{{.Size}}' 2>/dev/null || echo '__NO_DOCKER__'"
    ),
    "updates": "apt list --upgradable 2>/dev/null | tail -n +2 | wc -l",
    "services": (
        "systemctl is-active ssh docker tailscaled nginx fastapi postgresql "
        "redis-server portainer uptime-kuma 2>/dev/null"
    ),
    "battery": (
        "for f in /sys/class/power_supply/BAT*; do "
        '[ -d "$f" ] && echo "$(cat $f/capacity 2>/dev/null)|$(cat $f/status 2>/dev/null)|$(cat $f/voltage_now 2>/dev/null)"; '
        "done"
    ),
    "disk_temp": "smartctl -A /dev/sda 2>/dev/null | grep -i temperature | head -1",
    "top_cpu": "ps -eo pid,comm,%cpu --sort=-%cpu --no-headers | head -5",
    "top_mem": "ps -eo pid,comm,%mem --sort=-%mem --no-headers | head -5",
    "hostname": "hostname",
    "network_status": "cat /etc/network-guardian/status.json 2>/dev/null || echo '{}'",
}

# Run alone, before the rest of the batch: a fast, cheap round-trip used to
# measure real connection latency and connectivity. It must never be mixed
# into the timed batch above, since some of those commands (smartctl, apt,
# docker) can legitimately take seconds and would inflate "latency" with
# unrelated local command time instead of network RTT.
HOSTNAME_CMD = "hostname"

# Expensive commands whose output rarely changes: re-run only after their TTL
# (seconds) expires instead of on every poll cycle.
SLOW_COMMAND_TTLS = {
    "updates": 1800,
    "docker_disk": 300,
    "disk_temp": 600,
}

INTERNET_COMMANDS = {
    "ping_google": "ping -c 1 -W 2 8.8.8.8 2>/dev/null | tail -1",
    "ping_cloudflare": "ping -c 1 -W 2 1.1.1.1 2>/dev/null | tail -1",
}
