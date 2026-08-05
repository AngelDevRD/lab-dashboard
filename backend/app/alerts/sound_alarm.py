"""Alarma sonora en el propio servidor cuando su bateria (de verdad
funcional) cae por debajo de un umbral -- rules.power_rule ya descarta
equipos sin bateria o con bateria fisicamente danada antes de generar la
alerta, asi que cualquier alerta de categoria "power" que llega hasta aca
es de un host con datos reales. Hoy eso son .8 y .9; .6 y .7 tienen la
bateria fisicamente danada, asi que power_rule nunca genera alerta para
ellos por ahora -- pero el audio ya esta instalado en los 4, asi que el
dia que se les cambie la bateria esto empieza a sonar ahi tambien sin
tocar nada mas.

Reproduce un audio grabado por el dueño ("servidor N está descargado",
distinto por host, subido a /opt/lab-dashboard-alarm/battery-alarm.mp4 en
cada equipo) en vez de un tono generico -- mas dificil de ignorar/confundir
con otra cosa. Se repite mas veces cuanto mas baja este la bateria (ver
_repeat_count): 1 vez a 20%, hasta 4 veces a 5% o menos.

Se dispara desde los mismos puntos que la notificacion ntfy en
NotificationService: al activarse la alerta, y en cada reintento
(RETRY_CONFIG["power"], cada 10 min mientras siga activa) -- asi el
audio se repite mientras el problema persista, no es un aviso unico que
se puede pasar por alto.

Los nombres de control de volumen (Master/Speaker/DAC/PCM) varian segun
la tarjeta de sonido de cada equipo (confirmado: .8 usa "Master", .9 no
lo tiene y usa "Speaker"/"DAC") -- se prueban todos, ignorando los que no
existen en ese host, en vez de mantener una lista por servidor.
"""

import logging

from .. import config
from ..ssh_client import pool

logger = logging.getLogger("dashboard")

_VOLUME_CONTROLS = ("Master", "Speaker", "DAC", "PCM")
_UNMUTE = "; ".join(f'amixer sset "{c}" 90% unmute >/dev/null 2>&1' for c in _VOLUME_CONTROLS)
AUDIO_PATH = "/opt/lab-dashboard-alarm/battery-alarm.mp4"
PLAY_CMD = f"timeout 15 ffplay -nodisp -autoexit -loglevel quiet {AUDIO_PATH} >/dev/null 2>&1"

_MIN_REPEATS = 1
_MAX_REPEATS = 4
_REPEAT_START_PCT = 20  # a esta carga o mas alta: 1 sola vez
_REPEAT_STEP_PCT = 5    # cada 5% menos de bateria, una repeticion mas


def _repeat_count(percent: int | float | None) -> int:
    """20% -> 1 vez, 15% -> 2, 10% -> 3, 5% o menos -> 4 (tope)."""
    if percent is None:
        return _MIN_REPEATS
    steps_below = max(0, (_REPEAT_START_PCT - percent) / _REPEAT_STEP_PCT)
    return min(_MAX_REPEATS, _MIN_REPEATS + int(steps_below))


async def play_battery_alarm(host: str, percent: int | float | None = None) -> None:
    server = next((s for s in config.load_servers() if s["host"] == host), None)
    if not server:
        return
    conn = pool.get(server)
    repeats = _repeat_count(percent)
    cmd = f"{_UNMUTE}; for i in $(seq 1 {repeats}); do {PLAY_CMD}; done"
    try:
        await conn.run(cmd)
    except Exception:
        logger.exception("No se pudo sonar la alarma de bateria en %s", host)
