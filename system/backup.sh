#!/bin/bash
# Backup diario de la configuración y logs del Lab Dashboard.
# No incluye el código (eso vive en git) ni el .venv (reinstalable).
set -euo pipefail

APP_DIR="/opt/lab-dashboard"
BACKUP_DIR="${APP_DIR}/backups"
KEEP=30
TS="$(date +%Y%m%d-%H%M%S)"
STAGING="$(mktemp -d)"
trap 'rm -rf "$STAGING"' EXIT

mkdir -p "$BACKUP_DIR"

cp "${APP_DIR}/backend/servers.json" "$STAGING/" 2>/dev/null || true
cp "${APP_DIR}/backend/.env" "$STAGING/" 2>/dev/null || true
[ -d "${APP_DIR}/config" ] && cp -r "${APP_DIR}/config" "$STAGING/" 2>/dev/null || true

# logs: solo los rotados y comprimidos + el log de eventos (texto plano, chico)
mkdir -p "$STAGING/logs"
cp "${APP_DIR}/logs/events.log" "$STAGING/logs/" 2>/dev/null || true
cp "${APP_DIR}"/logs/*.gz "$STAGING/logs/" 2>/dev/null || true

# por si en el futuro se agrega una base SQLite
find "${APP_DIR}" -maxdepth 3 -name "*.db" -exec cp {} "$STAGING/" \; 2>/dev/null || true

ARCHIVE="${BACKUP_DIR}/lab-dashboard-${TS}.tar.gz"
tar -czf "$ARCHIVE" -C "$STAGING" .

# conservar solo los últimos $KEEP
ls -1t "${BACKUP_DIR}"/lab-dashboard-*.tar.gz 2>/dev/null | tail -n "+$((KEEP + 1))" | xargs -r rm -f

echo "Backup creado: $ARCHIVE ($(du -h "$ARCHIVE" | cut -f1))"
echo "Backups conservados: $(ls -1 "${BACKUP_DIR}"/lab-dashboard-*.tar.gz 2>/dev/null | wc -l)"
