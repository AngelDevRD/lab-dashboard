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
BROADCAST_INTERVAL = float(os.getenv("BROADCAST_INTERVAL", "2"))
HISTORY_LEN = int(os.getenv("HISTORY_LEN", "40"))
WS_SEND_TIMEOUT = float(os.getenv("WS_SEND_TIMEOUT", "5"))
SSH_TIMEOUT = float(os.getenv("SSH_TIMEOUT", "5"))
SSH_COMMAND_TIMEOUT = float(os.getenv("SSH_COMMAND_TIMEOUT", "8"))
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


def load_servers() -> list[dict]:
    if not SERVERS_FILE.exists():
        return []
    try:
        with open(SERVERS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        raw_list = data.get("servers", [])
        validated = [ServerConfig(**s) for s in raw_list]
        return [s.model_dump() for s in validated]
    except Exception as exc:
        logger.error("Error loading %s: %s", SERVERS_FILE, exc)
        return []
