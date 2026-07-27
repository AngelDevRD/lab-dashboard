"""Confidence score por metrica: cuanto se puede confiar en cada dato mostrado.

Basado en la auditoria de precision (battery-audit, angel1/angel2, mas el codigo
fuente del kernel para las causas raiz) — ver AUDITORIA_PRECISION.md.

El score de cada metrica es dinamico, no una tabla fija por tipo de sensor:
parte de un score base segun la fuente, y baja si en el momento actual se
detecta alguna de estas condiciones (todas verificables con datos que el
snapshot ya trae, sin agregar mediciones nuevas salvo el reloj remoto):
  - el sensor no responde (valor ausente)
  - el valor esta fuera de su rango fisico posible
  - la lectura esta congelada (mismo valor exacto en las ultimas muestras)
  - dos fuentes relacionadas discrepan (ej. temp de paquete vs promedio de
    nucleos)
  - hace cuanto se actualizo de verdad (no cuando se pregunto)

Funciones puras, sin I/O — mismo estilo que autonomy.py. No cambia ningun
calculo existente: es metadata que se agrega al lado de los valores que
collector.py/parsers.py ya producen.
"""

STATIC_SCORES = {
    "cpu": {"score": 98, "source": "/proc/stat"},
    "mem": {"score": 97, "source": "/proc/meminfo"},
    "disk": {"score": 95, "source": "df"},
    "load": {"score": 95, "source": "/proc/loadavg"},
    "net": {"score": 90, "source": "/proc/net/dev"},
    "docker": {"score": 90, "source": "docker ps"},
    "cpu_temp": {"score": 88, "source": "lm-sensors (coretemp, Package id)"},
    "latency": {"score": 92, "source": "round-trip SSH real (hostname)"},
    "disk_temp": {
        "score": 20,
        "source": "smartctl",
        "note": "requiere root; con el usuario de servicio actual siempre vuelve vacio",
    },
}

# Umbrales de antiguedad (segundos) para bajar la confianza de power_now.
# La auditoria confirmo que en angel2 el firmware solo actualiza este valor
# cada ~9-15 minutos en promedio (racha congelada mas larga observada: 38.75
# min) — mostrarlo sin avisar de su antiguedad implica una frescura que no
# tiene.
POWER_STALE_WARN_S = 120
POWER_STALE_BAD_S = 600
POWER_MAX_PLAUSIBLE_W = 150  # un mini-PC/laptop no consume esto en DC interno

# Umbral de "lectura congelada": mismo valor exacto en >= N muestras
# consecutivas del historial corto que ya trackea monitor.py.
FROZEN_MIN_SAMPLES = 4

# Umbrales de desfasaje de reloj (segundos) — ver parsers.parse_clock: la
# medicion tiene +-1-2s de margen por si sola, asi que umbrales chicos no
# discriminan nada real.
CLOCK_OK_S = 3
CLOCK_WARN_S = 15


def _is_frozen(history: list | None) -> bool:
    if not history or len(history) < FROZEN_MIN_SAMPLES:
        return False
    return len(set(history)) == 1


def score_bounded_metric(key: str, value: float | None, history: list | None, lo: float, hi: float) -> dict:
    """CPU%/mem%/disk% etc: score base de STATIC_SCORES, rebajado si el
    sensor no responde, el valor es fisicamente imposible, o esta congelado."""
    base = STATIC_SCORES[key]
    if value is None:
        return {"score": 0, "source": base["source"], "note": "sensor no responde"}
    if not (lo <= value <= hi):
        return {"score": 5, "source": base["source"], "note": f"valor fuera de rango fisico ({value})"}
    if _is_frozen(history):
        return {
            "score": min(base["score"], 40),
            "source": base["source"],
            "note": f"sin cambios en las ultimas {len(history)} muestras",
        }
    return {"score": base["score"], "source": base["source"], "note": None}


def score_cpu_temp(cpu: dict | None) -> dict:
    base = STATIC_SCORES["cpu_temp"]
    if not cpu or cpu.get("temp") is None:
        return {"score": 0, "source": base["source"], "note": "sensor no responde"}
    temp = cpu["temp"]
    if not (-10 <= temp <= 105):
        return {"score": 5, "source": base["source"], "note": f"valor fuera de rango fisico ({temp}°C)"}
    per_core = cpu.get("temp_per_core") or []
    if per_core:
        avg_core = sum(per_core) / len(per_core)
        diff = abs(temp - avg_core)
        if diff > 5:
            return {
                "score": 55,
                "source": base["source"],
                "note": f"discrepancia de {diff:.1f}°C entre el valor principal y el promedio de nucleos",
            }
    return {"score": base["score"], "source": base["source"], "note": None}


def score_power(power: dict | None, power_age_s: float | None) -> dict:
    source = "power_now / current_now*voltage_now"
    if not power or not power.get("available"):
        return {"score": 0, "source": source, "note": "sensor de bateria no disponible en este equipo"}
    watts = power.get("power_now_w")
    if watts is not None and (watts < 0 or watts > POWER_MAX_PLAUSIBLE_W):
        return {"score": 5, "source": source, "note": f"valor fisicamente imposible ({watts}W) para este hardware"}
    score = 70  # nativo o derivado, ver auditoria
    note = None
    if power_age_s is not None:
        if power_age_s >= POWER_STALE_BAD_S:
            score = 30
            note = (
                f"sin cambiar hace {round(power_age_s / 60)} min — el firmware "
                "actualiza esto muy lento, no es una lectura instantanea"
            )
        elif power_age_s >= POWER_STALE_WARN_S:
            score = 55
            note = f"sin cambiar hace {round(power_age_s)}s"
    return {"score": score, "source": source, "note": note}


def score_battery_percent(power: dict | None) -> dict:
    source = "capacity (firmware/EC)"
    if not power or not power.get("available"):
        return {"score": 0, "source": source, "note": None}
    pct = power.get("percent")
    if pct is not None and not (0 <= pct <= 100):
        return {"score": 5, "source": source, "note": f"valor fuera de rango fisico ({pct}%)"}
    return {"score": 92, "source": source, "note": None}


def score_autonomy(autonomy_result: dict | None) -> dict:
    source = "modelo hibrido (autonomy.py)"
    if not autonomy_result or not autonomy_result.get("validated"):
        return {
            "score": 15,
            "source": source,
            "note": (
                "sin descargas completas registradas todavia — no hay ground "
                "truth real con el que validar el error del modelo"
            ),
        }
    n = autonomy_result.get("complete_discharges", 0)
    score = 60 if n < 3 else 80
    return {"score": score, "source": source, "note": f"{n} descarga(s) completa(s) registradas"}


def score_clock(offset_s: float | None) -> dict:
    source = "date +%s (remoto vs dashboard)"
    if offset_s is None:
        return {"score": 50, "source": source, "note": "todavia no se pudo medir"}
    abs_off = abs(offset_s)
    if abs_off < CLOCK_OK_S:
        return {"score": 96, "source": source, "note": None}
    if abs_off < CLOCK_WARN_S:
        return {"score": 65, "source": source, "note": f"desfasado {offset_s:+.1f}s"}
    return {
        "score": 15,
        "source": source,
        "note": f"reloj desincronizado ({offset_s:+.1f}s) — revisar NTP/chrony en ese equipo",
    }


def score_connectivity(online_ratio: float | None) -> dict:
    source = "historial de polling SSH (ultimos ciclos)"
    if online_ratio is None:
        return {"score": 50, "source": source, "note": "sin historial todavia"}
    pct = round(online_ratio * 100)
    if online_ratio >= 0.95:
        return {"score": 95, "source": source, "note": None}
    return {
        "score": max(10, pct),
        "source": source,
        "note": f"{100 - pct}% de los ultimos ciclos de poll no respondieron",
    }


def for_server(
    snapshot: dict,
    power_age_s: float | None = None,
    autonomy_result: dict | None = None,
    cpu_history: list | None = None,
    mem_history: list | None = None,
    clock_offset_s: float | None = None,
    online_ratio: float | None = None,
) -> dict:
    """Arma el bloque de confidence scores para un snapshot ya construido por
    collector.py. No muta snapshot — devuelve un dict aparte para que
    monitor.py lo agregue como snapshot["confidence"]."""
    out: dict = {}
    if "cpu" in snapshot:
        out["cpu"] = score_bounded_metric("cpu", snapshot["cpu"].get("percent"), cpu_history, 0, 100)
    if "mem" in snapshot:
        out["mem"] = score_bounded_metric("mem", snapshot["mem"].get("percent"), mem_history, 0, 100)
    for key in ("disk", "load", "net", "docker"):
        if key in snapshot:
            out[key] = {**STATIC_SCORES[key], "note": None}
    if snapshot.get("cpu", {}).get("temp") is not None:
        out["cpu_temp"] = score_cpu_temp(snapshot.get("cpu"))
    if "latency_ms" in snapshot:
        out["latency"] = {**STATIC_SCORES["latency"], "note": None}
    if snapshot.get("disk_temp") is not None:
        out["disk_temp"] = STATIC_SCORES["disk_temp"]
    power = snapshot.get("power")
    if power is not None:
        out["battery_percent"] = score_battery_percent(power)
        out["power"] = score_power(power, power_age_s)
        out["autonomy"] = score_autonomy(autonomy_result)
    out["clock"] = score_clock(clock_offset_s)
    out["connectivity"] = score_connectivity(online_ratio)
    return out


HEALTH_LABELS = {
    "cpu": "CPU", "mem": "Memoria", "disk": "Disco", "load": "Load average",
    "net": "Red", "docker": "Docker", "cpu_temp": "Temperatura CPU",
    "latency": "Latencia SSH", "disk_temp": "Temp. SSD",
    "battery_percent": "Batería %", "power": "Consumo (power_now)",
    "autonomy": "Modelo de autonomía", "clock": "Reloj sincronizado",
    "connectivity": "Conectividad SSH",
}


def telemetry_health(confidence_scores: dict) -> dict:
    """Health Score del servidor: no es el estado del hardware, es cuanto se
    puede confiar en la telemetria que se esta mostrando ahora mismo. Un solo
    numero (promedio de los confidence scores presentes) mas el checklist que
    lo explica — misma fuente de verdad, sin logica duplicada."""
    if not confidence_scores:
        return {"score": 0, "label": "Sin datos", "checks": []}
    scores = [v["score"] for v in confidence_scores.values()]
    overall = round(sum(scores) / len(scores))
    if overall >= 90:
        label = "Excelente"
    elif overall >= 75:
        label = "Buena"
    elif overall >= 50:
        label = "Advertencia"
    else:
        label = "Crítica"

    checks = []
    for key, entry in confidence_scores.items():
        ok = entry["score"] >= 70
        name = HEALTH_LABELS.get(key, key)
        text = f"{name}: {entry['note']}" if entry.get("note") else name
        checks.append({"ok": ok, "text": text})
    checks.sort(key=lambda c: c["ok"])  # problemas primero
    return {"score": overall, "label": label, "checks": checks}
