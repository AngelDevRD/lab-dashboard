#!/usr/bin/env bash
# Ejecutado por .github/workflows/deploy.yml vía `ssh ... bash -s < este_archivo`.
# Despliegue seguro: fetch/ff-only, deps si cambiaron, tests si existen,
# restart, y health-check con reintentos antes de considerar el deploy exitoso.
set -uo pipefail

cd /opt/lab-dashboard

git fetch origin main --quiet
git checkout main --quiet
BEFORE=$(git rev-parse HEAD)
if ! git pull --ff-only origin main --quiet; then
  echo "DEPLOY_STATUS=fetch_failed"
  exit 1
fi
AFTER=$(git rev-parse HEAD)
CHANGED=$(git diff --name-only "$BEFORE" "$AFTER")

if echo "$CHANGED" | grep -q "backend/requirements.txt"; then
  if ! backend/.venv/bin/pip install -q -r backend/requirements.txt; then
    echo "DEPLOY_STATUS=deps_failed"
    exit 1
  fi
fi

if [ -x backend/.venv/bin/pytest ] && [ -d backend/tests ]; then
  if ! backend/.venv/bin/pytest -q backend/tests; then
    echo "DEPLOY_STATUS=tests_failed"
    exit 1
  fi
fi

RESTART_TS=$(date +%s)
sudo systemctl restart lab-dashboard.service

HEALTHY=0
for _ in $(seq 1 20); do
  if curl -sf http://127.0.0.1:8600/api/health > /dev/null 2>&1; then
    HEALTHY=1
    break
  fi
  sleep 1
done
DOWNTIME=$(( $(date +%s) - RESTART_TS ))

if [ "$HEALTHY" -ne 1 ]; then
  echo "DEPLOY_STATUS=health_check_failed"
  echo "DEPLOY_DOWNTIME=$DOWNTIME"
  echo "---journalctl (last 100)---"
  journalctl -u lab-dashboard.service -n 100 --no-pager
  exit 1
fi

echo "DEPLOY_STATUS=success"
echo "DEPLOY_COMMIT=$AFTER"
echo "DEPLOY_DOWNTIME=$DOWNTIME"
