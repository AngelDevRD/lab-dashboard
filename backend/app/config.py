import json
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, Field

logger = logging.getLogger("dashboard")

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")
SERVERS_FILE = Path(os.getenv("SERVERS_FILE", BASE_DIR / "servers.json"))
SSH_KEY_PATH = os.getenv("SSH_KEY_PATH", str(Path.home() / ".ssh" / "id_ed25519"))
POLL_INTERVAL = float(os.getenv("POLL_INTERVAL", "3"))
HISTORY_LEN = int(os.getenv("HISTORY_LEN", "40"))
WS_SEND_TIMEOUT = float(os.getenv("WS_SEND_TIMEOUT", "5"))
# Techo entre mensajes por WebSocket. El broadcast se dispara con cada ciclo de
# poll, pero un ciclo lento (un host que acepta TCP y después se cuelga hasta
# SSH_BATCH_TIMEOUT) dejaría al cliente sin mensajes: su watchdog lo lee como
# conexión muerta y fuerza una reconexión. Este keep-alive reenvía el último
# snapshot para evitarlo. Debe quedar por debajo de WS_STALE_MS del frontend.
WS_HEARTBEAT_INTERVAL = float(os.getenv("WS_HEARTBEAT_INTERVAL", "5"))
SSH_TIMEOUT = float(os.getenv("SSH_TIMEOUT", "5"))
SSH_COMMAND_TIMEOUT = float(os.getenv("SSH_COMMAND_TIMEOUT", "8"))
# Techo para una tanda completa de comandos (ver ssh_client._build_batch), que
# viaja en un solo exec_command. Acota el peor caso de un ciclo: antes cada
# comando tenía su propio SSH_COMMAND_TIMEOUT y la tanda podía tardar
# SSH_COMMAND_TIMEOUT × n.
SSH_BATCH_TIMEOUT = float(os.getenv("SSH_BATCH_TIMEOUT", "25"))
SSH_BACKOFF_BASE = float(os.getenv("SSH_BACKOFF_BASE", "2"))
SSH_BACKOFF_MAX = float(os.getenv("SSH_BACKOFF_MAX", "60"))
INTERNET_CHECK_TARGETS = ["8.8.8.8", "1.1.1.1"]
LOG_FILE = Path(os.getenv("LOG_FILE", BASE_DIR.parent / "logs" / "events.log"))
LOG_FILE_MAX_BYTES = int(os.getenv("LOG_FILE_MAX_BYTES", str(5 * 1024 * 1024)))
LOG_FILE_BACKUP_COUNT = int(os.getenv("LOG_FILE_BACKUP_COUNT", "5"))

NETWORK_REPORT_TOKEN = os.getenv("NETWORK_REPORT_TOKEN", "")
NETWORK_DEVICE_STALE_SEC = float(os.getenv("NETWORK_DEVICE_STALE_SEC", "60"))

FRAMEWORK_TELEMETRY_TOKEN = os.getenv("FRAMEWORK_TELEMETRY_TOKEN", "")
FRAMEWORK_TELEMETRY_FILE = Path(
    os.getenv(
        "FRAMEWORK_TELEMETRY_FILE", str(BASE_DIR / "data" / "framework_telemetry.jsonl")
    )
)

# ccusage rescans Claude Code's local session logs on every invocation, so results
# are cached instead of fetched on every request. Pinned to a specific version
# (instead of @latest) so npx doesn't hit the npm registry to resolve "latest"
# on every cold call — on a slower remote host that resolution is a big chunk
# of the delay. Bump this deliberately when upgrading, via env var if needed.
CLAUDE_USAGE_PACKAGE = os.getenv("CLAUDE_USAGE_PACKAGE", "ccusage@20.0.18")
CLAUDE_USAGE_CACHE_TTL = float(os.getenv("CLAUDE_USAGE_CACHE_TTL", "300"))
CLAUDE_USAGE_TIMEOUT = float(os.getenv("CLAUDE_USAGE_TIMEOUT", "30"))

# Pushed by a remote machine that actually runs Claude Code (ccusage reads local
# session logs, so it's useless on a host where Claude Code never ran). The
# backend serves this instead of shelling out to ccusage whenever a push has
# been received; falls back to running ccusage itself only if none ever arrived
# (keeps local/dev usage working unchanged).
CLAUDE_USAGE_REPORT_TOKEN = os.getenv("CLAUDE_USAGE_REPORT_TOKEN", "")
CLAUDE_USAGE_REPORT_FILE = Path(
    os.getenv("CLAUDE_USAGE_REPORT_FILE", str(BASE_DIR / "data" / "claude_usage_push.json"))
)
CLAUDE_USAGE_STALE_SEC = float(os.getenv("CLAUDE_USAGE_STALE_SEC", str(24 * 3600)))

# Consumo acumulado (kWh) por servidor: integra power_now_w en el tiempo.
# Persistido para sobrevivir reinicios del backend (si no, el contador
# volveria a 0 cada vez que se reinicia el contenedor).
ENERGY_KWH_FILE = Path(
    os.getenv("ENERGY_KWH_FILE", str(BASE_DIR / "data" / "energy_kwh.json"))
)
ENERGY_KWH_SAVE_INTERVAL_S = float(os.getenv("ENERGY_KWH_SAVE_INTERVAL_S", "60"))

CORS_ORIGINS = [
    o.strip()
    for o in os.getenv(
        "CORS_ORIGINS", "http://localhost:8600,http://127.0.0.1:8600"
    ).split(",")
    if o.strip()
]

KNOWN_SERVICES = [
    s.strip()
    for s in os.getenv(
        "KNOWN_SERVICES",
        "ssh,docker,tailscaled,nginx,fastapi,postgresql,redis-server,portainer,uptime-kuma",
    ).split(",")
    if s.strip()
]


class ServerConfig(BaseModel):
    name: str = Field(..., min_length=1)
    host: str = Field(..., min_length=1)
    ssh_port: int = Field(default=22, ge=1, le=65535)
    ssh_user: str = Field(default="ubuntu", min_length=1)


# (mtime_ns, size) del archivo ya parseado -> lista validada. El loop de
# monitoreo llama load_servers() en cada ciclo (y el endpoint /advanced en cada
# request): releer + validar con pydantic un archivo que casi nunca cambia es
# trabajo puro de descarte. Se revalida solo si el archivo cambió en disco, así
# que editar servers.json sigue tomando efecto sin reiniciar.
_servers_cache: tuple[tuple, list[dict]] | None = None


def _read_servers() -> list[dict]:
    with open(SERVERS_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    validated = [ServerConfig(**s) for s in data.get("servers", [])]
    return [s.model_dump() for s in validated]


def load_servers() -> list[dict]:
    global _servers_cache
    try:
        stat = SERVERS_FILE.stat()
    except OSError:
        _servers_cache = None
        return []
    stamp = (str(SERVERS_FILE), stat.st_mtime_ns, stat.st_size)
    if _servers_cache is not None and _servers_cache[0] == stamp:
        return _servers_cache[1]
    try:
        servers = _read_servers()
    except Exception as exc:
        logger.error("Error loading %s: %s", SERVERS_FILE, exc)
        _servers_cache = None
        return []
    _servers_cache = (stamp, servers)
    return servers
