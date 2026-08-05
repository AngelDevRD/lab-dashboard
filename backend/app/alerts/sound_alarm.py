"""Alarma sonora en el propio servidor cuando su bateria (de verdad
funcional) cae por debajo de un umbral -- rules.power_rule ya descarta
equipos sin bateria o con bateria fisicamente danada antes de generar la
alerta, asi que cualquier alerta de categoria "power" que llega hasta aca
es de un host con datos reales (hoy: .8 y .9).

Se dispara desde los mismos puntos que la notificacion ntfy en
NotificationService: al activarse la alerta, y en cada reintento
(RETRY_CONFIG["power"], cada 10 min mientras siga activa) -- asi el
sonido se repite mientras el problema persista, no es un beep unico que
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
BEEP_CMD = f"{_UNMUTE}; timeout 4 speaker-test -t sine -f 1000 -l 1 >/dev/null 2>&1"


async def play_battery_alarm(host: str) -> None:
    server = next((s for s in config.load_servers() if s["host"] == host), None)
    if not server:
        return
    conn = pool.get(server)
    try:
        await conn.run(BEEP_CMD)
    except Exception:
        logger.exception("No se pudo sonar la alarma de bateria en %s", host)
