# Claude Usage Reporter

Envía periódicamente el uso de Claude Code (ccusage) desde esta PC hacia el
backend de Lab Dashboard en `angel1`, para que la vista "Métricas" muestre
datos reales incluso cuando esta PC está apagada o sin red — el servidor
sirve el último reporte recibido con su fecha/hora.

## Por qué existe

`ccusage` solo puede leer los logs de sesión de Claude Code que existen en
el disco donde corrió (`~/.claude/projects`). El backend corre en `angel1`,
donde Claude Code nunca se ejecuta, así que pedirle que corra `ccusage` él
mismo siempre devolvía datos vacíos. Esta PC sí tiene los logs reales, así
que es quien calcula y empuja el reporte.

## Instalación

1. Copiar la config de ejemplo y completar los valores:

   ```powershell
   Copy-Item config.example.json config.json
   notepad config.json
   ```

   - `server_url`: URL del backend (`http://192.168.100.7:8600` por defecto).
   - `token`: debe coincidir exactamente con `CLAUDE_USAGE_REPORT_TOKEN` en el
     `.env` del backend en `angel1`. Generar uno nuevo si no existe:

     ```powershell
     -join ((48..57)+(97..122)|Get-Random -Count 32|%{[char]$_})
     ```

     Y agregarlo en `angel1`:

     ```bash
     echo "CLAUDE_USAGE_REPORT_TOKEN=<el-token-generado>" >> /opt/lab-dashboard/backend/.env
     sudo systemctl restart lab-dashboard.service
     ```

2. Probar una ejecución manual:

   ```powershell
   .\report_claude_usage.ps1
   ```

   Revisar `report.log` en esta misma carpeta para confirmar `Reporte enviado
   correctamente`. En el dashboard, la vista Métricas debe mostrar el badge
   `pc-angel · actualizado <fecha>` junto al título.

3. Registrar la tarea programada (cada 6 horas, se puede ajustar):

   ```powershell
   $action = New-ScheduledTaskAction -Execute "powershell.exe" `
     -Argument '-NoProfile -ExecutionPolicy Bypass -File "C:\Users\je416\Desktop\proyecto personal\lab-dashboard\system\claude-usage-reporter\report_claude_usage.ps1"'
   $trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Hours 6) -RepetitionDuration ([TimeSpan]::MaxValue)
   $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopOnIdleEnd
   Register-ScheduledTask -TaskName "ClaudeUsageReporter" -Action $action -Trigger $trigger -Settings $settings -Description "Envia metricas de Claude Code (ccusage) a Lab Dashboard cada 6h"
   ```

   Verificar que quedó registrada:

   ```powershell
   Get-ScheduledTask -TaskName "ClaudeUsageReporter"
   ```

## Operación

- **Reintentos**: si el POST falla (servidor caído, sin red), reintenta 3
  veces con backoff (5s, 15s, 45s) y luego se rinde hasta la próxima corrida
  programada — no acumula procesos ni reintentos infinitos.
- **Consumo**: el script corre, hace su trabajo (unos segundos) y termina.
  No queda nada residente entre ejecuciones.
- **Logs**: `report.log` en esta carpeta, recortado automáticamente a las
  últimas ~500 líneas.
- **Ampliar a otra métrica o máquina**: el payload (`reports: {periodo: {...}}`)
  es genérico — cualquier otra fuente de datos puede empujar bajo una nueva
  clave sin tocar el backend. Para sumar otra máquina como cliente, copiar
  esta carpeta, darle un `source` distinto en su `config.json`, y usar el
  mismo (o un nuevo) token.

## Desregistrar

```powershell
Unregister-ScheduledTask -TaskName "ClaudeUsageReporter" -Confirm:$false
```
