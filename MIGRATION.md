# Migración: systemd + venv → Docker

## Resumen

El proyecto ahora se ejecuta con Docker. El systemd + venv queda como legacy.

## Archivos obsoletos (no se borran — quedan como referencia)

| Archivo | Razón |
|---|---|
| `system/lab-dashboard.service` | Reemplazado por `docker compose up -d` |
| `system/lab-dashboard-update.service` | Reemplazado por `docker compose build && docker compose up -d` |
| `system/lab-dashboard-update.timer` | Ya no necesario |
| `system/lab-dashboard-backup.service` | Los volúmenes Docker + git son el respaldo |
| `system/lab-dashboard-backup.timer` | Ya no necesario |
| `system/update.sh` | Usaba `systemctl restart` + pip |
| `system/ci_remote_deploy.sh` | Usaba `systemctl restart`, pip, `journalctl`. Requiere reescritura para Docker si se usa CI |
| `system/bootstrap_server.sh` | Sigue siendo útil para bootstrap inicial del servidor |
| `system/nginx.conf` | Ya no es necesario si se usa solo Docker; puede servir como referencia si se quiere proxy inverso externo |
| `system/DEPLOY_PROMPT.md` | Contenido desactualizado para systemd |

## Archivos creados

| Archivo | Propósito |
|---|---|
| `Dockerfile` | Imagen Python 3.11 slim con la app |
| `docker-compose.yml` | Orquestación del contenedor |
| `.dockerignore` | Excluye archivos innecesarios de la imagen |
| `MIGRATION.md` | Este documento |

## Archivos modificados

| Archivo | Cambio |
|---|---|
| `README.md` | Sección Docker agregada; desarrollo local legacy se mantiene |
| `.gitignore` | Entradas Docker agregadas |

## Cambios en el código

Ninguno. `_sd_notify()` y `_watchdog_loop()` en `backend/app/main.py` ya detectan
automáticamente la ausencia de systemd (`NOTIFY_SOCKET` / `WATCHDOG_USEC` no seteados)
y se comportan como no-op. No se requirió modificar ni una línea de la app.

## Volúmenes montados

| Host | Contenedor | Propósito |
|---|---|---|
| `./backend/servers.json` | `/app/servers.json` | Lista de servidores (editable sin rebuild) |
| `./logs` | `/logs` | Logs de eventos persistentes |
| `~/.ssh` | `/app/.ssh:ro` | Claves SSH (solo lectura) |
| `./config` | `/app/config` | Configuración persistente futura |

## Variables de entorno

Se cargan desde `backend/.env` vía `env_file` de Docker Compose. Las variables
existentes (`SSH_KEY_PATH`, `POLL_INTERVAL`, etc.) funcionan igual.

## Próximos pasos recomendados

1. Probar en el servidor de producción: `docker compose up -d`
2. Deshabilitar el servicio systemd: `sudo systemctl stop lab-dashboard.service && sudo systemctl disable lab-dashboard.service`
3. Configurar Portainer apuntando a este repositorio
4. Actualizar `.github/workflows/deploy.yml` para usar Docker en lugar de systemd
5. Eliminar archivos obsoletos de `system/` después de verificar que todo funciona
