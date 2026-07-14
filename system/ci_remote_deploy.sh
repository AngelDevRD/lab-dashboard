#!/usr/bin/env bash
# Ejecutado por .github/workflows/deploy.yml vía `ssh ... bash -s < este_archivo`.
# Despliegue seguro: fetch/ff-only, deps si cambiaron, tests si existen, restart,
# health-check con reintentos, y rollback automático al commit anterior si algo
# falla después de haber movido el working tree.
set -uo pipefail

cd /opt/lab-dashboard

install_deps_for() {
  local ref="$1"
  backend/.venv/bin/pip install -q -r backend/requirements.txt
}

rollback_to() {
  local target="$1"
  local changed_back
  changed_back=$(git diff --name-only "$target" HEAD)
  git reset --hard "$target" --quiet
  if echo "$changed_back" | grep -q "backend/requirements.txt"; then
    install_deps_for "$target" || true
  fi
  sudo systemctl restart lab-dashboard.service
  local healthy=0
  for _ in $(seq 1 20); do
    if curl -sf http://127.0.0.1:8600/api/health > /dev/null 2>&1; then
      healthy=1
      break
    fi
    sleep 1
  done
  echo "$healthy"
}

git fetch origin main --quiet
git checkout main --quiet
BEFORE=$(git rev-parse HEAD)
if ! git pull --ff-only origin main --quiet; then
  echo "DEPLOY_STATUS=fetch_failed"
  exit 1
fi
AFTER=$(git rev-parse HEAD)
CHANGED=$(git diff --name-only "$BEFORE" "$AFTER")

FAIL_REASON=""

if echo "$CHANGED" | grep -q "backend/requirements.txt"; then
  if ! install_deps_for "$AFTER"; then
    FAIL_REASON="deps_failed"
  fi
fi

if [ -z "$FAIL_REASON" ] && [ -x backend/.venv/bin/pytest ] && [ -d backend/tests ]; then
  if ! backend/.venv/bin/pytest -q backend/tests; then
    FAIL_REASON="tests_failed"
  fi
fi

HEALTHY=0
DOWNTIME=0
if [ -z "$FAIL_REASON" ]; then
  RESTART_TS=$(date +%s)
  sudo systemctl restart lab-dashboard.service
  for _ in $(seq 1 20); do
    if curl -sf http://127.0.0.1:8600/api/health > /dev/null 2>&1; then
      HEALTHY=1
      break
    fi
    sleep 1
  done
  DOWNTIME=$(( $(date +%s) - RESTART_TS ))
  [ "$HEALTHY" -ne 1 ] && FAIL_REASON="health_check_failed"
fi

if [ -n "$FAIL_REASON" ]; then
  echo "DEPLOY_STATUS=$FAIL_REASON"
  echo "DEPLOY_DOWNTIME=$DOWNTIME"
  echo "---journalctl (last 100, versión que falló)---"
  journalctl -u lab-dashboard.service -n 100 --no-pager

  if [ "$BEFORE" = "$AFTER" ]; then
    echo "DEPLOY_ROLLBACK=not_applicable"
    exit 1
  fi

  echo "--- iniciando rollback a $BEFORE ---"
  ROLLBACK_HEALTHY=$(rollback_to "$BEFORE")
  if [ "$ROLLBACK_HEALTHY" -eq 1 ]; then
    echo "DEPLOY_ROLLBACK=success"
    echo "DEPLOY_ROLLBACK_COMMIT=$BEFORE"
  else
    echo "DEPLOY_ROLLBACK=failed"
    echo "---journalctl (last 100, tras intento de rollback)---"
    journalctl -u lab-dashboard.service -n 100 --no-pager
  fi
  exit 1
fi

echo "DEPLOY_STATUS=success"
echo "DEPLOY_COMMIT=$AFTER"
echo "DEPLOY_DOWNTIME=$DOWNTIME"
