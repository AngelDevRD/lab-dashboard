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
  lab-dashboard-update.service   # systemd: git pull + restart (fallback opcional)
  lab-dashboard-update.timer     # fallback opcional, no se instala por defecto
  update.sh
  nginx.conf
```

## Auto-deploy (push-based, vía GitHub Actions)

Cada `git push` a `main` dispara `.github/workflows/deploy.yml`: el runner se une a la
tailnet (acción `tailscale/github-action`), se conecta por SSH al servidor y hace
`git pull` + reinstala dependencias si cambió `requirements.txt` + `systemctl restart
lab-dashboard.service`. No hace falta reconectarse por SSH manualmente para actualizar.

Secrets a configurar en GitHub (`Settings → Secrets and variables → Actions`):

| Secret | Valor |
|---|---|
| `TS_OAUTH_CLIENT_ID` / `TS_OAUTH_CLIENT_SECRET` | Cliente OAuth de Tailscale (admin console → Settings → OAuth clients), con el tag `tag:ci` autorizado en tu ACL |
| `SSH_HOST` | IP o nombre MagicDNS Tailscale del servidor de destino |
| `SSH_USER` | Usuario SSH del servidor (ej. `ubuntu`) |
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

## Desarrollo local

```bash
cd backend
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt   # o .venv/bin/pip en Linux
.venv/Scripts/python -m uvicorn app.main:app --reload --port 8600
```

Abrir `http://localhost:8600`.

## Despliegue

Ver el prompt de despliegue en `system/DEPLOY_PROMPT.md` para ejecutar en una sesión
de Claude Code conectada por SSH al servidor Ubuntu.
