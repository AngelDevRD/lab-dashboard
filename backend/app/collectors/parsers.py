"""Pure parsing functions: raw command output -> plain dicts. No I/O here."""

import json
import re


def parse_uptime(raw: str) -> dict:
    lines = raw.strip().splitlines()
    pretty = lines[0].strip() if lines else "N/A"
    seconds = 0.0
    if len(lines) > 1:
        try:
            seconds = float(lines[1].split()[0])
        except (ValueError, IndexError):
            pass
    return {"pretty": pretty or "N/A", "seconds": seconds}


def parse_loadavg(raw: str) -> dict:
    parts = raw.strip().split()
    if len(parts) < 3:
        return {"load1": 0.0, "load5": 0.0, "load15": 0.0}
    return {
        "load1": float(parts[0]),
        "load5": float(parts[1]),
        "load15": float(parts[2]),
    }


_prev_cpu_samples: dict[str, tuple[int, int]] = {}


def parse_cpu(raw: str, host: str) -> dict:
    lines = raw.strip().splitlines()
    cpu_line = next((l for l in lines if l.startswith("cpu ")), None)
    nproc = 1
    for l in lines:
        if l.strip().isdigit():
            nproc = int(l.strip())
            break
    if not cpu_line:
        return {"percent": 0.0, "cores": nproc}
    values = [int(v) for v in cpu_line.split()[1:]]
    idle = values[3] + (values[4] if len(values) > 4 else 0)
    total = sum(values)

    prev = _prev_cpu_samples.get(host)
    _prev_cpu_samples[host] = (idle, total)
    if not prev:
        return {"percent": 0.0, "cores": nproc}
    prev_idle, prev_total = prev
    delta_idle = idle - prev_idle
    delta_total = total - prev_total
    if delta_total <= 0:
        return {"percent": 0.0, "cores": nproc}
    percent = round((1 - delta_idle / delta_total) * 100, 1)
    return {"percent": max(0.0, min(100.0, percent)), "cores": nproc}


def parse_cpu_temp(raw: str) -> float | None:
    raw = raw.strip()
    if not raw:
        return None
    try:
        data = json.loads(raw)
        for chip in data.values():
            for sensor in chip.values():
                if isinstance(sensor, dict):
                    for k, v in sensor.items():
                        if "input" in k and isinstance(v, (int, float)):
                            return round(float(v), 1)
    except (json.JSONDecodeError, AttributeError):
        pass
    nums = [int(x) for x in raw.splitlines() if x.strip().isdigit()]
    if nums:
        return round(nums[0] / 1000, 1)
    return None


def parse_mem(raw: str) -> dict:
    info = {}
    for line in raw.strip().splitlines():
        m = re.match(r"(\w+):\s+(\d+)", line)
        if m:
            info[m.group(1)] = int(m.group(2)) * 1024  # kB -> bytes
    total = info.get("MemTotal", 0)
    available = info.get("MemAvailable", info.get("MemFree", 0))
    used = max(0, total - available)
    percent = round(used / total * 100, 1) if total else 0.0
    return {"total": total, "used": used, "free": available, "percent": percent}


def parse_disk(raw: str) -> dict:
    line = raw.strip().splitlines()[0] if raw.strip() else ""
    parts = line.split()
    if len(parts) < 3:
        return {"total": 0, "used": 0, "free": 0, "percent": 0.0}
    total, used, free = int(parts[0]), int(parts[1]), int(parts[2])
    percent = round(used / total * 100, 1) if total else 0.0
    return {"total": total, "used": used, "free": free, "percent": percent}


_prev_net_samples: dict[str, tuple[float, int, int]] = {}


def parse_net_io(raw: str, host: str, now: float) -> dict:
    rx_total = tx_total = 0
    for line in raw.strip().splitlines():
        if ":" not in line:
            continue
        iface, rest = line.split(":", 1)
        iface = iface.strip()
        if iface == "lo":
            continue
        fields = rest.split()
        if len(fields) < 9:
            continue
        rx_total += int(fields[0])
        tx_total += int(fields[8])

    prev = _prev_net_samples.get(host)
    _prev_net_samples[host] = (now, rx_total, tx_total)
    if not prev:
        return {"download_bps": 0, "upload_bps": 0}
    prev_time, prev_rx, prev_tx = prev
    dt = max(now - prev_time, 0.001)
    download_bps = max(0, (rx_total - prev_rx) / dt)
    upload_bps = max(0, (tx_total - prev_tx) / dt)
    return {"download_bps": round(download_bps), "upload_bps": round(upload_bps)}


def parse_docker(raw: str) -> dict:
    raw = raw.strip()
    if not raw or raw == "__NO_DOCKER__":
        return {"available": False, "running": 0, "stopped": 0, "containers": []}
    containers = []
    running = stopped = 0
    for line in raw.splitlines():
        parts = line.split("|")
        if len(parts) != 3:
            continue
        name, state, status = parts
        containers.append({"name": name, "state": state, "status": status})
        if state == "running":
            running += 1
        else:
            stopped += 1
    return {
        "available": True,
        "running": running,
        "stopped": stopped,
        "containers": containers,
    }


def parse_services(raw: str, service_names: list[str]) -> dict:
    lines = raw.strip().splitlines()
    result = {}
    for i, name in enumerate(service_names):
        state = lines[i].strip() if i < len(lines) else "unknown"
        result[name] = state == "active"
    return result


def parse_battery(raw: str) -> dict:
    raw = raw.strip()
    if not raw:
        return {"available": False}
    line = raw.splitlines()[0]
    parts = line.split("|")
    if len(parts) < 3:
        return {"available": False}
    capacity, status, voltage = parts
    try:
        voltage_v = round(int(voltage) / 1_000_000, 2) if voltage.isdigit() else None
    except ValueError:
        voltage_v = None
    return {
        "available": True,
        "percent": int(capacity) if capacity.isdigit() else None,
        "status": status or "unknown",
        "voltage": voltage_v,
    }


def parse_disk_temp(raw: str) -> float | None:
    m = re.search(r"(\d+)\s*$", raw.strip())
    if m:
        return float(m.group(1))
    return None


def parse_top_procs(raw: str) -> list[dict]:
    procs = []
    for line in raw.strip().splitlines():
        parts = line.split(None, 2)
        if len(parts) == 3:
            pid, name, pct = parts
            try:
                procs.append({"pid": int(pid), "name": name, "value": float(pct)})
            except ValueError:
                continue
    return procs


def parse_ping(raw: str) -> float | None:
    m = re.search(r"time=([\d.]+)", raw)
    if m:
        return round(float(m.group(1)), 1)
    return None
