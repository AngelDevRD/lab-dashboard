#!/usr/bin/env python3
"""Network Guardian — agente de solo lectura para Ubuntu.

No cambia de red: el failover real lo hace wpa_supplicant/systemd-networkd solo,
usando las access-points ya configuradas en netplan (ver system/network-guardian/README.md).
Este agente únicamente observa el estado del WiFi e internet y lo expone en dos lugares:

  - /etc/network-guardian/status.json   (leído por el dashboard vía SSH)
  - un log rotado con los eventos (cambios de red, caídas de internet)

Config: /etc/network-guardian/config.yaml (ver config.example.yaml).
"""

import json
import logging
import os
import re
import subprocess
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

import yaml

CONFIG_PATH = Path("/etc/network-guardian/config.yaml")
DEFAULT_STATUS_FILE = "/etc/network-guardian/status.json"
DEFAULT_LOG_FILE = "/var/log/network-guardian/events.log"

logger = logging.getLogger("network-guardian")


def load_config() -> dict:
    defaults = {
        "interface": "wlp1s0",
        "preferred_ssid": "",
        "backup_ssid": "",
        "poll_interval_sec": 10,
        "extra_ping_targets": ["1.1.1.1", "8.8.8.8"],
        "ping_timeout_sec": 2,
        "status_file": DEFAULT_STATUS_FILE,
        "log_file": DEFAULT_LOG_FILE,
        "log_level": "INFO",
        "log_max_bytes": 5 * 1024 * 1024,
        "log_backup_count": 5,
        # Watchdog de recuperación: systemd-networkd (quien realmente asocia el WiFi
        # en este stack) no siempre re-intenta solo tras una desconexión — visto en el
        # incidente del 2026-07-20 (18 min sin ningún intento de reconexión). Si no hay
        # conectividad por más de recovery_after_sec, el agente pide a networkd que
        # reconfigure la interfaz (no reinicia el servicio completo, no toca otras
        # interfaces). Cooldown evita reintentos en loop si de verdad no hay ninguna
        # red disponible.
        "recovery_enabled": True,
        "recovery_after_sec": 90,
        "recovery_cooldown_sec": 120,
        # "auto" detecta si NetworkManager administra la interfaz (nmcli) o si es
        # systemd-networkd+wpa_supplicant (caso Ángel 1). Cada uno necesita comandos de
        # recuperación distintos — ver attempt_recovery().
        "network_manager": "auto",
    }
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        defaults.update(data)
    return defaults


def setup_logging(cfg: dict) -> None:
    logger.setLevel(getattr(logging, str(cfg["log_level"]).upper(), logging.INFO))
    log_path = Path(cfg["log_file"])
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        log_path,
        maxBytes=int(cfg["log_max_bytes"]),
        backupCount=int(cfg["log_backup_count"]),
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(asctime)s\t%(levelname)s\t%(message)s"))
    logger.addHandler(handler)


def run(cmd: list[str], timeout: float = 5) -> str:
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False
        )
        return result.stdout
    except (subprocess.TimeoutExpired, OSError):
        return ""


def get_link_info(interface: str) -> dict:
    """Parses `iw dev <iface> link` for SSID, RSSI and TX bitrate."""
    raw = run(["iw", "dev", interface, "link"])
    if "Not connected" in raw or not raw.strip():
        return {"ssid": None, "rssi": None, "link_speed_mbps": None}
    ssid_m = re.search(r"^\s*SSID:\s*(.+)$", raw, re.MULTILINE)
    signal_m = re.search(r"signal:\s*(-?\d+)\s*dBm", raw)
    rate_m = re.search(r"tx bitrate:\s*([\d.]+)\s*MBit/s", raw)
    return {
        "ssid": ssid_m.group(1).strip() if ssid_m else None,
        "rssi": int(signal_m.group(1)) if signal_m else None,
        "link_speed_mbps": round(float(rate_m.group(1))) if rate_m else None,
    }


def get_ip(interface: str) -> str | None:
    raw = run(["ip", "-4", "-br", "addr", "show", interface])
    m = re.search(r"(\d+\.\d+\.\d+\.\d+)/\d+", raw)
    return m.group(1) if m else None


def get_gateway() -> str | None:
    raw = run(["ip", "route", "show", "default"])
    m = re.search(r"default via (\d+\.\d+\.\d+\.\d+)", raw)
    return m.group(1) if m else None


def ping_targets(targets: list[str], timeout_sec: float) -> tuple[float | None, float]:
    """Returns (avg_ms of successful pings, packet_loss_pct across all targets)."""
    latencies = []
    for target in targets:
        raw = run(
            ["ping", "-c", "1", "-W", str(int(timeout_sec)), target],
            timeout=timeout_sec + 2,
        )
        m = re.search(r"time=([\d.]+)", raw)
        if m:
            latencies.append(float(m.group(1)))
    loss_pct = round((1 - len(latencies) / len(targets)) * 100, 1) if targets else 100.0
    avg = round(sum(latencies) / len(latencies), 1) if latencies else None
    return avg, loss_pct


def detect_network_manager(interface: str) -> str:
    """nmcli reports the interface as something other than 'unmanaged' when
    NetworkManager actually owns it (Ángel 2). If nmcli isn't installed or the
    interface is unmanaged, it's the systemd-networkd+wpa_supplicant case (Ángel 1)."""
    raw = run(["nmcli", "-t", "-f", "DEVICE,STATE", "device", "status"])
    for line in raw.strip().splitlines():
        parts = line.split(":")
        if len(parts) == 2 and parts[0] == interface and parts[1] != "unmanaged":
            return "networkmanager"
    return "networkd"


def attempt_recovery(interface: str, manager: str) -> bool:
    """Full recovery path per network stack — a plain reboot always works because it
    restarts every network daemon fresh; a lighter-weight recovery has to explicitly
    undo whatever manual state manipulation caused the disconnect, or it silently does
    nothing (proven twice on Ángel 1, 2026-07-20 — see README.md)."""
    if manager == "networkmanager":
        # NetworkManager itself, not wpa_supplicant, is the source of truth on this
        # host — nmcli connection up re-activates whichever profile is currently
        # associated with the device, undoing any manual disconnect/priority change.
        raw = run(["nmcli", "-t", "-f", "GENERAL.CONNECTION", "device", "show", interface])
        conn_name = raw.split(":", 1)[1].strip() if ":" in raw else ""
        result = subprocess.run(
            ["nmcli", "device", "connect", interface], capture_output=True, text=True, timeout=15
        )
        if conn_name:
            subprocess.run(
                ["nmcli", "connection", "up", conn_name], capture_output=True, timeout=15
            )
        return result.returncode == 0

    # systemd-networkd + wpa_supplicant (sin NetworkManager): re-habilitar todas las
    # redes en wpa_supplicant (una select_network/disable_network deja algunas
    # deshabilitadas dentro de wpa_supplicant, y networkctl reconfigure por sí solo no
    # lo deshace) y recién después pedirle a networkd que reconfigure la interfaz.
    enable_ok = run(["wpa_cli", "-i", interface, "enable_network", "all"]).strip() == "OK"
    subprocess.run(["wpa_cli", "-i", interface, "reconnect"], capture_output=True, timeout=10)
    result = subprocess.run(
        ["networkctl", "reconfigure", interface], capture_output=True, text=True, timeout=10
    )
    return enable_ok and result.returncode == 0


def write_status(status_file: str, status: dict) -> None:
    tmp_path = f"{status_file}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(status, f)
    os.replace(tmp_path, status_file)


def main() -> None:
    cfg = load_config()
    setup_logging(cfg)
    interface = cfg["interface"]
    preferred = cfg["preferred_ssid"]
    backup = cfg["backup_ssid"]

    last_ssid: str | None = None
    connected_since: float | None = None
    last_failover: dict | None = None
    was_no_internet = False
    no_internet_since: float | None = None
    last_recovery_attempt: float | None = None

    manager = cfg["network_manager"]
    if manager == "auto":
        manager = detect_network_manager(interface)

    logger.info("network-guardian iniciado (interface=%s, manager=%s)", interface, manager)

    while True:
        now = time.time()
        link = get_link_info(interface)
        ssid = link["ssid"]
        ip = get_ip(interface)
        gateway = get_gateway()
        targets = [t for t in [gateway, *cfg["extra_ping_targets"]] if t]
        ping_ms, loss_pct = ping_targets(targets, cfg["ping_timeout_sec"])

        if ssid != last_ssid:
            if last_ssid is not None and ssid is not None:
                reason = (
                    "failover_a_respaldo"
                    if ssid == backup
                    else "vuelta_a_preferida"
                    if ssid == preferred
                    else "cambio_red"
                )
                last_failover = {"time": now, "from": last_ssid, "to": ssid, "reason": reason}
                logger.info("%s: %s -> %s", reason, last_ssid, ssid)
            last_ssid = ssid
            connected_since = now if ssid else None

        if ssid is None or loss_pct >= 100:
            status = "no_internet"
            if not was_no_internet:
                logger.warning("sin conexión a internet (ssid=%s)", ssid)
                was_no_internet = True
                no_internet_since = now
        else:
            was_no_internet = False
            no_internet_since = None
            status = "degraded" if loss_pct > 0 else "ok"

        if (
            cfg["recovery_enabled"]
            and no_internet_since is not None
            and now - no_internet_since >= cfg["recovery_after_sec"]
            and (last_recovery_attempt is None or now - last_recovery_attempt >= cfg["recovery_cooldown_sec"])
        ):
            last_recovery_attempt = now
            logger.warning(
                "sin conectividad hace %.0fs, intentando recuperación (manager=%s, interface=%s)",
                now - no_internet_since,
                manager,
                interface,
            )
            ok = attempt_recovery(interface, manager)
            logger.info("recovery_attempt resultado=%s", "ok" if ok else "fallo")

        write_status(
            cfg["status_file"],
            {
                "current_network": ssid,
                "rssi": link["rssi"],
                "link_speed_mbps": link["link_speed_mbps"],
                "ping_ms": ping_ms,
                "packet_loss_pct": loss_pct,
                "ip": ip,
                "status": status,
                "connected_since": connected_since,
                "last_failover": last_failover,
                "updated_at": now,
            },
        )
        time.sleep(cfg["poll_interval_sec"])


if __name__ == "__main__":
    main()
