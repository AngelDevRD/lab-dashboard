import json
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")
SERVERS_FILE = Path(os.getenv("SERVERS_FILE", BASE_DIR / "servers.json"))
SSH_KEY_PATH = os.getenv("SSH_KEY_PATH", str(Path.home() / ".ssh" / "id_ed25519"))
POLL_INTERVAL = float(os.getenv("POLL_INTERVAL", "3"))
BROADCAST_INTERVAL = float(os.getenv("BROADCAST_INTERVAL", "2"))
SSH_TIMEOUT = float(os.getenv("SSH_TIMEOUT", "5"))
SSH_COMMAND_TIMEOUT = float(os.getenv("SSH_COMMAND_TIMEOUT", "8"))
INTERNET_CHECK_TARGETS = ["8.8.8.8", "1.1.1.1"]
LOG_FILE = Path(os.getenv("LOG_FILE", BASE_DIR.parent / "logs" / "events.log"))
KNOWN_SERVICES = [
    "ssh",
    "docker",
    "tailscaled",
    "nginx",
    "fastapi",
    "postgresql",
    "redis-server",
    "portainer",
    "uptime-kuma",
]


def load_servers() -> list[dict]:
    if not SERVERS_FILE.exists():
        return []
    with open(SERVERS_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("servers", [])
