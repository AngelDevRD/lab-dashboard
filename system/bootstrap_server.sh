#!/bin/bash
# bootstrap_server.sh — deja un servidor Ubuntu 24.04 del laboratorio con la misma
# configuración base de sistema, seguridad y optimización que 192.168.100.7.
# Idempotente: correrlo varias veces no debe romper nada ni duplicar reglas.
#
# NO instala ningún proyecto (eso es un segundo script/deploy aparte, ver
# system/DEPLOY_PROMPT.md). Solo deja la base lista para recibir uno.
#
# Uso:
#   sudo LAN_SUBNET=192.168.100.0/24 ./bootstrap_server.sh
#   sudo LAN_SUBNET=192.168.100.0/24 ENABLE_SSH_HARDENING=yes ./bootstrap_server.sh
#
# Variables de entorno:
#   LAN_SUBNET             (requerido)  subred LAN real de este servidor
#   ENABLE_SSH_HARDENING   (opcional)   "yes" para deshabilitar password auth y
#                                       root login. Requiere que tu usuario YA
#                                       tenga una llave pública funcionando en
#                                       authorized_keys — si no, el script aborta.
#   IS_LAPTOP              (opcional)   "yes"/"no". Si se omite, se autodetecta
#                                       buscando una batería.

set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "Correr con sudo/root." >&2
  exit 1
fi

: "${LAN_SUBNET:?Definí LAN_SUBNET, ej: LAN_SUBNET=192.168.100.0/24}"
ENABLE_SSH_HARDENING="${ENABLE_SSH_HARDENING:-no}"

log() { echo "== $* =="; }

# ---------------------------------------------------------------------------
log "1/9 Paquetes base"
apt-get update -qq
apt-get install -y -qq tlp smartmontools lm-sensors irqbalance fail2ban ufw zram-tools >/dev/null

# ---------------------------------------------------------------------------
log "2/9 Servicios de energía / optimización"
systemctl enable --now tlp >/dev/null 2>&1
systemctl enable --now irqbalance >/dev/null 2>&1
systemctl enable --now fail2ban >/dev/null 2>&1
systemctl enable --now smartmontools >/dev/null 2>&1
systemctl enable --now systemd-oomd >/dev/null 2>&1
systemctl enable --now fstrim.timer >/dev/null 2>&1
systemctl enable --now zramswap.service >/dev/null 2>&1 || true

# ---------------------------------------------------------------------------
log "3/9 UFW (sin abrir nada a Internet; solo SSH + Tailscale)"
ufw --force default deny incoming
ufw --force default allow outgoing
ufw allow 22/tcp comment "SSH" >/dev/null
ufw allow 41641/udp comment "Tailscale" >/dev/null
ufw --force enable >/dev/null
ufw status verbose

# ---------------------------------------------------------------------------
log "4/9 journald - límites"
mkdir -p /etc/systemd/journald.conf.d
cat > /etc/systemd/journald.conf.d/10-limits.conf <<'EOF'
[Journal]
SystemMaxUse=200M
SystemMaxFileSize=20M
MaxRetentionSec=30day
Compress=yes
EOF
systemctl restart systemd-journald

# ---------------------------------------------------------------------------
log "5/9 sysctl - hardening custom (los 10-*.conf y 99-sysctl.conf ya vienen de serie en Ubuntu)"
cat > /etc/sysctl.d/98-network-server-tuning.conf <<'EOF'
# TCP BBR + fq para mejor throughput/latencia (útil con Docker/Nginx/Postgres/Redis)
net.core.default_qdisc=fq
net.ipv4.tcp_congestion_control=bbr

# Buffers de red más generosos para cargas de servidor
net.core.rmem_max=16777216
net.core.wmem_max=16777216
net.ipv4.tcp_rmem=4096 87380 16777216
net.ipv4.tcp_wmem=4096 65536 16777216
net.core.somaxconn=4096
net.ipv4.tcp_max_syn_backlog=4096

# File descriptors (Docker/Postgres/Redis abren muchos)
fs.file-max=2097152

# inotify: necesario para editores, watchers, y muchos contenedores
fs.inotify.max_user_watches=524288
fs.inotify.max_user_instances=512

# Conntrack: tabla de conexiones más grande (útil con Docker + varios contenedores/servicios)
net.netfilter.nf_conntrack_max=131072
EOF
cat > /etc/sysctl.d/99-home-server-tuning.conf <<'EOF'
# Reduce el uso de swap: hay suficiente RAM libre, priorizar velocidad y menos escritura a disco
vm.swappiness=10

# Umbrales de escritura diferida más bajos para reducir picos de I/O al SSD
vm.dirty_ratio=10
vm.dirty_background_ratio=5

# Mantener más caché de inodos/dentries en RAM (barata en un servidor con RAM libre)
vm.vfs_cache_pressure=50
EOF
sysctl --system >/dev/null

# ---------------------------------------------------------------------------
log "6/9 Suspensión / tapa / energía"
IS_LAPTOP="${IS_LAPTOP:-$(ls /sys/class/power_supply/BAT* >/dev/null 2>&1 && echo yes || echo no)}"
systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target >/dev/null 2>&1
if [ "$IS_LAPTOP" = "yes" ]; then
  mkdir -p /etc/systemd/logind.conf.d
  cat > /etc/systemd/logind.conf.d/lid.conf <<'EOF'
[Login]
HandleLidSwitch=ignore
HandleLidSwitchExternalPower=ignore
HandleLidSwitchDocked=ignore
EOF
  systemctl restart systemd-logind
  echo "Laptop detectada: tapa cerrada configurada para ignorarse."
else
  echo "No es laptop (sin batería detectada): se omite config de tapa."
fi

# ---------------------------------------------------------------------------
log "7/9 logrotate (placeholder para cuando se despliegue un proyecto)"
mkdir -p /etc/logrotate.d
if [ ! -f /etc/logrotate.d/lab-dashboard ]; then
  echo "(sin proyecto desplegado todavía; el proyecto que se instale debe traer su propio /etc/logrotate.d/<nombre>)"
fi

# ---------------------------------------------------------------------------
log "8/9 SSH hardening"
if [ "$ENABLE_SSH_HARDENING" != "yes" ]; then
  echo "ENABLE_SSH_HARDENING != yes -> se omite (por defecto, para no arriesgar el acceso remoto)."
else
  DROPIN=/etc/ssh/sshd_config.d/10-hardening.conf
  cat > "$DROPIN" <<'EOF'
PasswordAuthentication no
PermitRootLogin no
EOF
  if ! sshd -t; then
    echo "sshd -t falló, revirtiendo el drop-in." >&2
    rm -f "$DROPIN"
    exit 1
  fi

  TEST_USER="${SUDO_USER:-$(logname 2>/dev/null || echo root)}"
  PIDFILE=/run/sshd-hardening-test.pid
  /usr/sbin/sshd -p 2222 -o PidFile="$PIDFILE"
  sleep 1
  if timeout 8 ssh -p 2222 -o PreferredAuthentications=publickey -o PasswordAuthentication=no \
      -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o BatchMode=yes \
      "${TEST_USER}@localhost" "echo KEY_AUTH_OK" 2>/dev/null | grep -q KEY_AUTH_OK; then
    kill "$(cat "$PIDFILE")" 2>/dev/null || true
    systemctl reload ssh
    echo "SSH hardening aplicado y verificado (login solo-llave confirmado para ${TEST_USER})."
  else
    kill "$(cat "$PIDFILE")" 2>/dev/null || true
    echo "ABORTADO: el login solo-con-llave para ${TEST_USER} falló en la prueba aislada." >&2
    echo "No se tocó el sshd en vivo. Verificá que tu llave pública esté en authorized_keys y reintentá." >&2
    rm -f "$DROPIN"
    exit 1
  fi
fi

# ---------------------------------------------------------------------------
log "9/9 Resumen"
echo "TLP:            $(systemctl is-active tlp)"
echo "irqbalance:     $(systemctl is-active irqbalance)"
echo "fail2ban:       $(systemctl is-active fail2ban)"
echo "smartmontools:  $(systemctl is-active smartmontools)"
echo "systemd-oomd:   $(systemctl is-active systemd-oomd)"
echo "fstrim.timer:   $(systemctl is-active fstrim.timer)"
echo "zramswap:       $(systemctl is-active zramswap.service 2>&1)"
echo "sleep.target:   $(systemctl is-enabled sleep.target 2>&1)"
echo "UFW:            $(ufw status | head -1)"
echo "SSH hardening:  ${ENABLE_SSH_HARDENING}"
echo
echo "Base lista. Para desplegar un proyecto sobre esta base, seguir el patrón de"
echo "system/DEPLOY_PROMPT.md (usuario dedicado, systemd, nginx, GitHub Actions)."
