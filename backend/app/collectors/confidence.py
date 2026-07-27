"""Confidence score por metrica: cuanto se puede confiar en cada dato mostrado.

Basado en la auditoria de precision (battery-audit, angel1/angel2, mas el codigo
fuente del kernel para las causas raiz) — ver AUDITORIA_PRECISION.md. Scores fijos
para metricas cuya confiabilidad no depende del estado en runtime (CPU, memoria,
etc.); las que si dependen del momento actual (potencia, segun hace cuanto
cambio de verdad; autonomia, segun si ya hubo una descarga completa que la
valide) se calculan en runtime a partir del snapshot.

Funciones puras, sin I/O — mismo estilo que autonomy.py. No cambia ningun
calculo existente: es metadata que se agrega al lado de los valores que
collector.py/parsers.py ya producen.
"""

STATIC_SCORES = {
    "cpu": {"score": 98, "source": "/proc/stat", "note": None},
    "mem": {"score": 97, "source": "/proc/meminfo", "note": None},
    "disk": {"score": 95, "source": "df", "note": None},
    "load": {"score": 95, "source": "/proc/loadavg", "note": None},
    "net": {"score": 90, "source": "/proc/net/dev", "note": None},
    "docker": {"score": 90, "source": "docker ps", "note": None},
    "cpu_temp": {
        "score": 88,
        "source": "lm-sensors (coretemp, Package id)",
        "note": None,
    },
    "latency": {
        "score": 92,
        "source": "round-trip SSH real (hostname)",
        "note": None,
    },
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


def score_power(power: dict | None, power_age_s: float | None) -> dict:
    if not power or not power.get("available"):
        return {
            "score": 0,
            "source": "power_supply (ausente)",
            "note": "sensor de bateria no disponible en este equipo",
        }
    score = 70  # nativo o derivado de current_now*voltage_now, ver auditoria
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
    return {"score": score, "source": "power_now / current_now×voltage_now", "note": note}


def score_battery_percent(power: dict | None) -> dict:
    if not power or not power.get("available"):
        return {"score": 0, "source": "power_supply (ausente)", "note": None}
    return {"score": 92, "source": "capacity (firmware/EC)", "note": None}


def score_autonomy(autonomy_result: dict | None) -> dict:
    if not autonomy_result or not autonomy_result.get("validated"):
        return {
            "score": 15,
            "source": "modelo hibrido (autonomy.py)",
            "note": (
                "sin descargas completas registradas todavia — no hay ground "
                "truth real con el que validar el error del modelo"
            ),
        }
    n = autonomy_result.get("complete_discharges", 0)
    score = 60 if n < 3 else 80
    return {
        "score": score,
        "source": "modelo hibrido (autonomy.py)",
        "note": f"{n} descarga(s) completa(s) registradas",
    }


def for_server(
    snapshot: dict,
    power_age_s: float | None = None,
    autonomy_result: dict | None = None,
) -> dict:
    """Arma el bloque de confidence scores para un snapshot ya construido por
    collector.py. No muta snapshot — devuelve un dict aparte para que
    monitor.py lo agregue como snapshot["confidence"]."""
    out: dict = {}
    for key in ("cpu", "mem", "disk", "load", "net", "docker"):
        if key in snapshot:
            out[key] = STATIC_SCORES[key]
    if snapshot.get("cpu", {}).get("temp") is not None:
        out["cpu_temp"] = STATIC_SCORES["cpu_temp"]
    if "latency_ms" in snapshot:
        out["latency"] = STATIC_SCORES["latency"]
    if snapshot.get("disk_temp") is not None:
        out["disk_temp"] = STATIC_SCORES["disk_temp"]
    power = snapshot.get("power")
    if power is not None:
        out["battery_percent"] = score_battery_percent(power)
        out["power"] = score_power(power, power_age_s)
        out["autonomy"] = score_autonomy(autonomy_result)
    return out
