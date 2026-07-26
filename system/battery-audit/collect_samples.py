"""Continuous battery/power sampler for the autonomy-algorithm audit.

Runs forever (meant to be a systemd service, see battery-audit.service),
appending one pipe-delimited row every SAMPLE_INTERVAL_S seconds to an
output file. Same 26-field schema as the original 10/40-min manual audits
(sample_data/raw_*.txt, live_sample_data/live_*.txt) so the existing
analysis tools work unchanged against however much history has piled up:

    python3 live_audit_analyze.py <dir-with-the-output-file> <out-dir>
    python3 simulate_hybrid.py    (point it at the new file the same way)

No third-party dependencies (stdlib only) — nothing to pip install on a
box that's meant to run this unattended for weeks.

Deliberately dumb/robust: every reading falls back to "NA" instead of
raising, because a single unreadable sysfs file (firmware quirk, a
battery driver hiccup) must never kill a capture that's supposed to run
for days — losing one field in one row is fine, losing the whole
process isn't.
"""

import glob
import os
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

SAMPLE_INTERVAL_S = float(os.environ.get("BATTERY_AUDIT_INTERVAL_S", "10"))
OUTPUT_FILE = Path(
    os.environ.get("BATTERY_AUDIT_OUTPUT", "/var/log/battery-audit/live_samples.txt")
)

FIELDS = [
    "elapsed", "timestamp", "capacity", "status", "voltage_now", "current_now",
    "power_now", "energy_now", "energy_full", "charge_now", "charge_full",
    "tte", "ttf", "ac_online", "cpu_line", "cpu_freq", "temp", "mem_total",
    "mem_avail", "disk_read_sectors", "disk_write_sectors", "net_rx", "net_tx",
    "docker_count", "docker_cpu_pct", "docker_mem",
]


def na(v):
    return v if v not in (None, "") else "NA"


def read_file(path):
    try:
        return Path(path).read_text().strip()
    except OSError:
        return None


def battery_dir():
    dirs = sorted(glob.glob("/sys/class/power_supply/BAT*"))
    return dirs[0] if dirs else None


def ac_online():
    for d in sorted(glob.glob("/sys/class/power_supply/A*")):
        v = read_file(f"{d}/online")
        if v is not None:
            return v
    return "0"


def cpu_line():
    raw = read_file("/proc/stat") or ""
    for line in raw.splitlines():
        if line.startswith("cpu "):
            return line
    return "NA"


def cpu_freq_khz():
    return read_file("/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq")


def cpu_temp_milli():
    for zone in sorted(glob.glob("/sys/class/thermal/thermal_zone*/temp")):
        v = read_file(zone)
        if v is not None:
            return v
    return None


def meminfo():
    raw = read_file("/proc/meminfo") or ""
    total = avail = None
    for line in raw.splitlines():
        m = re.match(r"MemTotal:\s+(\d+)", line)
        if m:
            total = m.group(1)
        m = re.match(r"MemAvailable:\s+(\d+)", line)
        if m:
            avail = m.group(1)
    return total, avail


def disk_sectors():
    raw = read_file("/proc/diskstats") or ""
    read_sec = write_sec = 0
    found = False
    for line in raw.splitlines():
        parts = line.split()
        if len(parts) < 10:
            continue
        name = parts[2]
        if not re.match(r"^(sd[a-z]+|vd[a-z]+|hd[a-z]+|nvme\d+n\d+|mmcblk\d+)$", name):
            continue
        read_sec += int(parts[5])
        write_sec += int(parts[9])
        found = True
    return (str(read_sec), str(write_sec)) if found else (None, None)


def net_bytes():
    raw = read_file("/proc/net/dev") or ""
    rx = tx = 0
    found = False
    for line in raw.splitlines():
        if ":" not in line:
            continue
        iface, rest = line.split(":", 1)
        iface = iface.strip()
        if iface == "lo":
            continue
        fields = rest.split()
        if len(fields) < 9:
            continue
        rx += int(fields[0])
        tx += int(fields[8])
        found = True
    return (str(rx), str(tx)) if found else (None, None)


def docker_stats():
    try:
        out = subprocess.run(
            ["docker", "stats", "--no-stream", "--format", "{{.CPUPerc}}|{{.MemUsage}}"],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None, None, None
    if not out:
        return "0", "0.00%", "0MiB"
    lines = out.splitlines()
    cpu_total = 0.0
    mem_used_mib = 0.0
    for line in lines:
        parts = line.split("|")
        if len(parts) != 2:
            continue
        cpu_str, mem_str = parts
        try:
            cpu_total += float(cpu_str.strip().rstrip("%"))
        except ValueError:
            pass
        m = re.match(r"([\d.]+)\s*MiB", mem_str.strip())
        if m:
            mem_used_mib += float(m.group(1))
    return str(len(lines)), f"{cpu_total:.2f}%", f"{mem_used_mib:.2f}MiB"


def sample(start_monotonic):
    bat = battery_dir()

    def bat_field(name):
        return read_file(f"{bat}/{name}") if bat else None

    total, avail = meminfo()
    read_sec, write_sec = disk_sectors()
    rx, tx = net_bytes()
    docker_count, docker_cpu, docker_mem = docker_stats()

    row = {
        "elapsed": f"{time.monotonic() - start_monotonic:.3f}",
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
        "capacity": bat_field("capacity"),
        "status": bat_field("status"),
        "voltage_now": bat_field("voltage_now"),
        "current_now": bat_field("current_now"),
        "power_now": bat_field("power_now"),
        "energy_now": bat_field("energy_now"),
        "energy_full": bat_field("energy_full"),
        "charge_now": bat_field("charge_now"),
        "charge_full": bat_field("charge_full"),
        "tte": bat_field("time_to_empty_now"),
        "ttf": bat_field("time_to_full_now"),
        "ac_online": ac_online(),
        "cpu_line": cpu_line(),
        "cpu_freq": cpu_freq_khz(),
        "temp": cpu_temp_milli(),
        "mem_total": total,
        "mem_avail": avail,
        "disk_read_sectors": read_sec,
        "disk_write_sectors": write_sec,
        "net_rx": rx,
        "net_tx": tx,
        "docker_count": docker_count,
        "docker_cpu_pct": docker_cpu,
        "docker_mem": docker_mem,
    }
    return "|".join(na(row[f]) for f in FIELDS)


def main():
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    start_monotonic = time.monotonic()
    with open(OUTPUT_FILE, "a", buffering=1) as f:
        while True:
            cycle_start = time.monotonic()
            try:
                f.write(sample(start_monotonic) + "\n")
                f.flush()
                os.fsync(f.fileno())
            except Exception as exc:
                # A single bad cycle (transient I/O error, sysfs race on
                # suspend/resume) must never kill a capture meant to run
                # unattended for weeks.
                print(f"battery-audit: sample failed, continuing: {exc}", flush=True)
            elapsed = time.monotonic() - cycle_start
            time.sleep(max(0.0, SAMPLE_INTERVAL_S - elapsed))


if __name__ == "__main__":
    main()
