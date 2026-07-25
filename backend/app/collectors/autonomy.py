"""Hybrid autonomy estimation.

Auditoria de 2026-07-25 (10 min, 200 muestras, angel1 + angel2 — ver
system/battery-audit/) mostro dos cosas: (1) el promedio movil de power_now es
la fuente mas ESTABLE de las evaluadas (menor CV), pero (2) en algunos equipos
(angel2) power_now puede quedarse congelado en un valor durante minutos
mientras energy_now si sigue avanzando — ahi el promedio de power_now, por
estable que sea, esta promediando un numero sesgado.

Este modulo no elige entre power_now o el delta de energia: usa power_now
como base (Paso 1), y solo cuando confirma —durante varios ciclos de 5
minutos, no en el primer indicio— que power_now esta "congelado" mientras
energy_now se mueve, mezcla una fraccion del valor derivado del delta de
energia (Paso 4). En cuanto power_now vuelve a mostrar variacion real, el
modo revierte solo, sin intervencion manual por servidor (Paso 5).

Etapas deliberadamente separadas (no todo en un metodo):
  calculate  -> rolling_power_avg30()      promedio movil de power_now (Paso 1)
  calibrate  -> evaluate()                 compara power_now vs delta-energia
                                            cada CHECK_INTERVAL_S, confirma
                                            firmware inconsistente tras varios
                                            ciclos (Pasos 2-3)
  decide     -> blended_power()            aplica el factor de calibracion
                                            solo si evaluate() lo confirmo
                                            (Pasos 4-5)

"Lectura" y "conversion" (power_now vs charge_now/current_now/voltage_now,
distintos formatos de firmware) ya viven en parsers.parse_battery — este
modulo solo consume su salida (power_now_w, energy_now_wh ya normalizados),
por lo que funciona igual sin importar el formato de origen.
"""

import logging
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger("dashboard")

WINDOW_POWER_S = 30
CHECK_INTERVAL_S = 300  # Paso 2: cada 5 minutos
CONFIRM_CYCLES = 3  # Paso 3: "confirmarlo durante varios ciclos" -> 15 min
STALE_CV_THRESHOLD = 0.02  # power_now varia <2% en la ventana => "congelado"
DIVERGENCE_THRESHOLD = 0.15  # >15% de diferencia vs la derivada, algo anda mal
CALIBRATION_WEIGHT = 0.2  # Paso 4: 80% power_now_avg30 + 20% derivada


class Mode(str, Enum):
    NORMAL = "normal"
    INCONSISTENT = "firmware_inconsistente"
    CALIBRATED = "calibrado"


@dataclass
class HostAutonomyState:
    # (timestamp, power_w) y (timestamp, energy_now_wh), recortados a
    # CHECK_INTERVAL_S — cubre tanto la ventana de 30s (Paso 1) como la de
    # 5 min (Paso 2) sin mantener dos buffers separados.
    power_samples: deque = field(default_factory=deque)
    energy_samples: deque = field(default_factory=deque)
    last_check_ts: float | None = None
    stale_streak: int = 0
    mode: Mode = Mode.NORMAL


def _trim(buf: deque, now: float, window_s: float) -> None:
    while buf and now - buf[0][0] > window_s:
        buf.popleft()


def record_sample(state: HostAutonomyState, now: float, power_w: float | None, energy_wh: float | None) -> None:
    """Lectura ya convertida (viene de parsers.parse_battery) -> guardar para las etapas siguientes."""
    if power_w is not None:
        state.power_samples.append((now, power_w))
        _trim(state.power_samples, now, CHECK_INTERVAL_S)
    if energy_wh is not None:
        state.energy_samples.append((now, energy_wh))
        _trim(state.energy_samples, now, CHECK_INTERVAL_S)


def rolling_power_avg30(state: HostAutonomyState, now: float) -> float | None:
    """Paso 1: promedio movil de power_now en los ultimos 30s."""
    vals = [p for ts, p in state.power_samples if now - ts <= WINDOW_POWER_S]
    return sum(vals) / len(vals) if vals else None


def _cv(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    mean = sum(values) / len(values)
    if mean == 0:
        return None
    var = sum((v - mean) ** 2 for v in values) / len(values)
    return (var**0.5) / abs(mean)


def _derived_power(state: HostAutonomyState) -> float | None:
    """Potencia real = delta(energy_now) / delta(tiempo) sobre toda la
    ventana disponible (no un promedio de derivadas puntuales — eso amplifica
    el ruido de conteos discretos en vez de suavizarlo, confirmado en la
    auditoria)."""
    if len(state.energy_samples) < 2:
        return None
    t0, e0 = state.energy_samples[0]
    t1, e1 = state.energy_samples[-1]
    dt_h = (t1 - t0) / 3600
    if dt_h <= 0:
        return None
    return abs(e1 - e0) / dt_h


def evaluate(state: HostAutonomyState, now: float) -> dict | None:
    """Paso 2/3: corre como maximo una vez cada CHECK_INTERVAL_S. Compara el
    promedio de power_now contra la potencia derivada del delta de energia;
    si power_now esta practicamente plano (CV bajo) mientras la derivada
    diverge, cuenta un ciclo de sospecha. Solo tras CONFIRM_CYCLES ciclos
    consecutivos se confirma firmware inconsistente — un unico ciclo raro no
    basta (Paso 3: "no asumir inmediatamente que esta roto").
    Devuelve None si aun no toca evaluar (nada que loguear)."""
    if state.last_check_ts is not None and now - state.last_check_ts < CHECK_INTERVAL_S:
        return None
    span = state.energy_samples[-1][0] - state.energy_samples[0][0] if len(state.energy_samples) >= 2 else 0
    if span < CHECK_INTERVAL_S * 0.8:
        return None  # todavia no hay suficiente historia para un ciclo real

    state.last_check_ts = now
    power_values = [p for _, p in state.power_samples]
    power_avg = sum(power_values) / len(power_values) if power_values else None
    power_cv = _cv(power_values)
    derived = _derived_power(state)

    frozen = power_cv is not None and power_cv < STALE_CV_THRESHOLD
    diverges = bool(
        power_avg and derived and power_avg > 0
        and abs(power_avg - derived) / power_avg > DIVERGENCE_THRESHOLD
    )

    if frozen and diverges:
        state.stale_streak += 1
    else:
        state.stale_streak = 0

    # Paso 5: en cuanto un ciclo deja de mostrar el patron, se sale de
    # CALIBRATED/INCONSISTENT solo — no hay bandera "una vez roto, siempre
    # calibrado". Ningun ajuste manual por servidor.
    if state.stale_streak >= CONFIRM_CYCLES:
        state.mode = Mode.CALIBRATED
    elif state.stale_streak > 0:
        state.mode = Mode.INCONSISTENT
    else:
        state.mode = Mode.NORMAL

    result = {
        "mode": state.mode, "power_avg": power_avg, "power_cv": power_cv,
        "derived_power": derived, "frozen": frozen, "diverges": diverges,
        "stale_streak": state.stale_streak,
    }
    logger.info(
        "autonomy calibration check: mode=%s power_now_avg=%s derived=%s "
        "cv=%s stale_streak=%d",
        state.mode.value,
        f"{power_avg:.2f}W" if power_avg else "NA",
        f"{derived:.2f}W" if derived else "NA",
        f"{power_cv:.3f}" if power_cv is not None else "NA",
        state.stale_streak,
    )
    return result


def blended_power(state: HostAutonomyState, now: float) -> tuple[float | None, float]:
    """Paso 4/5: valor de potencia realmente usado para la autonomia, y el
    factor de calibracion aplicado (0 = 100% power_now, tal como Paso 1)."""
    avg30 = rolling_power_avg30(state, now)
    if state.mode != Mode.CALIBRATED:
        return avg30, 0.0
    derived = _derived_power(state)
    if avg30 is None:
        return derived, 1.0
    if derived is None:
        return avg30, 0.0
    blended = (1 - CALIBRATION_WEIGHT) * avg30 + CALIBRATION_WEIGHT * derived
    return blended, CALIBRATION_WEIGHT


def autonomy_seconds(status: str | None, energy_now_wh: float | None, energy_full_wh: float | None, power_w: float | None) -> float | None:
    if not power_w or power_w <= 0:
        return None
    if status == "Discharging" and energy_now_wh is not None and energy_now_wh > 0:
        return (energy_now_wh / power_w) * 3600
    if (
        status == "Charging"
        and energy_now_wh is not None
        and energy_full_wh is not None
        and energy_full_wh > energy_now_wh
    ):
        return ((energy_full_wh - energy_now_wh) / power_w) * 3600
    return None


def estimate(state: HostAutonomyState, status: str | None, energy_now_wh: float | None, energy_full_wh: float | None, now: float | None = None) -> dict:
    """Punto de entrada unico para el monitor: registra la muestra, corre el
    ciclo de calibracion si toca, y devuelve la autonomia final mas todo lo
    necesario para el log."""
    now = now if now is not None else time.time()
    check = evaluate(state, now)
    power_used, factor = blended_power(state, now)
    seconds = autonomy_seconds(status, energy_now_wh, energy_full_wh, power_used)
    return {
        "autonomy_seconds": round(seconds) if seconds is not None else None,
        "power_used_w": power_used,
        "calibration_factor": factor,
        "mode": state.mode.value,
        "check": check,
    }
