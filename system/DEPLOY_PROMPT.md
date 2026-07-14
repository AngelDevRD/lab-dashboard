Pegar este prompt completo en una sesión de Claude Code que ya esté conectada por SSH
al servidor Ubuntu donde se va a desplegar el dashboard (uno de los servidores del
laboratorio, ej. 192.168.100.6/7/8).

---

Quiero desplegar en este servidor Ubuntu el proyecto "Lab Dashboard", un backend FastAPI
+ frontend estático que ya está listo en GitHub: `https://github.com/AngelDevRD/lab-dashboard`
(repo privado, rama `main`). Hazlo todo automáticamente:

1. **Clonar el repo** en `/opt/lab-dashboard` (crear el directorio con sudo si hace
   falta). Este servidor va a correr todo bajo un usuario de sistema dedicado
   `dashboard-deploy` (ver punto 6) — créalo primero si aún no existe y deja
   `/opt/lab-dashboard` con ese usuario como dueño, no tu usuario personal ni
   root. Si `gh` no está autenticado en este servidor,
   usa una deploy key: genera un par de claves SSH dedicado
   (`~/.ssh/lab_dashboard_deploy`), muéstrame la clave pública para agregarla como
   "Deploy Key" (solo lectura) en GitHub, y configura `~/.ssh/config` para que el
   host `github.com-lab-dashboard` use esa clave al clonar.

2. **Entorno Python**: crear un venv en `/opt/lab-dashboard/backend/.venv` con
   Python 3.11+, instalar `requirements.txt`.

3. **Configuración**: copiar `backend/.env.example` a `backend/.env` y ajustar
   `SSH_KEY_PATH` para que apunte a la clave SSH que este servidor usará para
   monitorear a los demás (generar una si no existe: `~/.ssh/id_ed25519` sin
   passphrase, y mostrarme la pública para agregarla a `authorized_keys` de los
   otros servidores del laboratorio).

4. **Revisar `backend/servers.json`**: confirmar que los hosts/usuarios SSH ahí
   listados son correctos para este laboratorio; si no, pídeme los datos antes de
   continuar.

5. **Servicio systemd**: usar `system/lab-dashboard.service` como base — reemplazar
   `__DEPLOY_USER__` por `dashboard-deploy` (el mismo usuario dedicado del punto 6,
   dueño de `/opt/lab-dashboard`), copiarlo a `/etc/systemd/system/`,
   `systemctl daemon-reload`, `enable --now lab-dashboard.service`. Debe reiniciar
   solo si falla (`Restart=always`) y arrancar en cada boot (`enable`).

6. **Auto-actualización (push-based, vía GitHub Actions, con usuario dedicado
   y rollback automático)**: el repo ya trae `.github/workflows/deploy.yml` +
   `system/ci_remote_deploy.sh`. Cada `git push` a `main` (que toque
   `backend/`, `frontend/` o `system/`) dispara el workflow: el runner de
   GitHub se une a mi tailnet (`tailscale/github-action`) y se conecta por SSH
   a este servidor para correr `ci_remote_deploy.sh`, que hace `git fetch` +
   `checkout main` + `pull --ff-only` (nunca reescribe historia local),
   reinstala dependencias si cambió `requirements.txt`, corre `pytest` si
   existe `backend/tests/`, reinicia el servicio y hace health-check contra
   `/api/health` con reintentos de hasta 20s. **Si cualquiera de esos pasos
   falla, hace rollback automático**: vuelve al commit anterior con
   `git reset --hard`, reinstala dependencias de esa versión si hace falta,
   reinicia el servicio otra vez y vuelve a verificar `/api/health` — así una
   actualización rota no deja la tablet mostrando error, sigue corriendo la
   última versión estable. El job de GitHub Actions queda marcado como
   fallido de todos modos (para que yo me entere), con los últimos 100 logs
   de `journalctl -u lab-dashboard.service` y el resultado del rollback.

   Este proceso **no debe correr como root ni con mi usuario personal**.
   Necesito que crees un usuario de sistema dedicado solo para esto:

   - Crear el usuario `dashboard-deploy` (`sudo useradd --system --create-home
     --shell /bin/bash dashboard-deploy`), sin acceso a nada más del servidor.
   - Dueño de `/opt/lab-dashboard` debe ser `dashboard-deploy` (o un grupo
     compartido con permisos de escritura para ese usuario).
   - Tailscale instalado y conectado a mi tailnet (si no lo está: `curl -fsSL
     https://tailscale.com/install.sh | sh && sudo tailscale up`), y dime la IP
     Tailscale (`tailscale ip -4`) o el nombre MagicDNS de este equipo.
   - Una **deploy key SSH dedicada** para `dashboard-deploy` (no mi clave
     personal): genera un par `~/.ssh/gh_deploy_key` sin passphrase para ese
     usuario, agrega la pública a su `~/.ssh/authorized_keys`, y muéstrame la
     privada para cargarla como secret `SSH_PRIVATE_KEY` en GitHub (yo la
     agrego, tú no la subas a ningún lado).
   - Permiso sudo sin contraseña para `dashboard-deploy`, **acotado
     únicamente** a `systemctl restart lab-dashboard.service` (regla
     específica en `/etc/sudoers.d/lab-dashboard`, no sudo general, no acceso
     a otros comandos ni servicios).

   Dime al final: la IP/nombre Tailscale, y la clave privada de
   `dashboard-deploy` — yo configuro los secrets `TS_OAUTH_CLIENT_ID`,
   `TS_OAUTH_CLIENT_SECRET`, `SSH_HOST`, `SSH_USER=dashboard-deploy` y
   `SSH_PRIVATE_KEY` en GitHub Actions desde mi lado.

   Como respaldo opcional (si Actions no puede alcanzar el servidor), el repo
   también trae `system/lab-dashboard-update.service` + `.timer` +
   `update.sh`, que hacen lo mismo por polling cada 60s en vez de push. No los
   instales salvo que te lo pida explícitamente.

7. **Nginx** (si está instalado o lo instalas): usar `system/nginx.conf` como
   base, copiarlo a `/etc/nginx/sites-available/lab-dashboard`, enlazar a
   `sites-enabled`, `nginx -t` y reload. El dashboard debe quedar accesible en
   el puerto 80 de este servidor dentro de la red local (sin exponerlo a
   internet). Si `ufw` está activo, **no** uses `sudo ufw allow 80/tcp` (eso
   abre el puerto a "Anywhere", incluyendo internet si el servidor tiene otra
   interfaz expuesta) — pregúntame primero cuál es la subred LAN real de este
   servidor (puede no ser `192.168.100.0/24`) y usa la regla restringida:

   ```bash
   sudo ufw allow from <SUBRED_LAN>/24 to any port 80 proto tcp comment "Lab Dashboard LAN"
   ```

   Aplica el mismo criterio (`ufw allow from <SUBRED_LAN>/24 to any port ...`,
   nunca `ufw allow <puerto>/tcp` a secas) si en el futuro se abre algún otro
   puerto del dashboard. No tocar más reglas de firewall.

   La tablet va a acceder por `http://<ip-local-de-este-servidor>/` por
   WiFi/LAN — el dashboard en sí no depende de que haya internet (solo el
   auto-deploy vía GitHub Actions lo necesita); si se cae la conexión a
   internet, el panel sigue funcionando y sencillamente reporta "sin
   internet" en la tarjeta correspondiente.

8. **Verificación final**: confirmar que `systemctl status lab-dashboard.service`
   está `active`, que `curl -s localhost/api/health` responde `{"status":"ok"}`,
   y que al menos un servidor del `servers.json` (usuario SSH `angel1`) aparece
   `online` en `/api/status` (si no, dejar claro que falta agregar la clave
   pública SSH — `~/.ssh/id_ed25519.pub` generada en el paso 3 — a
   `authorized_keys` del usuario `angel1` en los servidores monitoreados; no es
   un bug del código).

No expongas el dashboard a internet, no abras más puertos de los necesarios, y
antes de cualquier cambio irreversible (borrar algo, sobrescribir configs
existentes de nginx/systemd) avísame y pide confirmación. Al terminar, dame la
URL local de acceso (`http://<ip-del-servidor>/`) y un resumen de qué quedó
activo.
