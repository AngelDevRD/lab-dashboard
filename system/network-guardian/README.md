# Network Guardian — Fase 1 (Ubuntu, Ángel 1 y Ángel 2)

Monitor de conectividad WiFi de solo lectura. **No conmuta de red**: el failover real lo
hace `wpa_supplicant`/`systemd-networkd` solo, en base a las access-points ya declaradas
en netplan para la interfaz WiFi. El agente solo observa (SSID activo, RSSI, link speed,
ping) y deja constancia en `status.json` + un log rotado cuando detecta que la red activa
cambió.

## Red configurada en netplan (no lo toca el agente)

En `/etc/netplan/50-cloud-init.yaml` de cada servidor van **ambas** access-points bajo la
misma interfaz:

```yaml
network:
  version: 2
  wifis:
    wlp1s0:
      addresses: ["192.168.100.X/24"]
      nameservers:
        addresses: ["1.1.1.1", "8.8.8.8"]
      routes:
        - to: "default"
          via: "192.168.100.1"
      access-points:
        "<red-preferida>":
          auth: { key-management: "psk", password: "..." }
        "<red-respaldo>":
          auth: { key-management: "psk", password: "..." }
```

Aplicar cambios con `sudo netplan generate` (valida sintaxis) y luego `sudo netplan
apply`. Al aplicar, la interfaz WiFi se re-asocia brevemente (unos segundos) aunque no
cambie de red — es esperado, no es una falla.

Netplan/wpa_supplicant no exponen una prioridad explícita entre access-points para el
backend `networkd`: la elección de red al reconectar depende del algoritmo interno de
`wpa_supplicant` (favorece la de mejor señal entre las visibles). En la práctica, si el
repetidor tiene señal claramente más fuerte que el router de respaldo (caso típico), esto
ya se comporta como "preferida > respaldo" sin configuración adicional. Si hace falta una
preferencia más estricta, es un ajuste a futuro (fuera de esta fase).

Estado confirmado en **Ángel 1 (192.168.100.7)**: `preferred_ssid: "Angel"` (repetidor,
-18 dBm), `backup_ssid: "santamaria"` (router con UPS) — ambas ya en netplan, agente
instalado. **Ángel 2 (192.168.100.8)**: pendiente — repetir la misma investigación
(`nmcli device status`, `iw dev`, netplan actual) antes de tocar nada, una vez haya
acceso SSH admin.

## Instalación

```bash
scp -r system/network-guardian angel1@<host>:/tmp/network-guardian
ssh angel1@<host> "cd /tmp/network-guardian && sudo ./install.sh"
```

Backup obligatorio antes de tocar netplan en un servidor nuevo:

```bash
sudo cp -a /etc/netplan/50-cloud-init.yaml /etc/netplan/50-cloud-init.yaml.bak-$(date +%Y%m%d)
```

## Incidente 2026-07-20 — servidor inaccesible tras prueba de failover

Al simular la caída del repetidor en Ángel 1 (`wpa_cli disable_network` sobre "Angel"), el
servidor quedó **inalcanzable por SSH ~18 minutos**, hasta un reboot manual.

**Causa raíz (confirmada con `journalctl`, `iw scan`, `networkctl status`):**

1. **"santamaria" no es visible desde donde está Ángel 1** — un `iw dev wlp1s0 scan` en
   vivo solo detectó "Angel". No había ninguna red de respaldo al alcance físicamente, más
   allá de cualquier configuración de software.
2. **Causa secundaria (control-plane):** la interfaz la administra `systemd-networkd`
   (habla con `wpa_supplicant` por D-Bus; NetworkManager la reporta `unmanaged` y no
   participa). El comando `wpa_cli disable_network` actúa directo sobre `wpa_supplicant`,
   evitando a `systemd-networkd` — quien realmente decide cuándo reintentar asociación.
   El log mostró el `CTRL-EVENT-DISCONNECTED` y después **silencio total durante 18
   minutos**: ningún escaneo, ningún intento de reconexión, hasta el reboot.

**Corrección aplicada:**

- El agente ahora incluye un **watchdog de recuperación** (`recovery_enabled` en
  `config.yaml`): si no hay internet por más de `recovery_after_sec` (default 90s), corre
  `networkctl reconfigure <interfaz>` — el mismo mecanismo de recuperación que un reboot,
  sin tocar otras interfaces ni reiniciar `systemd-networkd` completo. Con `cooldown` para
  no reintentar en loop si de verdad no hay ninguna red al alcance.
- **Pendiente de acción física/humana** (no es algo que se resuelva por software): hay que
  confirmar que el router de respaldo "santamaria" esté encendido y su señal llegue a la
  ubicación real de Ángel 1 — si no llega, ningún mecanismo de software puede completar el
  failover. Considerar reposicionar el router, agregar un repetidor de esa señal, o mover
  el servidor.
- **Regla adoptada para pruebas futuras:** nunca volver a forzar la desconexión de la red
  activa vía `wpa_cli` sin haber confirmado antes (a) que la red de respaldo es visible
  desde esa ubicación y (b) que el watchdog de recuperación está instalado y activo.

## Incidente 2026-07-20 (segundo) — el watchdog no alcanzó a recuperar

Con el watchdog ya desplegado, se probó una conexión dirigida a "santamaria"
(`wpa_cli select_network 0`) para ver si era alcanzable con un intento activo en vez de
depender solo del escaneo pasivo. El servidor quedó inaccesible **más de 5 minutos**,
muy por encima del umbral de recuperación (90s) — requirió un segundo reboot manual.

**Causa:** `select_network` deshabilita internamente, dentro de `wpa_supplicant`, todas
las redes salvo la seleccionada. La recuperación original solo corría `networkctl
reconfigure <iface>`, que le pide a `systemd-networkd` que reaplique la config de la
interfaz — pero **no reinicia `wpa_supplicant` ni deshace su estado interno de redes
deshabilitadas**. Por eso el primer incidente (causado por `disable_network`) sí se
resolvía con un reboot (arranca `wpa_supplicant` limpio) pero el watchdog con
`networkctl reconfigure` a secas no alcanzaba para deshacer un `select_network`.

**Corrección:** `attempt_recovery()` ahora corre `wpa_cli enable_network all` +
`wpa_cli reconnect` **antes** de `networkctl reconfigure`, así cualquier red deshabilitada
manualmente (por cualquier motivo) se re-habilita como parte de la recuperación
automática, no solo al reiniciar.

**Nota sobre "santamaria" en esta ubicación:** con el dominio regulatorio ya corregido a
`DO` (antes estaba en el genérico "00"/mundo) y escaneos repetidos (pasivo y activo),
"santamaria" sigue sin aparecer desde la ubicación de Ángel 1. La tablet la detectó desde
el mismo lugar exacto, así que no es (solo) distancia física — falta confirmar banda/canal
real del router (pendiente: revisar el panel de administración de "santamaria") antes de
seguir probando conexiones en vivo. **Regla reforzada:** no repetir pruebas de conexión
dirigida sin ese dato, dado que ya causó dos cortes de producción en el mismo día.

## Archivos

- `network_guardian_agent.py` — el agente (loop de solo lectura, sin dependencias fuera
  de `pyyaml` + herramientas de sistema `iw`/`ip`/`ping`).
- `config.example.yaml` — plantilla, copiar a `/etc/network-guardian/config.yaml`.
- `network-guardian.service` — systemd unit (`Restart=always`).
- `install.sh` — instalación idempotente.
