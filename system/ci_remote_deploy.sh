#!/usr/bin/env bash
# Ejecutado por .github/workflows/deploy.yml vía `ssh ... bash -s < este_archivo`.
# Delega en update-docker.sh, que ya hace fetch/ff-only + build + deploy +
# health-check + rollback automático contra el contenedor Docker real. Este
# wrapper solo traduce su salida al formato DEPLOY_STATUS=/DEPLOY_COMMIT=/...
# que el workflow parsea para el resumen del job.
set -uo pipefail

cd /opt/lab-dashboard

BEFORE=$(git rev-parse HEAD)
START_TS=$(date +%s)

OUTPUT=$(bash system/update-docker.sh 2>&1)
EXIT_CODE=$?
echo "$OUTPUT"

AFTER=$(git rev-parse HEAD)
DOWNTIME=$(( $(date +%s) - START_TS ))

if [ $EXIT_CODE -eq 0 ]; then
  echo "DEPLOY_STATUS=success"
  echo "DEPLOY_COMMIT=$AFTER"
  echo "DEPLOY_DOWNTIME=$DOWNTIME"
  exit 0
fi

echo "DEPLOY_STATUS=deploy_failed"
echo "DEPLOY_DOWNTIME=$DOWNTIME"

if echo "$OUTPUT" | grep -q "=== EJECUTANDO ROLLBACK ==="; then
  if echo "$OUTPUT" | grep -q "Rollback: health check OK"; then
    echo "DEPLOY_ROLLBACK=success"
    echo "DEPLOY_ROLLBACK_COMMIT=$BEFORE"
  else
    echo "DEPLOY_ROLLBACK=failed"
  fi
else
  echo "DEPLOY_ROLLBACK=not_applicable"
fi

exit 1
