Pegar este prompt completo en una sesión de Claude Code que ya esté conectada por SSH
al servidor Ubuntu donde se va a desplegar el dashboard (uno de los servidores del
laboratorio, ej. 192.168.100.6/7/8).

---

Quiero desplegar en este servidor Ubuntu el proyecto "Lab Dashboard", un backend FastAPI
+ frontend estático que ya está listo en GitHub: `https://github.com/AngelDevRD/lab-dashboard`
(repo privado, rama `main`). Hazlo todo automáticamente:

1. **Clonar el repo** en `/opt/lab-dashboard` (crear el directorio con sudo si hace
   falta, dueño el usuario actual). Si `gh` no está autenticado en este servidor,
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
   `__DEPLOY_USER__` por el usuario real, copiarlo a `/etc/systemd/system/`,
   `systemctl daemon-reload`, `enable --now lab-dashboard.service`. Debe reiniciar
   solo si falla (`Restart=always`) y arrancar en cada boot (`enable`).

6. **Auto-actualización (push-based, vía GitHub Actions)**: el repo ya trae
   `.github/workflows/deploy.yml`. Cada `git push` a `main` desde mi PC dispara
   el workflow: el runner de GitHub se une a mi tailnet (`tailscale/github-action`)
   y se conecta por SSH a este servidor para hacer `git pull` + reinstalar
   dependencias si cambió `requirements.txt` + `systemctl restart
   lab-dashboard.service`. Para que funcione necesito que dejes listo en este
   servidor:
   - Tailscale instalado y conectado a mi tailnet (si no lo está: `curl -fsSL
     https://tailscale.com/install.sh | sh && sudo tailscale up`), y dime la IP
     Tailscale (`tailscale ip -4`) o el nombre MagicDNS de este equipo.
   - Una **deploy key SSH dedicada** (no la mía personal): genera un par
     `~/.ssh/gh_deploy_key` sin passphrase, agrega la pública a
     `~/.ssh/authorized_keys`, y muéstrame la privada para cargarla como
     secret `SSH_PRIVATE_KEY` en GitHub (yo la agrego, tú no la subas a
     ningún lado).
   - Permiso sudo sin contraseña **solo** para
     `systemctl restart lab-dashboard.service` (regla específica en
     `/etc/sudoers.d/lab-dashboard`, no sudo general) para el usuario SSH
     usado por el deploy.

   Dime al final: la IP/nombre Tailscale, el usuario SSH y la clave privada
   generada — yo configuro los secrets `TS_OAUTH_CLIENT_ID`,
   `TS_OAUTH_CLIENT_SECRET`, `SSH_HOST`, `SSH_USER` y `SSH_PRIVATE_KEY` en
   GitHub Actions desde mi lado.

   Como respaldo opcional (si Actions no puede alcanzar el servidor), el repo
   también trae `system/lab-dashboard-update.service` + `.timer` +
   `update.sh`, que hacen lo mismo por polling cada 60s en vez de push. No los
   instales salvo que te lo pida explícitamente.

7. **Nginx** (si está instalado o lo instalas): usar `system/nginx.conf` como
   base, copiarlo a `/etc/nginx/sites-available/lab-dashboard`, enlazar a
   `sites-enabled`, `nginx -t` y reload. El dashboard debe quedar accesible en
   el puerto 80 de este servidor dentro de la red local (sin exponerlo a
   internet). Abrir solo el puerto 80/tcp en el firewall (ufw) si está activo;
   no tocar más reglas.

8. **Verificación final**: confirmar que `systemctl status lab-dashboard.service`
   y `lab-dashboard-update.timer` están `active`, que `curl -s localhost/api/health`
   responde `{"status":"ok"}`, y que al menos un servidor del `servers.json`
   aparece `online` en `/api/status` (si no, dejar claro que falta agregar la
   clave pública SSH a los `authorized_keys` de los servidores monitoreados —
   no es un bug del código).

No expongas el dashboard a internet, no abras más puertos de los necesarios, y
antes de cualquier cambio irreversible (borrar algo, sobrescribir configs
existentes de nginx/systemd) avísame y pide confirmación. Al terminar, dame la
URL local de acceso (`http://<ip-del-servidor>/`) y un resumen de qué quedó
activo.
