"""Presencia de dispositivos Android en la LAN, resuelta 100% localmente:
tabla ARP del sistema operativo (MAC -> IP actual, porque estos dispositivos
reciben IP por DHCP y puede cambiar en cualquier momento) + un ping ICMP a
esa IP (una entrada ARP puede seguir cacheada un rato aunque el dispositivo
ya se haya apagado). Sin dependencia de ningun servicio externo -- ni
Companion ni nada mas: si el SO ve al dispositivo en su red, esto lo detecta.

    MAC conocida -> tabla ARP del SO -> IP actual -> ping esa IP -> online/offline

CAVEAT DE DESPLIEGUE (importante, no resuelto aca): en producción el backend
corre dentro de un contenedor Docker (ver Dockerfile/docker-compose.yml) en
la red bridge por defecto. La tabla ARP visible ahí adentro es la del bridge
virtual del contenedor (172.17.0.0/16), NO la de la LAN real
(192.168.100.0/24) donde están estos dispositivos -- el host sí la ve, el
contenedor no, salvo que el servicio corra con `network_mode: host`. Sin ese
cambio en docker-compose.yml (que implica exponer el contenedor entero en la
red del host, más superficie de ataque sobre el hardening de UFW/nginx ya
documentado ahí), esto funciona corriendo directo en el host -- como en este
entorno de desarrollo en Windows -- pero devolverá todo "offline" dentro del
contenedor de producción tal como está hoy. Queda para que el usuario decida
si vale la pena ese trade-off.
"""

import asyncio
import json
import logging
import platform
import re
import subprocess

from . import config

logger = logging.getLogger("dashboard")

# host_id -> ultima IP confirmada. Persistido en disco (ver config.py) para
# sobrevivir reinicios/redeploys -- sin esto, cada redeploy del timer de
# auto-actualizacion (corre cada 2 min) volveria a perder el fallback y
# dejaria "offline" a cualquier dispositivo sin trafico ARP reciente hasta
# que, por azar, generara trafico hacia este host de nuevo.
_last_ip_state: dict[str, str] = {}
_last_ip_loaded = False


def _load_last_ips() -> None:
    global _last_ip_state, _last_ip_loaded
    _last_ip_loaded = True
    path = config.DEVICE_LAST_IP_FILE
    if not path.exists():
        return
    try:
        with open(path, "r", encoding="utf-8") as f:
            _last_ip_state = json.load(f)
    except Exception:
        logger.exception("Failed to read device last-IP state from %s", path)
        _last_ip_state = {}


def _save_last_ips() -> None:
    path = config.DEVICE_LAST_IP_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(_last_ip_state, f)
    tmp.replace(path)

_MAC = r"([0-9a-fA-F]{2}(?:[:-][0-9a-fA-F]{2}){5})"
_IP = r"(\d{1,3}(?:\.\d{1,3}){3})"

# Windows `arp -a`, formato tipico (el separador de miles/decimales y el
# idioma de la columna de estado varian por configuracion regional, pero eso
# no afecta esta regex porque no la usamos):
#   Interfaz: 192.168.100.5 --- 0xb
#     Dirección de Internet   Dirección física      Tipo
#     192.168.100.104      24-da-9b-14-a4-ca     dinámico
_WINDOWS_ARP_LINE = re.compile(rf"^\s*{_IP}\s+{_MAC}\s+\S+", re.MULTILINE)

# Linux `ip neigh` (preferido: distingue REACHABLE/STALE/FAILED):
#   192.168.100.104 dev wlan0 lladdr 24:da:9b:14:a4:ca REACHABLE
_LINUX_NEIGH_LINE = re.compile(rf"^{_IP}\s+dev\s+\S+\s+lladdr\s+{_MAC}", re.MULTILINE)

# Linux `arp -n` (fallback si `ip` no esta disponible):
#   Address           HWtype  HWaddress           Flags Mask  Iface
#   192.168.100.104   ether   24:da:9b:14:a4:ca   C           wlan0
_LINUX_ARP_LINE = re.compile(rf"^{_IP}\s+\S+\s+{_MAC}", re.MULTILINE)


def _normalize_mac(mac: str) -> str:
    return mac.strip().lower().replace("-", ":")


def _run(cmd: list[str], timeout: float = 5) -> str:
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, errors="replace"
        )
        return result.stdout or ""
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("device_presence: fallo ejecutando %s: %s", cmd, exc)
        return ""


def parse_arp_table(output: str, patterns: list[re.Pattern]) -> dict[str, str]:
    """MAC normalizada (minuscula, ':') -> IP. Probar varios patrones deja
    esto testeable sin invocar subprocess (ver tests/test_device_presence.py)."""
    table: dict[str, str] = {}
    for pattern in patterns:
        for match in pattern.finditer(output):
            ip, mac = match.group(1), match.group(2)
            table[_normalize_mac(mac)] = ip
    return table


def get_arp_table() -> dict[str, str]:
    """Una sola lectura de la tabla ARP/vecinos del SO para los 4 dispositivos
    -- no una consulta por dispositivo."""
    if platform.system() == "Windows":
        return parse_arp_table(_run(["arp", "-a"]), [_WINDOWS_ARP_LINE])
    table = parse_arp_table(_run(["ip", "neigh"]), [_LINUX_NEIGH_LINE])
    if table:
        return table
    return parse_arp_table(_run(["arp", "-n"]), [_LINUX_ARP_LINE])


def resolve_ip_by_mac(mac: str, arp_table: dict[str, str] | None = None) -> str | None:
    table = arp_table if arp_table is not None else get_arp_table()
    return table.get(_normalize_mac(mac))


def _ping(ip: str, timeout_seconds: float = 1.0) -> bool:
    if platform.system() == "Windows":
        cmd = ["ping", "-n", "1", "-w", str(max(int(timeout_seconds * 1000), 100)), ip]
    else:
        cmd = ["ping", "-c", "1", "-W", str(max(int(round(timeout_seconds)), 1)), ip]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout_seconds + 2
        )
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("device_presence: ping a %s falló: %s", ip, exc)
        return False


def is_device_online(
    mac: str,
    timeout_seconds: float = 1.0,
    arp_table: dict[str, str] | None = None,
    fallback_ip: str | None = None,
) -> tuple[bool, str | None]:
    """(online, ip_resuelta).

    Una entrada ARP solo existe si el propio host tuvo trafico IP reciente
    con ese vecino -- un dispositivo puede estar perfectamente prendido y
    conectado a la LAN sin que ESTE host en particular le haya hablado nunca
    (confirmado en angel1: MACs completamente ausentes de `ip neigh show`,
    ni siquiera como entrada vieja/FAILED, para dispositivos que si estaban
    encendidos). Un ping broadcast a la subred no sirve de forzador: Android
    moderno no responde ICMP a broadcast por bateria/seguridad (probado).

    Por eso, sin entrada ARP, se intenta un ping directo a la ultima IP
    conocida de ese dispositivo (persistida entre ciclos por el caller): el
    ping mismo dispara la resolucion ARP como efecto de lado del kernel al
    enviar el paquete, y si el dispositivo sigue en esa IP (lo mas probable,
    los leases DHCP no cambian tan seguido) queda confirmado. Sin entrada ARP
    y sin IP previa conocida -> offline (nunca se vio a este dispositivo
    desde este host, no hay a que pingear)."""
    ip = resolve_ip_by_mac(mac, arp_table)
    if ip is not None:
        return _ping(ip, timeout_seconds), ip
    if fallback_ip is not None:
        return _ping(fallback_ip, timeout_seconds), fallback_ip
    return False, None


async def check_all(devices: list[dict], timeout_seconds: float = 1.0) -> list[dict]:
    """Chequea los dispositivos configurados. Un solo arp/ip-neigh compartido
    entre todos, y los pings (subprocess bloqueante) corren en threads del
    executor por defecto para no trabar el loop de asyncio del monitor."""
    if not _last_ip_loaded:
        _load_last_ips()
    loop = asyncio.get_running_loop()
    arp_table = await loop.run_in_executor(None, get_arp_table)
    changed = False

    async def _check(d: dict) -> dict:
        nonlocal changed
        mac = d.get("mac")
        if not mac:
            return {"id": d["id"], "name": d["name"], "status": "unknown", "ip": None, "mac": None}
        fallback_ip = _last_ip_state.get(d["id"])
        online, ip = await loop.run_in_executor(
            None, is_device_online, mac, timeout_seconds, arp_table, fallback_ip
        )
        if online and ip and _last_ip_state.get(d["id"]) != ip:
            _last_ip_state[d["id"]] = ip
            changed = True
        return {
            "id": d["id"],
            "name": d["name"],
            "status": "online" if online else "offline",
            "ip": ip,
            "mac": mac,
        }

    results = list(await asyncio.gather(*(_check(d) for d in devices)))
    if changed:
        await loop.run_in_executor(None, _save_last_ips)
    return results
