#!/bin/bash
# install.sh — instala/actualiza el sampler de bateria (solo lectura) en un
# servidor Ubuntu, como servicio systemd que corre indefinidamente. Idempotente:
# correrlo de nuevo actualiza el script y el unit file sin tocar el archivo de
# datos ya acumulado.
#
# Uso: sudo ./install.sh
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "Correr con sudo/root." >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR=/opt/battery-audit
LOG_DIR=/var/log/battery-audit

log() { echo "== $* =="; }

log "1/4 Directorios"
mkdir -p "$INSTALL_DIR" "$LOG_DIR"

log "2/4 Copiando sampler"
cp "$SCRIPT_DIR/collect_samples.py" "$INSTALL_DIR/collect_samples.py"
chmod 755 "$INSTALL_DIR/collect_samples.py"

log "3/4 systemd unit"
cp "$SCRIPT_DIR/battery-audit.service" /etc/systemd/system/battery-audit.service
systemctl daemon-reload
systemctl enable --now battery-audit.service

log "4/4 Resumen"
sleep 2
systemctl is-active battery-audit.service
echo "Archivo de datos: $LOG_DIR/live_samples.txt"
echo "Dejalo correr todo lo que quieras — cuando creas que ya es suficiente tiempo, desde tu PC:"
echo "  mkdir -p system/battery-audit/live_sample_data_largo"
echo "  scp <host>:$LOG_DIR/live_samples.txt system/battery-audit/live_sample_data_largo/live_<hostname-real>.txt"
echo "  python3 system/battery-audit/live_audit_analyze.py system/battery-audit/live_sample_data_largo ."
echo "(no hace falta pararlo para analizar — el archivo se puede copiar en caliente, sigue corriendo)"
