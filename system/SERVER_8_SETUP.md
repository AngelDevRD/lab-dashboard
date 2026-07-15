# Configuración base para 192.168.100.8

Este documento deja al servidor `.8` con la misma configuración de sistema operativo,
seguridad y optimización que `192.168.100.7`, **sin desplegar ningún proyecto** — `.8`
queda preparado para recibir uno en el futuro.

Ejecutar como `angel2` (usuario actual en `.8`) con `sudo`.

## 0. Requisito previo: acceso SSH por llave (antes de tocar nada de seguridad)

`.8` hoy solo tiene acceso por password para `angel2` — **no deshabilitar
`PasswordAuthentication` hasta completar y verificar este paso**, o se pierde el acceso
remoto.

1. En tu máquina de administración, genera o reutiliza una clave (`~/.ssh/id_ed25519`).
2. Copia la pública a `.8`: `ssh-copy-id angel2@192.168.100.8` (o pégala a mano en
   `~/.ssh/authorized_keys`).
3. Verifica en una sesión **nueva**: `ssh -o PreferredAuthentications=publickey -o
   PasswordAuthentication=no angel2@192.168.100.8` — debe entrar sin pedir password.
4. Solo entonces continuar con la sección 5 (SSH hardening).

## 1. Paquetes y servicios de energía/optimización

```bash
sudo apt update
sudo apt install -y tlp smartmontools lm-sensors irqbalance fail2ban ufw zram-tools
sudo systemctl enable --now tlp
sudo systemctl enable --now irqbalance
sudo systemctl enable --now fail2ban
sudo systemctl enable --now smartmontools
sudo systemctl enable --now systemd-oomd
sudo systemctl enable --now fstrim.timer
```

`zram-tools` (paquete `zramswap.service`) — en `.7` está configurado con `/dev/zram0` de
1.8G. Revisar/ajustar `/etc/default/zramswap` si el tamaño de RAM de `.8` es distinto
antes de habilitar:

```bash
sudo systemctl enable --now zramswap.service
swapon --show   # confirmar zram0 aparece
```

## 2. Firewall (UFW)

**No copiar la regla del puerto 80 de `.7`** — `.8` no sirve nada todavía.

```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow 22/tcp comment "SSH"
# Si .8 se une a la misma tailnet, permitir el puerto UDP de Tailscale:
sudo ufw allow 41641/udp comment "Tailscale"
sudo ufw enable
sudo ufw status verbose
```

Cuando en el futuro se despliegue un proyecto en `.8` y necesite exponer un puerto en la
LAN, usar el mismo criterio que en `.7` — **nunca** `ufw allow <puerto>/tcp` a secas:

```bash
sudo ufw allow from <SUBRED_LAN_REAL>/24 to any port <PUERTO> proto tcp comment "<descripción>"
```

Confirmar primero cuál es la subred LAN real de `.8` (puede no ser `192.168.100.0/24`).

## 3. journald — límites de logs

```bash
sudo mkdir -p /etc/systemd/journald.conf.d
sudo tee /etc/systemd/journald.conf.d/10-limits.conf > /dev/null <<'EOF'
[Journal]
SystemMaxUse=200M
SystemMaxFileSize=20M
MaxRetentionSec=30day
Compress=yes
EOF
sudo systemctl restart systemd-journald
journalctl --disk-usage
```

## 4. sysctl — hardening de red/kernel

`.7` tiene hardening custom en `/etc/sysctl.d/` (`10-kernel-hardening.conf`,
`10-network-security.conf`, `10-ipv6-privacy.conf`, `10-bufferbloat.conf`,
`10-console-messages.conf`, `10-magic-sysrq.conf`, `10-map-count.conf`,
`10-ptrace.conf`, `10-zeropage.conf`, `98-network-server-tuning.conf`,
`99-home-server-tuning.conf`). Antes de recrearlos a mano, revisar si `.8` ya los tiene
(pueden venir de una imagen base común):

```bash
ls /etc/sysctl.d/
```

Si faltan, copiarlos literalmente desde `.7` (son configuración de SO, no del
proyecto):

```bash
# Desde una máquina con acceso a ambos, o copiando archivo por archivo:
scp angel1@192.168.100.7:/etc/sysctl.d/10-*.conf angel1@192.168.100.7:/etc/sysctl.d/9*.conf /tmp/
scp /tmp/*.conf angel2@192.168.100.8:/tmp/
ssh angel2@192.168.100.8 'sudo mv /tmp/*.conf /etc/sysctl.d/ && sudo sysctl --system'
```

## 5. SSH hardening (solo después de verificar la sección 0)

```bash
sudo tee /etc/ssh/sshd_config.d/10-hardening.conf > /dev/null <<'EOF'
PasswordAuthentication no
PermitRootLogin no
EOF
sudo sshd -t && echo SYNTAX_OK
```

Antes de recargar, probar en una instancia aislada en el puerto 2222 (no toca el
`sshd` en vivo del puerto 22):

```bash
sudo /usr/sbin/sshd -p 2222 -o PidFile=/run/sshd-hardening-test.pid
ssh -p 2222 -o PreferredAuthentications=publickey -o PasswordAuthentication=no \
    -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o BatchMode=yes \
    angel2@localhost "echo KEY_AUTH_WORKS"
sudo kill $(sudo cat /run/sshd-hardening-test.pid)
```

Si `KEY_AUTH_WORKS` aparece, recién entonces:

```bash
sudo systemctl reload ssh
# Verificar en una sesión NUEVA (no reusar la actual) antes de cerrar la sesión vieja:
ssh -o PreferredAuthentications=publickey -o PasswordAuthentication=no angel2@192.168.100.8 echo OK
```

## 6. Suspensión / tapa / ahorro de energía

Confirmar primero si `.8` es una laptop (como `.7`, que tiene sensor de batería):

```bash
upower -e 2>/dev/null | grep -i BAT; sensors 2>&1 | grep -i BAT
```

Si es laptop:

```bash
sudo systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target
sudo mkdir -p /etc/systemd/logind.conf.d
sudo tee /etc/systemd/logind.conf.d/lid.conf > /dev/null <<'EOF'
[Login]
HandleLidSwitch=ignore
HandleLidSwitchExternalPower=ignore
HandleLidSwitchDocked=ignore
EOF
sudo systemctl restart systemd-logind
```

CPU governor — en `.7` quedó en `powersave` (reduce consumo en un servidor 24/7 de bajo
uso). Revisar y alinear si aplica:

```bash
cat /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor
```

## 7. Verificación final de `.8`

```bash
systemctl is-active tlp irqbalance fail2ban smartmontools systemd-oomd fstrim.timer
sudo ufw status verbose
journalctl --disk-usage
systemctl is-enabled sleep.target   # debe decir "masked" si es laptop
sudo sshd -T | grep -Ei "passwordauthentication|permitrootlogin"
```

## Lo que NO incluye este documento (a propósito)

- Clonar el repo `lab-dashboard`, crear el usuario `dashboard-deploy`, el servicio
  systemd del dashboard, nginx, ni los secrets de GitHub Actions — eso es específico del
  proyecto que se decida correr en `.8`. Cuando se elija, seguir el mismo patrón de
  `system/DEPLOY_PROMPT.md`, adaptado al nuevo proyecto.
- Agregar la clave pública de monitoreo (`lab-dashboard-monitor@angel1`) a
  `authorized_keys` de `angel2` en `.8` — **eso sí aplica ya**, para que el dashboard en
  `.7` pueda seguir monitoreando `.8` como "Servidor 3". Ver siguiente sección.

## Pendiente ahora mismo (no espera a elegir proyecto)

Para que el dashboard en `.7` monitoree `.8` correctamente, la clave pública de
monitoreo debe estar en `authorized_keys` de `angel2`:

```bash
# En .7, obtener la pública (no es secreta, es la mitad pública):
sudo cat /home/dashboard-deploy/.ssh/id_ed25519.pub
# Pegarla en .8:
echo "<esa línea>" >> ~/.ssh/authorized_keys   # como angel2
```

(Ya se confirmó que esto está funcionando — `.8` aparece `online` en `/api/status` del
dashboard.)
