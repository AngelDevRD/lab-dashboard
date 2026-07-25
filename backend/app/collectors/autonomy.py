"""Hybrid autonomy estimation.

Auditoria de 10 min (2026-07-25, angel1 + angel2 — ver system/battery-audit/)
mostro que el promedio movil de power_now es la fuente mas ESTABLE de las
evaluadas, y que en angel2 power_now se veia "congelado" (CV~0) durante toda
esa ventana mientras energy_now si avanzaba.

v1 de este modulo disparaba la calibracion solo si power_now estaba
practicamente plano (CV<2%) Y divergia de la derivada. Una auditoria de
seguimiento EN VIVO de 40 min (800 muestras) invalido esa condicion: en esa
sesion mas larga, power_now en angel2 SI variaba (CV=3.3%, nunca "congelado")
pero seguia sesgado 2.7x sobre la potencia real (delta de energia real:
0.57W vs power_now reportando ~1.56W, sostenido durante los 40 minutos
completos) — el chequeo de "congelado" nunca se disparo pese al sesgo real y
persistente. Confundir "sin movimiento" con "impreciso" produce un falso
negativo: un firmware puede ser estable pero incorrecto (angel2) o inestable
pero correcto, y son cosas distintas que hay que medir por separado.

v2 (esta version) dispara la calibracion por **divergencia sostenida**,
sin importar si power_now tiene jitter o no: cada 5 minutos se calcula el
error relativo entre power_now (promedio 30s) y la potencia real derivada
del delta de energia; si ese error supera el 15% durante 3 ventanas
consecutivas, se confirma. La varianza de power_now (CV) se sigue calculando
y logueando como metrica de "estabilidad", pero es puramente informativa —
ya no forma parte de la condicion de disparo.

Este modulo no elige entre power_now o el delta de energia: usa power_now
como base (Paso 1), y solo cuando confirma —durante varios ciclos de 5
minutos, no en el primer indicio— que power_now diverge de forma sostenida
de la energia real, mezcla una fraccion del valor derivado (Paso 4). En
cuanto la divergencia deja de sostenerse, el modo revierte solo, sin
intervencion manual por servidor (Paso 5).

Etapas deliberadamente separadas (no todo en un metodo):
  calculate  -> rolling_power_avg30()      promedio movil de power_now (Paso 1)
  calibrate  -> evaluate()                 compara power_now vs delta-energia
                                            cada CHECK_INTERVAL_S, confirma
                                            divergencia sostenida tras varios
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
DIVERGENCE_THRESHOLD = 0.15  # error > 15% vs la potencia real derivada, sostenido -> calibrar
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
    host: str = ""
    power_samples: deque = field(default_factory=deque)
    energy_samples: deque = field(default_factory=deque)
    last_check_ts: float | None = None
    stale_streak: int = 0
    mode: Mode = Mode.NORMAL
    last_calibration_ts: float | None = None


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
    """Paso 2/3 (v2 — disparador por divergencia sostenida, no por "congelado").

    Corre como maximo una vez cada CHECK_INTERVAL_S. Calcula la confiabilidad
    (error relativo entre el promedio de power_now y la potencia real
    derivada del delta de energia) y, por separado, la estabilidad (CV de
    power_now) — son dos cosas distintas: un firmware puede ser estable pero
    incorrecto (caso real observado en angel2: CV=3.3%, jitea, pero sesgado
    2.7x de forma sostenida) o inestable pero correcto. Solo la confiabilidad
    decide la calibracion; la estabilidad es informativa (logs).

    Si el error supera DIVERGENCE_THRESHOLD, cuenta un ciclo de sospecha.
    Solo tras CONFIRM_CYCLES ciclos consecutivos se confirma divergencia
    sostenida — un unico ciclo raro no basta (Paso 3: "no asumir
    inmediatamente que esta roto"). Devuelve None si aun no toca evaluar."""
    if state.last_check_ts is not None and now - state.last_check_ts < CHECK_INTERVAL_S:
        return None
    span = state.energy_samples[-1][0] - state.energy_samples[0][0] if len(state.energy_samples) >= 2 else 0
    if span < CHECK_INTERVAL_S * 0.8:
        return None  # todavia no hay suficiente historia para un ciclo real

    state.last_check_ts = now
    power_values = [p for _, p in state.power_samples]
    power_avg = sum(power_values) / len(power_values) if power_values else None
    power_cv = _cv(power_values)  # estabilidad, informativo — no gatilla nada
    derived = _derived_power(state)

    error = None
    if power_avg is not None and derived and derived > 0:
        error = abs(power_avg - derived) / derived
    diverges = error is not None and error > DIVERGENCE_THRESHOLD

    if diverges:
        state.stale_streak += 1
    else:
        state.stale_streak = 0

    prev_mode = state.mode
    # Paso 5: en cuanto un ciclo deja de mostrar divergencia sostenida, se
    # sale de CALIBRATED/INCONSISTENT solo — no hay bandera "una vez roto,
    # siempre calibrado". Ningun ajuste manual por servidor.
    if state.stale_streak >= CONFIRM_CYCLES:
        state.mode = Mode.CALIBRATED
    elif state.stale_streak > 0:
        state.mode = Mode.INCONSISTENT
    else:
        state.mode = Mode.NORMAL
    if state.mode == Mode.CALIBRATED and prev_mode != Mode.CALIBRATED:
        state.last_calibration_ts = now

    error_pct = error * 100 if error is not None else None
    log_factor = CALIBRATION_WEIGHT if state.mode == Mode.CALIBRATED else 0.0
    decision = {
        Mode.NORMAL: "power_now confiable, sin cambios",
        Mode.INCONSISTENT: f"ciclo {state.stale_streak}/{CONFIRM_CYCLES} con error>{DIVERGENCE_THRESHOLD*100:.0f}%, aun sin confirmar",
        Mode.CALIBRATED: f"divergencia confirmada tras {CONFIRM_CYCLES} ciclos, aplicando {CALIBRATION_WEIGHT*100:.0f}% de peso derivado",
    }[state.mode]

    # Observabilidad — punto 1: log estructurado en cada evaluacion (una vez
    # cada CHECK_INTERVAL_S, no modifica la logica de decision de arriba).
    logger.info(
        "[AUTONOMY] host=%s mode=%s error=%s cv=%s power_now_avg30=%s "
        "power_delta_energy=%s calibration_factor=%.2f decision=%s",
        state.host,
        state.mode.value,
        f"{error_pct:.1f}%" if error_pct is not None else "NA",
        f"{power_cv*100:.1f}%" if power_cv is not None else "NA",
        f"{power_avg:.2f}W" if power_avg is not None else "NA",
        f"{derived:.2f}W" if derived is not None else "NA",
        log_factor,
        decision,
    )
    # Observabilidad — punto 2: log separado SOLO en transiciones, con motivo.
    if state.mode != prev_mode:
        logger.info(
            "[AUTONOMY][TRANSITION] host=%s %s -> %s motivo=\"%s\"",
            state.host, prev_mode.value, state.mode.value, decision,
        )

    return {
        "mode": state.mode, "power_avg": power_avg, "power_cv": power_cv,
        "derived_power": derived, "error_pct": error_pct,
        "diverges": diverges, "stale_streak": state.stale_streak,
        "prev_mode": prev_mode, "decision": decision,
    }


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


def metrics(state: HostAutonomyState) -> dict:
    """Observabilidad — punto 3: snapshot de las metricas internas para el
    endpoint de diagnostico. No calcula nada nuevo, solo expone el estado
    actual del algoritmo (mismas funciones que ya usa estimate())."""
    now = time.time()
    avg30 = rolling_power_avg30(state, now)
    derived = _derived_power(state)
    cv = _cv([p for _, p in state.power_samples])
    error = None
    if avg30 is not None and derived and derived > 0:
        error = abs(avg30 - derived) / derived
    factor = CALIBRATION_WEIGHT if state.mode == Mode.CALIBRATED else 0.0
    return {
        "host": state.host,
        "autonomy_mode": state.mode.value,
        "firmware_reliable": state.mode == Mode.NORMAL,
        "power_now_avg30": round(avg30, 3) if avg30 is not None else None,
        "power_delta_energy": round(derived, 3) if derived is not None else None,
        "error_pct": round(error * 100, 1) if error is not None else None,
        "cv_pct": round(cv * 100, 1) if cv is not None else None,
        "calibration_factor": factor,
        "samples": len(state.power_samples),
        "last_calibration": state.last_calibration_ts,
    }


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
