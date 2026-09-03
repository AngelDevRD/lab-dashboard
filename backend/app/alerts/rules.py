from collections.abc import Generator

from .models import Severity
from .thresholds import ThresholdSettings


def cpu_rule(server: dict, settings: ThresholdSettings) -> Generator[dict, None, None]:
    if not settings.enable_cpu_alerts:
        return
    cpu = server.get("cpu", {})
    pct = cpu.get("percent")
    if pct is None:
        return
    if pct >= 100:
        yield {
            "severity": Severity.CRITICAL,
            "category": "cpu",
            "title": "CPU al 100%",
            "description": f"CPU alcanzó 100% en {server.get('name')}.",
            "current_value": pct,
            "threshold_value": 100,
        }
    elif pct >= settings.cpu_critical:
        yield {
            "severity": Severity.CRITICAL,
            "category": "cpu",
            "title": "CPU crítica",
            "description": f"CPU al {pct}%. Límite: {settings.cpu_critical}%.",
            "current_value": pct,
            "threshold_value": settings.cpu_critical,
        }
    elif pct >= settings.cpu_warning:
        yield {
            "severity": Severity.WARNING,
            "category": "cpu",
            "title": "CPU elevada",
            "description": f"CPU al {pct}%. Límite: {settings.cpu_warning}%.",
            "current_value": pct,
            "threshold_value": settings.cpu_warning,
        }
    load = server.get("load", {})
    cores = cpu.get("cores", 1)
    l1 = load.get("load1", 0)
    if l1 > cores * settings.load_warning_multiplier:
        yield {
            "severity": Severity.WARNING,
            "category": "cpu",
            "title": "Carga promedio excesiva",
            "description": f"Load average ({l1:.1f}) supera {settings.load_warning_multiplier}x núcleos ({cores}).",
            "current_value": round(l1, 1),
            "threshold_value": round(cores * settings.load_warning_multiplier, 1),
        }


def ram_rule(server: dict, settings: ThresholdSettings) -> Generator[dict, None, None]:
    if not settings.enable_ram_alerts:
        return
    mem = server.get("mem", {})
    pct = mem.get("percent")
    if pct is None:
        return
    if pct >= settings.ram_critical:
        yield {
            "severity": Severity.CRITICAL,
            "category": "ram",
            "title": "RAM crítica",
            "description": f"RAM al {pct}%. Límite: {settings.ram_critical}%.",
            "current_value": pct,
            "threshold_value": settings.ram_critical,
        }
    elif pct >= settings.ram_warning:
        yield {
            "severity": Severity.WARNING,
            "category": "ram",
            "title": "RAM alta",
            "description": f"RAM al {pct}%. Límite: {settings.ram_warning}%.",
            "current_value": pct,
            "threshold_value": settings.ram_warning,
        }
    swap = mem.get("swap", {})
    sp = swap.get("percent", 0)
    st = swap.get("total", 0)
    if st > 0 and sp >= settings.swap_critical:
        yield {
            "severity": Severity.CRITICAL,
            "category": "ram",
            "title": "Swap crítica",
            "description": f"Swap al {sp}%. Límite: {settings.swap_critical}%.",
            "current_value": sp,
            "threshold_value": settings.swap_critical,
        }
    elif st > 0 and sp >= settings.swap_warning:
        yield {
            "severity": Severity.WARNING,
            "category": "ram",
            "title": "Swap elevada",
            "description": f"Swap al {sp}%. Límite: {settings.swap_warning}%.",
            "current_value": sp,
            "threshold_value": settings.swap_warning,
        }


def disk_rule(server: dict, settings: ThresholdSettings) -> Generator[dict, None, None]:
    if not settings.enable_disk_alerts:
        return
    disk = server.get("disk", {})
    pct = disk.get("percent")
    free = disk.get("free", 0)
    if pct is None:
        return
    if pct >= settings.disk_critical:
        yield {
            "severity": Severity.CRITICAL,
            "category": "disk",
            "title": "Disco casi lleno",
            "description": f"Disco al {pct}%. Límite: {settings.disk_critical}%.",
            "current_value": pct,
            "threshold_value": settings.disk_critical,
        }
    elif pct >= settings.disk_warning:
        yield {
            "severity": Severity.WARNING,
            "category": "disk",
            "title": "Disco elevado",
            "description": f"Disco al {pct}%. Límite: {settings.disk_warning}%.",
            "current_value": pct,
            "threshold_value": settings.disk_warning,
        }
    if 0 < free < settings.disk_min_free_bytes:
        yield {
            "severity": Severity.WARNING,
            "category": "disk",
            "title": "Poco espacio libre",
            "description": f"Quedan {free / 1e9:.1f} GB libres en {server.get('name')}. Mínimo: {settings.disk_min_free_bytes / 1e9:.0f} GB.",
            "current_value": round(free / 1e9, 1),
            "threshold_value": settings.disk_min_free_bytes / 1e9,
        }


def docker_rule(server: dict, settings: ThresholdSettings) -> Generator[dict, None, None]:
    if not settings.enable_docker_alerts:
        return
    if not server.get("monitor_docker", True):
        return
    docker = server.get("docker", {})
    if not docker.get("available"):
        yield {
            "severity": Severity.WARNING,
            "category": "docker",
            "title": "Docker no disponible",
            "description": f"Docker daemon no responde en {server.get('name')}.",
        }
        return
    containers = docker.get("containers", [])
    if len(containers) == 0:
        yield {
            "severity": Severity.WARNING,
            "category": "docker",
            "title": "Sin contenedores activos",
            "description": f"No existen contenedores Docker en {server.get('name')} cuando deberían existir.",
        }
        return
    for c in containers:
        if c["state"] != "running":
            yield {
                "severity": Severity.CRITICAL,
                "category": "docker",
                "title": "Contenedor detenido",
                "description": f"Contenedor '{c['name']}' está {c['state']} en {server.get('name')}: {c['status']}.",
                "current_value": c["state"],
                "threshold_value": "running",
            }


def temp_rule(server: dict, settings: ThresholdSettings) -> Generator[dict, None, None]:
    if not settings.enable_temp_alerts:
        return
    cpu = server.get("cpu", {})
    temp = cpu.get("temp")
    if temp is not None and temp >= settings.cpu_max_temp:
        yield {
            "severity": Severity.CRITICAL,
            "category": "temp",
            "title": "Temperatura crítica CPU",
            "description": f"CPU a {temp}°C en {server.get('name')}. Límite: {settings.cpu_max_temp}°C.",
            "current_value": temp,
            "threshold_value": settings.cpu_max_temp,
        }
    elif temp is not None and temp >= 70:
        yield {
            "severity": Severity.WARNING,
            "category": "temp",
            "title": "Temperatura CPU elevada",
            "description": f"CPU a {temp}°C en {server.get('name')}.",
            "current_value": temp,
            "threshold_value": 70,
        }
    disk_temp = server.get("disk_temp")
    if disk_temp is not None and disk_temp >= settings.disk_max_temp:
        yield {
            "severity": Severity.WARNING,
            "category": "temp",
            "title": "Disco caliente",
            "description": f"Disco a {disk_temp}°C en {server.get('name')}. Límite: {settings.disk_max_temp}°C.",
            "current_value": disk_temp,
            "threshold_value": settings.disk_max_temp,
        }


def services_rule(server: dict, settings: ThresholdSettings) -> Generator[dict, None, None]:
    if not settings.enable_service_alerts:
        return
    services = server.get("services", {})
    for name, state in services.items():
        if state != "active":
            yield {
                "severity": Severity.CRITICAL,
                "category": "services",
                "title": f"Servicio caído: {name}",
                "description": f"El servicio {name} no responde en {server.get('name')}.",
                "current_value": "inactive",
                "threshold_value": "active",
            }


def power_rule(server: dict, settings: ThresholdSettings) -> Generator[dict, None, None]:
    if not settings.enable_battery_alerts:
        return
    if not server.get("monitor_battery", True):
        return
    power = server.get("power", {})
    if not power.get("available"):
        return
    pct = power.get("percent")
    if pct is None:
        return
    # Bateria fisicamente presente pero danada/desconectada: el firmware
    # reporta 0% fijo para siempre (nunca sube, nunca cambia de estado) sin
    # ningun sensor de potencia detras -- eso no es una emergencia real, es
    # hardware que no funciona. Sin este chequeo, un equipo asi dispara la
    # alerta de "bateria en emergencia" en cada ciclo, para siempre.
    # No se filtra por texto de "status": el firmware roto puede reportar
    # "Discharging", "Not charging" o "Unknown" indistintamente -- la senal
    # real de bateria danada es que no hay ninguna lectura de potencia.
    battery_damaged = pct == 0 and power.get("power_now_w") is None
    if battery_damaged:
        return
    if pct <= settings.battery_emergency:
        yield {
            "severity": Severity.CRITICAL,
            "category": "power",
            "title": "Batería en emergencia",
            "description": f"Batería al {pct}% en {server.get('name')}. Umbral: {settings.battery_emergency}%.",
            "current_value": pct,
            "threshold_value": settings.battery_emergency,
        }
    elif pct <= settings.battery_critical:
        yield {
            "severity": Severity.CRITICAL,
            "category": "power",
            "title": "Batería crítica",
            "description": f"Batería al {pct}% en {server.get('name')}. Umbral: {settings.battery_critical}%.",
            "current_value": pct,
            "threshold_value": settings.battery_critical,
        }
    elif pct <= settings.battery_warning:
        yield {
            "severity": Severity.WARNING,
            "category": "power",
            "title": "Batería baja",
            "description": f"Batería al {pct}% en {server.get('name')}. Umbral: {settings.battery_warning}%.",
            "current_value": pct,
            "threshold_value": settings.battery_warning,
        }


def network_rule(server: dict, settings: ThresholdSettings) -> Generator[dict, None, None]:
    if not settings.enable_network_alerts:
        return
    if not server.get("online"):
        # Title stays fixed ("Servidor desconectado") regardless of reason so the
        # alert keeps the same dedup identity (see service._hash_id) while the
        # underlying cause fluctuates between polls (e.g. timeout -> unreachable).
        # Otherwise every reason change would look like a new alert and re-fire
        # a fresh notification for what is really the same ongoing outage.
        reason = server.get("offline_reason", "network_error")
        reason_text = {
            "timeout": "no respondió dentro del tiempo límite (timeout)",
            "unreachable": "no es alcanzable en la red",
            "auth_error": "rechazó la autenticación SSH",
            "backoff": "está en backoff tras fallos previos",
            "network_error": "sufrió un error de red",
        }
        yield {
            "severity": Severity.CRITICAL,
            "category": "network",
            "title": "Servidor desconectado",
            "description": f"{server.get('name')} {reason_text.get(reason, 'perdió conexión')}.",
            "current_value": reason,
            "threshold_value": "online",
        }
        return
    latency = server.get("latency_ms")
    if latency is not None:
        if latency >= settings.latency_critical_ms:
            yield {
                "severity": Severity.CRITICAL,
                "category": "network",
                "title": "Latencia crítica",
                "description": f"Latencia de {latency}ms en {server.get('name')}. Límite: {settings.latency_critical_ms}ms.",
                "current_value": latency,
                "threshold_value": settings.latency_critical_ms,
            }
        elif latency >= settings.latency_warning_ms:
            yield {
                "severity": Severity.WARNING,
                "category": "network",
                "title": "Latencia alta",
                "description": f"Latencia de {latency}ms en {server.get('name')}. Límite: {settings.latency_warning_ms}ms.",
                "current_value": latency,
                "threshold_value": settings.latency_warning_ms,
            }


# network_rule is the only rule that must still run when the server is
# offline (it's what yields the "Servidor desconectado" alert). The rest
# depend on metrics that simply don't exist in an offline snapshot and must
# be skipped rather than misread missing data as "unavailable" (see
# engine.evaluate_server).
RESOURCE_RULES: list = [
    cpu_rule,
    ram_rule,
    disk_rule,
    docker_rule,
    temp_rule,
    services_rule,
    power_rule,
]

RULES: list = RESOURCE_RULES + [network_rule]
