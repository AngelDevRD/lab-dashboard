#!/usr/bin/env bash
# Ejecutado por lab-dashboard-update.timer cada 60s.
# Hace git pull; si hubo cambios, reinstala deps si cambió requirements.txt
# y reinicia únicamente el servicio del dashboard.
set -euo pipefail

REPO_DIR="/opt/lab-dashboard"
LOG="/opt/lab-dashboard/logs/update.log"

cd "$REPO_DIR"

BEFORE=$(git rev-parse HEAD)
git fetch origin main --quiet
git reset --hard origin/main --quiet
AFTER=$(git rev-parse HEAD)

if [ "$BEFORE" = "$AFTER" ]; then
  exit 0
fi

echo "$(date -Iseconds) update $BEFORE -> $AFTER" >> "$LOG"

if git diff --name-only "$BEFORE" "$AFTER" | grep -q "backend/requirements.txt"; then
  "$REPO_DIR/backend/.venv/bin/pip" install -q -r "$REPO_DIR/backend/requirements.txt"
fi

sudo systemctl restart lab-dashboard.service
