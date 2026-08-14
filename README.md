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
  lab-dashboard.service          # systemd: proceso del dashboard (legacy)
  lab-dashboard-update.service   # systemd: git pull + restart (legacy)
  lab-dashboard-update.timer     # timer legacy (cada 60s, usaba reset --hard)
  update.sh                      # script legacy (systemd + venv)
  update-docker.sh               # script actual: git pull + docker compose build + deploy + health check + rollback
  lab-dashboard-update.service   # systemd: ejecuta update-docker.sh
  lab-dashboard-update.timer     # timer Docker: cada 30 min
  nginx.conf
```

## Auto-deploy

**El mecanismo que despliega de verdad es el timer de systemd en el servidor**, no
GitHub Actions. Cada 2 minutos, `lab-dashboard-update.timer` corre
`system/update-docker.sh` como el usuario `angel1`:

1. Aborta si el árbol de trabajo está sucio o si el repo no está en `main` — nunca
   pisa trabajo en curso, y nunca usa `reset --hard`.
2. `git fetch` + fast-forward a `origin/main` (si hay divergencia, merge automático).
3. Si `HEAD` ya es igual a `.last-built-commit`, sale sin hacer nada: el ciclo en
   vacío es de ~1s y solo construye cuando hay commits nuevos.
4. `docker compose build` + `up -d`.
5. Health-check con reintentos contra `GET /api/health`.
6. **Si el build, el contenedor o el health-check fallan**, rollback automático al
   commit anterior, rebuild y redeploy. La tablet nunca se queda con una versión
   rota: sigue corriendo la última que pasó el health-check.

Un push que solo cambia el README también se despliega — el timer mira commits, no
rutas.

### Estado del workflow de GitHub Actions

`.github/workflows/deploy.yml` daría despliegue **inmediato** en vez de esperar al
timer, pero **falla en cada push** y sus secrets ya están configurados. La causa no
es el workflow: el servidor tiene Tailscale SSH activo (`RunSSH: true`), que
intercepta el puerto 22 para peers del tailnet y autentica por **ACL del tailnet**,
ignorando la deploy key que el workflow carga con `webfactory/ssh-agent`. En el log
del job se ve al servidor respondiendo `SSH-2.0-Tailscale` en vez de OpenSSH.

Para habilitarlo hace falta una regla `ssh` en la ACL (admin console → Access
Controls), no un cambio en el repo:

```json
"ssh": [
  { "action": "accept", "src": ["tag:ci"], "dst": ["angel1"], "users": ["angel1"] }
]
```

### Dos trampas del despliegue

- **Las units de systemd instaladas son copias, no symlinks al repo.** Editar
  `system/lab-dashboard-update.timer` y pushear **no** actualiza la que corre: hay
  que copiarla a `/etc/systemd/system/` con sudo y hacer `daemon-reload`.
- **La deploy key del servidor es de solo lectura.** Puede hacer `fetch`, no `push`.
  Un commit hecho en caliente en `/opt/lab-dashboard` no puede subirse desde ahí;
  hay que traerlo con `git bundle` y pushearlo desde una máquina con permiso de
  escritura. Mientras ese commit siga sin subir, el árbol diverge y —si además queda
  algo sin commitear— el updater aborta en el paso 1 en cada ciclo.

El dashboard en sí (frontend + WebSocket + polling SSH a los servidores del lab) no
depende de internet, solo de la red local — únicamente el auto-deploy necesita que
este servidor alcance GitHub.

Secrets configurados en GitHub (`Settings → Secrets and variables → Actions`), usados
solo por el workflow:

| Secret | Valor |
|---|---|
| `TS_OAUTH_CLIENT_ID` / `TS_OAUTH_CLIENT_SECRET` | Cliente OAuth de Tailscale (admin console → Settings → OAuth clients), con el tag `tag:ci` autorizado en la ACL |
| `SSH_HOST` | IP Tailscale del servidor de destino |
| `SSH_USER` | Usuario de despliegue en el servidor |
| `SSH_PRIVATE_KEY` | Clave privada de una **deploy key dedicada** (no tu clave personal) |

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

### Requisitos

- Docker Engine 24+ y Docker Compose v2.
- Claves SSH en `~/.ssh/id_ed25519` (o configurar `SSH_KEY_PATH` en `.env`).

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
docker compose logs -f --tail=100
```

### Comprobar estado

```bash
docker compose ps
curl http://localhost:8600/api/health
```

### Reiniciar

```bash
docker compose restart
```

### Actualizar manualmente

```bash
./system/update-docker.sh
```

### Actualizar forzado (si hay cambios locales)

```bash
FORCE=true ./system/update-docker.sh
```

### Rollback manual

```bash
# checkout al commit anterior
git checkout <commit-anterior>
docker compose build
docker compose up -d
```

## Auto-update systemd (Docker)

El sistema incluye un servicio systemd para actualización automática vía Docker:

| Archivo | Propósito |
|---|---|
| `system/update-docker.sh` | Script de actualización seguro con rollback automático |
| `/etc/systemd/system/lab-dashboard-update.service` | Servicio oneshot que ejecuta el script |
| `/etc/systemd/system/lab-dashboard-update.timer` | Timer que revisa cada 2 minutos — es el mecanismo real de despliegue, no un opcional |

### Ejecutar actualización manual

```bash
sudo systemctl start lab-dashboard-update
```

Ver el resultado:

```bash
journalctl -u lab-dashboard-update -n 50 --no-pager
```

### Habilitar / reinstalar el timer (cada 2 min)

Reinstalar hace falta cada vez que cambia el `.timer` del repo: la unit instalada es
una copia, no un symlink.

```bash
cd /opt/lab-dashboard
sudo cp system/lab-dashboard-update.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now lab-dashboard-update.timer
systemctl list-timers lab-dashboard-update.timer --no-pager
```

### Ver estado del timer

```bash
systemctl status lab-dashboard-update.timer
systemctl list-timers --all | grep lab-dashboard
```

### Deshabilitar actualización periódica

```bash
sudo systemctl stop lab-dashboard-update.timer
sudo systemctl disable lab-dashboard-update.timer
```

### Logs del updater

```bash
tail -f logs/update-docker.log
```

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
