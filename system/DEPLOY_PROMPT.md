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

6. **Auto-actualización**: usar `system/lab-dashboard-update.service` y
   `.timer` (reemplazar `__DEPLOY_USER__` también ahí) más `system/update.sh`
   (dar permisos de ejecución). El timer corre cada 60s, hace `git fetch` +
   `reset --hard origin/main`, y si hubo cambios reinstala dependencias (si
   cambió `requirements.txt`) y reinicia SOLO `lab-dashboard.service`. El
   usuario del servicio necesita permiso sudo sin contraseña para
   `systemctl restart lab-dashboard.service` únicamente (agregar una regla
   específica en `/etc/sudoers.d/lab-dashboard`, no sudo general).
   Habilitar y arrancar el timer con `systemctl enable --now lab-dashboard-update.timer`.

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
