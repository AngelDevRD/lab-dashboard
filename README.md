# Lab Dashboard

Dashboard de monitoreo en tiempo real para servidores del laboratorio, pensado para
correr 24/7 en una tablet Android antigua. Frontend 100% HTML/CSS/JS vanilla, backend
FastAPI que se conecta por SSH a cada servidor listado en `backend/servers.json`.

## Estructura

```
backend/
  app/
    collectors/   # comandos SSH + parsers
    ssh_client.py # pool de conexiones SSH reutilizables
    monitor.py    # loop de polling en background + cache
    main.py       # FastAPI + WebSocket + estáticos
  servers.json    # lista de servidores (agregar uno = detección automática)
frontend/
  index.html
  static/css/style.css
  static/js/app.js
.github/workflows/deploy.yml   # push a main -> SSH al servidor vía Tailscale -> restart
system/
  lab-dashboard.service          # systemd: proceso del dashboard
  ci_remote_deploy.sh            # script que corre el workflow EN el servidor (fetch/deps/tests/restart/health-check)
  lab-dashboard-update.service   # systemd: git pull + restart (fallback opcional)
  lab-dashboard-update.timer     # fallback opcional, no se instala por defecto
  update.sh
  nginx.conf
```

## Auto-deploy (push-based, vía GitHub Actions)

Cada `git push` a `main` que toque `backend/`, `frontend/`, `system/` o el propio
workflow dispara `.github/workflows/deploy.yml` (un push que solo cambia el README no
despliega nada). El runner se une a la tailnet (`tailscale/github-action`), carga una
deploy key dedicada y ejecuta un **despliegue seguro** por SSH:

1. `git fetch origin main` + `git checkout main` + `git pull --ff-only` (nunca reescribe
   historia local con `reset --hard`, si el fast-forward falla el deploy se detiene).
2. Si cambió `backend/requirements.txt`, reinstala dependencias.
3. Si existe `backend/tests/`, corre `pytest` antes de reiniciar nada.
4. `systemctl restart lab-dashboard.service`.
5. Health-check: reintenta `GET /api/health` hasta 20s.
6. **Si cualquiera de los pasos 2-5 falla**, rollback automático: `git reset --hard`
   al commit anterior, reinstala dependencias de esa versión si hace falta, reinicia
   el servicio y vuelve a verificar `/api/health`. La tablet nunca se queda mostrando
   una versión rota — sigue corriendo la última que sí pasó el health-check.
7. El job de Actions se marca como **Failed** de todos modos si hubo que hacer
   rollback (para que te enteres), con las últimas 100 líneas de
   `journalctl -u lab-dashboard.service` y el resultado del rollback en los logs.

Al terminar, el job escribe un resumen en la pestaña *Summary* del run con: commit
desplegado, fecha/hora, estado (éxito/error), downtime estimado (segundos entre el
restart y el primer health-check exitoso), resultado del rollback si aplicó, y la URL
del dashboard.

**Usuario dedicado**: todo el proceso (dueño de `/opt/lab-dashboard`, proceso del
servicio, y la cuenta que usa GitHub Actions por SSH) corre bajo `dashboard-deploy`,
un usuario de sistema sin privilegios — nunca root ni tu usuario personal. Su único
permiso sudo es reiniciar `lab-dashboard.service`, nada más (`/etc/sudoers.d/lab-dashboard`).

El dashboard en sí (frontend + WebSocket + polling SSH a los servidores del lab) no
depende de internet, solo de la red local — únicamente el auto-deploy necesita que
este servidor alcance GitHub/Tailscale.

Secrets a configurar en GitHub (`Settings → Secrets and variables → Actions`):

| Secret | Valor |
|---|---|
| `TS_OAUTH_CLIENT_ID` / `TS_OAUTH_CLIENT_SECRET` | Cliente OAuth de Tailscale (admin console → Settings → OAuth clients), con el tag `tag:ci` autorizado en tu ACL |
| `SSH_HOST` | IP o nombre MagicDNS Tailscale del servidor de destino |
| `SSH_USER` | `dashboard-deploy` (usuario dedicado, no root ni tu usuario personal) |
| `SSH_PRIVATE_KEY` | Clave privada de una **deploy key dedicada** (no tu clave personal), cuya pública está en `authorized_keys` del servidor |

El servicio del sistema (`system/lab-dashboard-update.service/.timer`) queda como
fallback opcional por si Actions no puede alcanzar el servidor — no se instala por
defecto, ver `system/DEPLOY_PROMPT.md`.

## Agregar un servidor

Editar `backend/servers.json`:

```json
{ "name": "Servidor 4", "host": "192.168.100.9", "ssh_port": 22, "ssh_user": "ubuntu" }
```

No hace falta tocar HTML/JS: la tarjeta aparece sola en el próximo ciclo de polling.

## Requisitos en cada servidor monitoreado

- Clave pública `~/.ssh/id_ed25519.pub` (la que usa el backend) agregada a
  `~/.ssh/authorized_keys` del usuario SSH configurado.
- Comandos estándar de Linux (`/proc`, `df`, `systemctl`, `ps`). Opcionales:
  `docker`, `sensors` (paquete `lm-sensors`) y `smartctl` (paquete `smartmontools`)
  para temperatura y disco — si no están, el dashboard muestra "No disponible".

## Docker (reemplazo del systemd + venv)

### Construir

```bash
docker compose build
```

### Iniciar

```bash
docker compose up -d
```

Abrir `http://localhost:8600`.

### Detener

```bash
docker compose down
```

Para eliminar también los volúmenes (logs, config):

```bash
docker compose down -v
```

### Ver logs

```bash
docker compose logs -f
```

### Actualizar

```bash
git pull
docker compose build
docker compose up -d
```

### Rollback

```bash
docker compose down
# checkout al commit anterior
git checkout <commit-anterior>
docker compose build
docker compose up -d
```

### Requisitos

- Docker Engine 24+ y Docker Compose v2.
- Claves SSH en `~/.ssh/id_ed25519` (o configurar `SSH_KEY_PATH` en `.env`).

## Desarrollo local (sin Docker)

```bash
cd backend
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt   # o .venv/bin/pip en Linux
.venv/Scripts/python -m uvicorn app.main:app --reload --port 8600
```

Abrir `http://localhost:8600`.

## Despliegue

### Docker (recomendado)

Ver sección Docker arriba. Para Portainer: apuntar stack al repositorio con `docker-compose.yml`.

### Systemd (legacy — migrar a Docker)

Ver `MIGRATION.md`.
