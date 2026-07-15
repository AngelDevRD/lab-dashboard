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
}

INTERNET_COMMANDS = {
    "ping_google": "ping -c 1 -W 2 8.8.8.8 2>/dev/null | tail -1",
    "ping_cloudflare": "ping -c 1 -W 2 1.1.1.1 2>/dev/null | tail -1",
}
