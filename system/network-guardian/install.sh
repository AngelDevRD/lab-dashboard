#!/bin/bash
# install.sh — instala/actualiza el agente Network Guardian (solo lectura) en un
# servidor Ubuntu. Idempotente: correrlo de nuevo actualiza el script y el unit file
# sin tocar /etc/network-guardian/config.yaml si ya existe.
#
# Uso: sudo ./install.sh
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "Correr con sudo/root." >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR=/opt/network-guardian
CONFIG_DIR=/etc/network-guardian
LOG_DIR=/var/log/network-guardian

log() { echo "== $* =="; }

log "1/5 Dependencias"
apt-get update -qq
apt-get install -y -qq python3 python3-yaml iw >/dev/null

log "2/5 Directorios"
mkdir -p "$INSTALL_DIR" "$CONFIG_DIR" "$LOG_DIR"

log "3/5 Copiando agente"
cp "$SCRIPT_DIR/network_guardian_agent.py" "$INSTALL_DIR/network_guardian_agent.py"
chmod 755 "$INSTALL_DIR/network_guardian_agent.py"

if [ ! -f "$CONFIG_DIR/config.yaml" ]; then
  log "3b/5 Primera instalación: copiando config.example.yaml"
  cp "$SCRIPT_DIR/config.example.yaml" "$CONFIG_DIR/config.yaml"
  echo "Editá $CONFIG_DIR/config.yaml (interface, preferred_ssid, backup_ssid) antes de continuar."
else
  log "3b/5 Config existente detectada, no se sobreescribe"
fi

log "4/5 systemd unit"
cp "$SCRIPT_DIR/network-guardian.service" /etc/systemd/system/network-guardian.service
systemctl daemon-reload
systemctl enable --now network-guardian.service

log "5/5 Resumen"
sleep 1
systemctl is-active network-guardian.service
echo "Status file: $CONFIG_DIR/status.json"
cat "$CONFIG_DIR/status.json" 2>/dev/null || echo "(status.json todavía no se generó, esperá al próximo ciclo)"
