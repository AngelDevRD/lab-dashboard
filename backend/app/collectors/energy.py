"""Cumulative energy consumption (kWh) per server host.

Integrates power_now_w (the same "consumo actual" watts already shown per
server card) over time: kwh_total += power_w * dt_hours / 1000. Persisted to
disk (config.ENERGY_KWH_FILE) so the counter survives backend restarts --
otherwise every redeploy would reset it to 0.

Only integrates while power_now_w is actually available; a gap (server
offline, no battery sensor, power temporarily unavailable) is never treated
as "power stayed constant during the gap" -- dt is capped so a long outage
doesn't silently inflate the total with a guessed value.
"""

import json
import logging
import time

from .. import config

logger = logging.getLogger("dashboard")

# Un hueco mas largo que esto (servidor offline, sensor caido un rato) no se
# integra: mejor perder esa ventana que asumir consumo constante durante un
# corte largo.
MAX_GAP_S = 120

_state: dict[str, dict] = {}
_loaded = False
_last_saved = 0.0


def _load() -> None:
    global _state, _loaded
    _loaded = True
    path = config.ENERGY_KWH_FILE
    if not path.exists():
        return
    try:
        with open(path, "r", encoding="utf-8") as f:
            _state = json.load(f)
    except Exception:
        logger.exception("Failed to read energy kWh state from %s", path)
        _state = {}


def _save(force: bool = False) -> None:
    global _last_saved
    now = time.time()
    if not force and now - _last_saved < config.ENERGY_KWH_SAVE_INTERVAL_S:
        return
    path = config.ENERGY_KWH_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(_state, f)
    tmp.replace(path)
    _last_saved = now


def record(host: str, power_w: float | None, now: float | None = None) -> None:
    if not _loaded:
        _load()
    now = now if now is not None else time.time()
    entry = _state.setdefault(host, {"kwh_total": 0.0, "last_sample_ts": None})
    if power_w is not None and power_w > 0:
        prev_ts = entry["last_sample_ts"]
        if prev_ts is not None:
            dt_h = (now - prev_ts) / 3600
            if 0 < dt_h <= MAX_GAP_S / 3600:
                entry["kwh_total"] += power_w * dt_h / 1000
        entry["last_sample_ts"] = now
    _save()


def get_kwh(host: str) -> float | None:
    if not _loaded:
        _load()
    entry = _state.get(host)
    # last_sample_ts solo queda en None si nunca hubo una lectura de
    # power_now_w > 0 para este host (ver record()) -- sin eso, "0.0" seria
    # indistinguible de "nunca se pudo medir nada" (caso .6/.7: bateria
    # danada o inexistente, jamas va a haber una lectura real).
    if not entry or entry.get("last_sample_ts") is None:
        return None
    return round(entry["kwh_total"], 4)
