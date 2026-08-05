"""Pure parsing functions: raw command output -> plain dicts. No I/O here."""

import json
import re
from datetime import datetime


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
    """Returns {"value": <primary temp>, "per_core": [temps...]}.

    "value" prioriza la temperatura de paquete completo (label "Package id"),
    que es la metrica estandar de coretemp; si no esta, promedia los nucleos
    reales (label "Core N"). Nunca cae a "el primer *_input que aparezca" —
    eso mezclaba lecturas de voltaje de bateria o del chip WiFi (que en
    `sensors -j` suelen listarse antes que coretemp en el JSON) con
    temperaturas reales, sesgando el promedio varios grados sin ningun aviso
    (confirmado en angel1/angel2: `in0_input` = voltios de BAT1, y
    `temp1_input` del chip iwlwifi, se colaban en el promedio de "CPU temp").
    """
    raw = raw.strip()
    if not raw:
        return {"value": None, "per_core": [], "amd_package_power_w": None}
    try:
        data = json.loads(raw)
        per_core: list[float] = []
        package_temp: float | None = None
        k10temp_value: float | None = None
        # fam15h_power/power1_average: consumo real del paquete AMD (W), no
        # temperatura -- viaja en el mismo "sensors -j" que ya se pide para
        # la temperatura, asi que se aprovecha sin otro round-trip SSH. Sirve
        # como fallback de vatios en equipos AMD sin bateria funcional.
        amd_package_power_w: float | None = None
        for chip_name, chip in data.items():
            if re.match(r"fam15h_power", chip_name, re.IGNORECASE):
                for sensor in chip.values():
                    if isinstance(sensor, dict) and "power1_average" in sensor:
                        try:
                            watts = round(float(sensor["power1_average"]), 2)
                            # El sensor a veces reporta un valor negativo
                            # espurio en una lectura suelta (ruido del chip
                            # en reposo) -- fisicamente no existe consumo
                            # negativo, tratarlo como lectura invalida.
                            amd_package_power_w = watts if watts >= 0 else None
                        except (TypeError, ValueError):
                            pass
            for label, sensor in chip.items():
                if not isinstance(sensor, dict):
                    continue
                for k, v in sensor.items():
                    if "input" not in k or not isinstance(v, (int, float)):
                        continue
                    if re.search(r"package", label, re.IGNORECASE):
                        package_temp = round(float(v), 1)
                    elif re.search(r"core\s*\d+", label, re.IGNORECASE):
                        per_core.append(round(float(v), 1))
                    # AMD (driver k10temp): sin etiquetas "Package"/"Core N"
                    # como Intel -- el chip reporta un solo "temp1" generico.
                    # Se identifica por el nombre del chip, no del sensor, y
                    # solo se usa como ultimo fallback para no pisar coretemp.
                    elif k10temp_value is None and re.match(r"k10temp", chip_name, re.IGNORECASE):
                        k10temp_value = round(float(v), 1)
        if package_temp is not None or per_core:
            value = (
                package_temp if package_temp is not None
                else round(sum(per_core) / len(per_core), 1)
            )
            return {"value": value, "per_core": per_core, "amd_package_power_w": amd_package_power_w}
        if k10temp_value is not None:
            return {"value": k10temp_value, "per_core": [], "amd_package_power_w": amd_package_power_w}
    except (json.JSONDecodeError, AttributeError):
        pass
    nums = [int(x) for x in raw.splitlines() if x.strip().isdigit()]
    if nums:
        temps = [round(n / 1000, 1) for n in nums]
        return {"value": temps[0], "per_core": temps, "amd_package_power_w": None}
    return {"value": None, "per_core": [], "amd_package_power_w": None}


def parse_cpu_power_rapl(raw: str) -> float | None:
    """Dos lecturas de energy_uj (RAPL, dominio package-0) separadas ~1s ->
    watts promedio de esa ventana. Ver comando "cpu_power_rapl" en
    commands.py -- vacio si el host no es Intel o RAPL no esta accesible."""
    parts = raw.strip().split()
    if len(parts) != 2:
        return None
    try:
        a, b = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    delta_uj = b - a
    if delta_uj < 0:  # el contador de RAPL da la vuelta (wraparound)
        return None
    return round(delta_uj / 1_000_000, 2)


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
        "cache": info.get("Cached", 0),
        "buffers": info.get("Buffers", 0),
        "swap": {"total": swap_total, "used": swap_used, "free": swap_free, "percent": swap_percent},
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
    lines = raw.splitlines()
    if lines and lines[-1] == "__DOCKER_OK__":
        lines = lines[:-1]
    containers = []
    running = stopped = 0
    for line in lines:
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
    """Returns {name: raw systemctl state} (active/activating/failed/inactive/unknown)
    instead of a bool, so the UI can tell "restarting" apart from "down"."""
    lines = raw.strip().splitlines()
    result = {}
    for i, name in enumerate(service_names):
        result[name] = lines[i].strip() if i < len(lines) else "unknown"
    return result


# host -> voltaje filtrado (EMA), en microvoltios. Ver comentario en
# parse_battery: solo se usa para el fallback charge_now*V -> energy_now,
# nunca para power_now (que debe seguir reaccionando al instante).
_voltage_ema_uv: dict[str, float] = {}
VOLTAGE_EMA_ALPHA = 0.1


def parse_battery(raw: str, host: str = "") -> dict:
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

    # Voltaje EMA por host, solo para el fallback charge_now*V -> energy_now
    # (ver docstring de mas abajo). power_now sigue usando voltage_uv crudo
    # sin filtrar: ahi la reaccion instantanea es lo que se quiere.
    voltage_uv_filtered = voltage_uv
    if voltage_uv is not None:
        prev = _voltage_ema_uv.get(host)
        voltage_uv_filtered = (
            voltage_uv if prev is None
            else prev + VOLTAGE_EMA_ALPHA * (voltage_uv - prev)
        )
        _voltage_ema_uv[host] = voltage_uv_filtered

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

    # Procedencia de cada valor (measured/derived/unavailable), registrada en
    # el mismo lugar donde ya se decide si hace falta derivar o no -- consumida
    # por monitor.py para armar power.meta. No cambia ningun calculo existente.
    energy_now_origin = "measured" if e_now else None
    energy_full_origin = "measured" if e_full else None
    power_now_origin = "measured" if p_now else None

    # energy_now/energy_full: usan el voltaje FILTRADO. Cuando el firmware no
    # expone energy_now directamente (caso angel1), la unica forma de
    # derivarlo es charge_now * voltaje. charge_now ya es un contador de
    # carga integrado por el propio chip (confiable); pero si se multiplica
    # por el voltaje instantaneo, una caida transitoria de tension por IR
    # (picos de CPU, hasta ~40mV medidos en la auditoria) se cuela en
    # energy_now -- justo la señal que autonomy.py usa como "verdad" para
    # decidir si power_now es confiable. Filtrar el voltaje con una EMA
    # rompe ese acoplamiento sin perder la integracion real de charge_now.
    if not e_now and voltage_uv_filtered and c_now:
        e_now = c_now * voltage_uv_filtered / 1_000_000
        energy_now_origin = "derived"
    if not e_full and voltage_uv_filtered and c_full:
        e_full = c_full * voltage_uv_filtered / 1_000_000
        energy_full_origin = "derived"
    # power_now: voltaje SIN filtrar a proposito -- es la lectura "ahora
    # mismo", el suavizado de esta va aparte (ver power_display en
    # monitor.py), no debe mezclarse con el filtro de energy.
    if not p_now and voltage_uv and i_now:
        p_now = i_now * voltage_uv / 1_000_000
        power_now_origin = "derived"

    energy_now_origin = energy_now_origin or ("unavailable" if not e_now else "measured")
    energy_full_origin = energy_full_origin or ("unavailable" if not e_full else "measured")
    power_now_origin = power_now_origin or ("unavailable" if not p_now else "measured")

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
        "energy_now_wh_origin": energy_now_origin,
        "energy_full_wh_origin": energy_full_origin,
        "power_now_w_origin": power_now_origin,
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


def parse_clock(raw: str, reference_now: float) -> float | None:
    """Offset (segundos) entre el reloj del servidor remoto y el del
    dashboard: remote - reference_now. `reference_now` debe ser el punto
    medio entre el envio y la respuesta del comando (ver
    collector.py: _measure_clock_offset), estilo NTP -- eso compensa
    aproximadamente la mitad del RTT en vez de mezclarlo con el offset real.
    Sigue siendo una estimacion (asume ida y vuelta simetricos), suficiente
    para detectar un reloj realmente desincronizado (drift de minutos/horas),
    no para medir jitter fino."""
    raw = raw.strip()
    try:
        remote_ts = float(raw)
    except ValueError:
        return None
    return round(remote_ts - reference_now, 1)


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


# ---------------------------------------------------------------------------
# "Información avanzada" (collectors/advanced.py) — fetched on demand only
# when a server's detail panel is open, never part of the continuous poll.
# ---------------------------------------------------------------------------

def parse_cpu_freq(raw: str) -> dict:
    lines = raw.strip().splitlines()
    current = None
    maximum = None
    if lines and lines[0].strip():
        try:
            current = round(float(lines[0].strip()))
        except ValueError:
            current = None
    if len(lines) > 1 and lines[1].strip().isdigit():
        maximum = round(int(lines[1].strip()) / 1000)  # kHz -> MHz
    return {"current_mhz": current, "max_mhz": maximum}


_prev_disk_io_samples: dict[str, tuple[float, int, int]] = {}


def _is_whole_disk(name: str) -> bool:
    if re.match(r"^(sd|vd|hd)[a-z]+$", name):
        return True
    if re.match(r"^(nvme\d+n\d+|mmcblk\d+)$", name):
        return True
    return False


def parse_disk_io(raw: str, host: str, now: float) -> dict | None:
    """Delta-based, like parse_net_io — first sample has no rate yet (None)."""
    read_sectors = write_sectors = 0
    for line in raw.strip().splitlines():
        parts = line.split()
        if len(parts) < 10 or not _is_whole_disk(parts[2]):
            continue
        read_sectors += int(parts[5])
        write_sectors += int(parts[9])
    read_bytes = read_sectors * 512
    write_bytes = write_sectors * 512

    prev = _prev_disk_io_samples.get(host)
    _prev_disk_io_samples[host] = (now, read_bytes, write_bytes)
    if not prev:
        return None
    prev_time, prev_read, prev_write = prev
    dt = max(now - prev_time, 0.001)
    return {
        "read_bps": round(max(0, (read_bytes - prev_read) / dt)),
        "write_bps": round(max(0, (write_bytes - prev_write) / dt)),
    }


def parse_smart_health(raw: str) -> str | None:
    """Reads only the actual health-assessment line. smartctl often needs root
    and prints "Permission denied" / "open device ... failed" otherwise — that
    text contains the word "failed" too, so matching it loosely would report a
    disk failure that isn't real. None means "couldn't determine", not "bad"."""
    m = re.search(r"overall-health self-assessment test result:\s*(\S+)", raw, re.IGNORECASE)
    if not m:
        return None
    result = m.group(1).upper()
    if result == "PASSED":
        return "ok"
    if result == "FAILED":
        return "error"
    return "warning"


def parse_docker_stats(raw: str) -> dict | None:
    raw = raw.strip()
    if not raw or raw == "__NO_DOCKER__":
        return None
    cpu_total = 0.0
    mem_total = 0.0
    count = 0
    for line in raw.splitlines():
        parts = line.split("|")
        if len(parts) != 2:
            continue
        cpu_str, mem_str = parts
        try:
            cpu_total += float(cpu_str.strip().rstrip("%"))
        except ValueError:
            pass
        mem_used = mem_str.split("/")[0].strip() if "/" in mem_str else ""
        m = re.match(r"([\d.]+)\s*([A-Za-z]+)", mem_used)
        if m:
            value, unit = m.groups()
            mem_total += float(value) * _SIZE_UNITS.get(unit, 0)
        count += 1
    if count == 0:
        return None
    return {"cpu_percent": round(cpu_total, 1), "mem_used_bytes": round(mem_total)}


def parse_ping_extended(raw: str) -> dict:
    loss = None
    latency = None
    m = re.search(r"([\d.]+)%\s*packet loss", raw)
    if m:
        loss = float(m.group(1))
    m = re.search(r"= [\d.]+/([\d.]+)/", raw)  # rtt min/avg/max/mdev
    if m:
        latency = round(float(m.group(1)), 1)
    return {"latency_ms": latency, "loss_pct": loss}


def parse_tailscale_ip(raw: str) -> str | None:
    for line in raw.strip().splitlines():
        line = line.strip()
        if re.match(r"^\d+\.\d+\.\d+\.\d+$", line):
            return line
    return None


def parse_system_info(raw: str) -> dict:
    lines = raw.strip("\n").splitlines()
    os_pretty = lines[0].strip().strip('"') if len(lines) > 0 and lines[0].strip() else None
    kernel = lines[1].strip() if len(lines) > 1 and lines[1].strip() else None
    arch = lines[2].strip() if len(lines) > 2 and lines[2].strip() else None
    boot_at = None
    if len(lines) > 3 and lines[3].strip():
        try:
            boot_at = datetime.strptime(lines[3].strip(), "%Y-%m-%d %H:%M:%S").timestamp()
        except ValueError:
            boot_at = None
    return {"os_pretty": os_pretty, "kernel": kernel, "arch": arch, "boot_at": boot_at}
