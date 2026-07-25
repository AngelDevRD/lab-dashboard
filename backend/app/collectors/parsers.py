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


def parse_cpu_temp(raw: str) -> dict:
    """Returns {"value": <primary/avg temp>, "per_core": [temps...]}."""
    raw = raw.strip()
    if not raw:
        return {"value": None, "per_core": []}
    try:
        data = json.loads(raw)
        per_core: list[float] = []
        for chip in data.values():
            for label, sensor in chip.items():
                if not isinstance(sensor, dict):
                    continue
                for k, v in sensor.items():
                    if "input" in k and isinstance(v, (int, float)):
                        if re.search(r"core|cpu", label, re.IGNORECASE):
                            per_core.append(round(float(v), 1))
                        elif not per_core:
                            per_core.append(round(float(v), 1))
        if per_core:
            return {
                "value": round(sum(per_core) / len(per_core), 1),
                "per_core": per_core,
            }
    except (json.JSONDecodeError, AttributeError):
        pass
    nums = [int(x) for x in raw.splitlines() if x.strip().isdigit()]
    if nums:
        temps = [round(n / 1000, 1) for n in nums]
        return {"value": temps[0], "per_core": temps}
    return {"value": None, "per_core": []}


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
    swap_total = info.get("SwapTotal", 0)
    swap_free = info.get("SwapFree", 0)
    swap_used = max(0, swap_total - swap_free)
    swap_percent = round(swap_used / swap_total * 100, 1) if swap_total else 0.0
    return {
        "total": total,
        "used": used,
        "free": available,
        "percent": percent,
        "swap": {"total": swap_total, "used": swap_used, "percent": swap_percent},
    }


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
    result = {"rx_total": rx_total, "tx_total": tx_total}
    if not prev:
        return {**result, "download_bps": 0, "upload_bps": 0}
    prev_time, prev_rx, prev_tx = prev
    dt = max(now - prev_time, 0.001)
    download_bps = max(0, (rx_total - prev_rx) / dt)
    upload_bps = max(0, (tx_total - prev_tx) / dt)
    return {
        **result,
        "download_bps": round(download_bps),
        "upload_bps": round(upload_bps),
    }


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
    (
        capacity, status, voltage,
        energy_now, energy_full, power_now,
        time_to_empty, time_to_full,
        charge_now, charge_full, current_now,
    ) = (
        parts[0], parts[1], parts[2],
        parts[3] if len(parts) > 3 else "",
        parts[4] if len(parts) > 4 else "",
        parts[5] if len(parts) > 5 else "",
        parts[6] if len(parts) > 6 else "",
        parts[7] if len(parts) > 7 else "",
        parts[8] if len(parts) > 8 else "",
        parts[9] if len(parts) > 9 else "",
        parts[10] if len(parts) > 10 else "",
    )
    try:
        voltage_v = round(int(voltage) / 1_000_000, 2) if voltage.isdigit() else None
    except ValueError:
        voltage_v = None

    voltage_uv = int(voltage) if voltage.isdigit() else None

    def safe_float(v: str) -> float | None:
        try:
            return float(v)
        except (ValueError, TypeError):
            return None

    e_now = safe_float(energy_now)
    e_full = safe_float(energy_full)
    p_now = safe_float(power_now)
    tte = safe_float(time_to_empty)
    ttf = safe_float(time_to_full)

    c_now = safe_float(charge_now)
    c_full = safe_float(charge_full)
    i_now = safe_float(current_now)

    if not e_now and voltage_uv and c_now:
        e_now = c_now * voltage_uv / 1_000_000
    if not e_full and voltage_uv and c_full:
        e_full = c_full * voltage_uv / 1_000_000
    if not p_now and voltage_uv and i_now:
        p_now = i_now * voltage_uv / 1_000_000

    autonomy = None
    if status == "Discharging":
        if tte and tte > 0:
            autonomy = tte
        elif e_now and e_now > 0 and p_now and p_now > 0:
            autonomy = (e_now / p_now) * 3600
    elif status == "Charging":
        if ttf and ttf > 0:
            autonomy = ttf
        elif e_full and e_now is not None and e_full > 0 and p_now and p_now > 0:
            autonomy = ((e_full - e_now) / p_now) * 3600

    return {
        "available": True,
        "percent": int(capacity) if capacity.isdigit() else None,
        "status": status or "unknown",
        "voltage": voltage_v,
        "energy_now_wh": round(e_now / 1_000_000, 2) if e_now else None,
        "energy_full_wh": round(e_full / 1_000_000, 2) if e_full else None,
        "power_now_w": round(p_now / 1_000_000, 2) if p_now else None,
        "autonomy_seconds": round(autonomy) if autonomy else None,
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


_SIZE_UNITS = {
    "B": 1,
    "kB": 1000,
    "KB": 1024,
    "MB": 1000**2,
    "MiB": 1024**2,
    "GB": 1000**3,
    "GiB": 1024**3,
    "TB": 1000**4,
}


def parse_docker_disk(raw: str) -> dict | None:
    raw = raw.strip()
    if not raw or raw == "__NO_DOCKER__":
        return None
    total_bytes = 0
    for line in raw.splitlines():
        parts = line.split("|")
        if len(parts) != 2:
            continue
        _, size = parts
        m = re.match(r"([\d.]+)\s*([A-Za-z]+)", size.strip())
        if not m:
            continue
        value, unit = m.groups()
        total_bytes += float(value) * _SIZE_UNITS.get(unit, 0)
    return {"total_bytes": round(total_bytes)}


def parse_updates(raw: str) -> int:
    try:
        return int(raw.strip())
    except ValueError:
        return 0


def parse_ping(raw: str) -> float | None:
    m = re.search(r"time=([\d.]+)", raw)
    if m:
        return round(float(m.group(1)), 1)
    return None


def parse_network_status(raw: str) -> dict:
    """Parses the JSON status file written locally by network_guardian_agent.py."""
    raw = raw.strip()
    default = {"available": False}
    if not raw:
        return default
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return default
    if not isinstance(data, dict) or not data:
        return default
    return {"available": True, **data}
