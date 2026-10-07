#!/usr/bin/env bash
# update-docker.sh — Actualización segura del dashboard vía Docker
#
# Uso:
#   ./system/update-docker.sh          # ejecuta actualización
#   FORCE=true ./system/update-docker.sh  # salta verificación de cambios locales
#
# Comportamiento:
#   1. Verifica que no haya cambios locales sin guardar (aborta si los hay)
#   2. git fetch origin + checkout main + pull --ff-only
#   3. Si hay commits nuevos: build + deploy + health-check
#   4. Si el health-check falla: rollback automático al commit anterior
#   5. Nunca usa reset --hard ni docker compose down sin necesidad

set -euo pipefail

REPO_DIR="/opt/lab-dashboard"
LOG_DIR="${REPO_DIR}/logs"
LOG_FILE="${LOG_DIR}/update-docker.log"
LOCK_FILE="/run/lock/lab-dashboard-update.lock"
HEALTH_URL="http://localhost:8600/api/health"
MAX_RETRIES=12
RETRY_DELAY=5
ROLLBACK=false
LAST_BUILT_FILE="${REPO_DIR}/.last-built-commit"

mkdir -p "$LOG_DIR"

log() {
  echo "$(date -Iseconds) [$$] $*" | tee -a "$LOG_FILE"
}

# Una sola actualizacion a la vez: el timer y una corrida manual pueden
# coincidir y hacer build/up -d (o rollback) en paralelo. El lock vive en
# /run/lock (fuera del repo, asi no cuenta como archivo sin trackear).
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  log "Otra actualización está en curso (lock $LOCK_FILE). Saliendo sin hacer nada."
  exit 0
fi

log "=== Inicio de actualización ==="

cd "$REPO_DIR"

# --- 1. Verificar cambios locales ---
if ! git diff --quiet HEAD; then
  if [ "${FORCE:-false}" = "true" ]; then
    log "WARN: Hay cambios locales sin guardar. FORCE=true, continuando de todas formas."
  else
    log "ERROR: Hay cambios locales sin guardar. Abortando."
    log "       Para ignorar: FORCE=true ./system/update-docker.sh"
    exit 1
  fi
fi

UNTRACKED=$(git ls-files --others --exclude-standard | head -5)
if [ -n "$UNTRACKED" ] && [ "${FORCE:-false}" != "true" ]; then
  log "ERROR: Hay archivos sin trackear. Abortando."
  log "       Archivos: $UNTRACKED"
  exit 1
fi

# --- 2. Guardar commit actual para rollback ---
CURRENT=$(git rev-parse HEAD)
log "Commit actual: $CURRENT"

# --- 3. Fetch + checkout + merge ---
log "Actualizando desde origin..."
git fetch origin 2>&1 | tee -a "$LOG_FILE"

# Si el repo esta en una rama que no es main, es una rama de trabajo activa
# (ej. una feature branch a medio revisar) -- nunca cambiarla automaticamente.
# Un incidente real: este updater switcheo a main mientras habia trabajo sin
# terminar en otra rama, pisando el checkout (los commits no se perdieron,
# pero el working tree quedo en la rama equivocada a mitad de una sesion).
# A diferencia del chequeo de cambios sin commitear (paso 1), esto NO se
# puede saltear con FORCE=true: forzar el update no es lo mismo que forzar
# un cambio de rama que no pediste.
CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD)
if [ "$CURRENT_BRANCH" != "main" ]; then
  log "ERROR: el repositorio esta en la rama '$CURRENT_BRANCH', no en main. Abortando."
  log "       No se cambia de rama automaticamente -- puede haber trabajo en curso."
  log "       Si esa rama ya no hace falta, cambiar a mano a main y reintentar."
  exit 1
fi

# Intentar fast-forward; si falla (divergencia), hacer merge automático
if git merge --ff-only origin/main 2>&1 | tee -a "$LOG_FILE"; then
  log "Fast-forward exitoso."
else
  log "WARN: Fast-forward no posible (commits locales presentes). Haciendo merge automático..."
  if git merge origin/main --no-edit 2>&1 | tee -a "$LOG_FILE"; then
    log "Merge automático completado."
  else
    log "ERROR: Merge con origin/main falló. Abortando."
    exit 1
  fi
fi

HEAD=$(git rev-parse HEAD)

# --- 4. Comparar contra el último commit buildado ---
LAST_BUILT=""
if [ -f "$LAST_BUILT_FILE" ]; then
  LAST_BUILT=$(cat "$LAST_BUILT_FILE")
fi

if [ "$HEAD" = "$LAST_BUILT" ]; then
  log "Último build: $LAST_BUILT — sin cambios nuevos."
  exit 0
fi

log "Nuevos cambios detectados (HEAD $HEAD != last-built $LAST_BUILT)."

# --- 5. Build de la imagen ---
log "Construyendo nueva imagen Docker..."
if ! docker compose build 2>&1 | tee -a "$LOG_FILE"; then
  log "ERROR: docker compose build falló. Haciendo rollback..."
  git checkout "$CURRENT" 2>&1 | tee -a "$LOG_FILE"
  log "Rollback completado. Intentando reconstruir imagen anterior..."
  if docker compose build 2>&1 | tee -a "$LOG_FILE"; then
    log "Rollback: build exitoso. No se redeploya automáticamente — el contenedor anterior sigue corriendo."
  else
    log "ERROR CRÍTICO: Rollback build también falló. El contenedor anterior sigue en ejecución."
  fi
  exit 1
fi

# --- 6. Deploy ---
log "Desplegando nuevo contenedor..."
docker compose up -d 2>&1 | tee -a "$LOG_FILE"

# --- 7. Verificar estado del contenedor ---
sleep 3
if ! docker compose ps --status running 2>&1 | grep -q "lab-dashboard"; then
  log "ERROR: El contenedor no está en estado running. Iniciando rollback..."
  ROLLBACK=true
fi

# --- 8. Health check con reintentos ---
if [ "$ROLLBACK" = false ]; then
  HEALTH_OK=false
  for i in $(seq 1 $MAX_RETRIES); do
    if curl -sf "$HEALTH_URL" > /dev/null 2>&1; then
      HEALTH_OK=true
      log "Health check OK (intento $i)"
      break
    fi
    log "Health check: intento $i/$MAX_RETRIES falló, esperando ${RETRY_DELAY}s..."
    sleep "$RETRY_DELAY"
  done

  if [ "$HEALTH_OK" = false ]; then
    log "ERROR: Health check falló después de $MAX_RETRIES intentos. Iniciando rollback..."
    ROLLBACK=true
  fi
fi

# --- 9. Rollback si es necesario ---
if [ "$ROLLBACK" = true ]; then
  log "=== EJECUTANDO ROLLBACK ==="
  log "Revirtiendo a commit: $CURRENT"
  git checkout "$CURRENT" 2>&1 | tee -a "$LOG_FILE"

  log "Reconstruyendo imagen anterior..."
  if docker compose build 2>&1 | tee -a "$LOG_FILE"; then
    log "Redeployando contenedor anterior..."
    docker compose up -d 2>&1 | tee -a "$LOG_FILE"
    sleep 3

    for i in $(seq 1 $MAX_RETRIES); do
      if curl -sf "$HEALTH_URL" > /dev/null 2>&1; then
        log "Rollback: health check OK después de rollback (intento $i)"
        ROLLBACK=false
        break
      fi
      log "Rollback: health check intento $i/$MAX_RETRIES..."
      sleep "$RETRY_DELAY"
    done

    if [ "$ROLLBACK" = true ]; then
      log "ERROR CRÍTICO: Rollback desplegado pero health check sigue fallando."
      log "            El contenedor anterior puede no responder. Revisar manualmente."
    fi
  else
    log "ERROR CRÍTICO: Rollback build falló. El contenedor nuevo puede estar caído."
    log "            Revisar manualmente con: docker compose logs"
  fi

  log "=== FIN DE ROLLBACK ==="
  exit 1
fi

# --- 10. Guardar commit buildado ---
echo "$HEAD" > "$LAST_BUILT_FILE"
log "Build registrado: $HEAD"

# --- 11. Limpieza de imágenes antiguas ---
log "Limpiando imágenes Docker no utilizadas..."
docker image prune -f 2>&1 | tee -a "$LOG_FILE"

log "=== Actualización completada exitosamente ==="
log "  Commit: $HEAD"
log "  Health: $HEALTH_URL -> OK"
exit 0
