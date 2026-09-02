# Claude Usage Reporter — corre en la PC principal (donde Claude Code realmente
# se usa) y empuja el reporte de ccusage al backend de Lab Dashboard en angel1.
#
# Por que push y no que el servidor jale: ccusage solo puede leer logs de
# sesion en la maquina donde corrieron esas sesiones (~/.claude/projects). El
# servidor angel1 nunca tuvo esos logs, asi que pedirle que ejecute ccusage
# el mismo siempre devolvia vacio. Este script corre donde SI estan los logs
# y manda el resultado ya calculado.
#
# Disenado para Task Scheduler: se ejecuta, hace su trabajo, termina. No deja
# nada corriendo en segundo plano entre ejecuciones (cero consumo de CPU/RAM
# fuera de la ventana de ejecucion, que dura pocos segundos).
#
# Uso:
#   1. Copiar config.example.json a config.json en esta misma carpeta.
#   2. Poner ahi la URL del servidor y el mismo token que CLAUDE_USAGE_REPORT_TOKEN
#      en el .env del backend (angel1).
#   3. Registrar la tarea programada (ver README.md).

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$configPath = Join-Path $scriptDir "config.json"

if (-not (Test-Path $configPath)) {
    Write-Error "Falta config.json. Copia config.example.json a config.json y completa server_url/token."
    exit 1
}

$config = Get-Content $configPath -Raw | ConvertFrom-Json
$logFile = Join-Path $scriptDir $config.log_file
$maxBytes = if ($config.log_max_bytes) { $config.log_max_bytes } else { 2097152 }

function Write-Log {
    param([string]$Level, [string]$Message)
    $line = "{0}`t{1}`t{2}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Level, $Message
    Add-Content -Path $logFile -Value $line -Encoding utf8
    if ((Test-Path $logFile) -and (Get-Item $logFile).Length -gt $maxBytes) {
        $tail = Get-Content $logFile -Tail 500
        Set-Content -Path $logFile -Value $tail -Encoding utf8
    }
}

function Get-CcusageReport {
    param([string]$Period)
    $pkg = if ($config.ccusage_package) { $config.ccusage_package } else { "ccusage@20.0.18" }
    $timeoutSec = if ($config.ccusage_timeout_sec) { $config.ccusage_timeout_sec } else { 90 }

    # npx no tenia ningun timeout: si se cuelga (registry npm lento/no
    # alcanzable, resolucion de version, lock de cache), el proceso quedaba
    # vivo indefinidamente. Con ExecutionTimeLimit=72h en la tarea programada
    # y MultipleInstances=IgnoreNew, un solo colgue bloqueaba en silencio
    # TODAS las corridas siguientes durante dias (confirmado en report.log:
    # "Iniciando recoleccion" sin ninguna linea despues, y
    # NumberOfMissedRuns > 0 en Get-ScheduledTaskInfo). Correr en un Job y
    # matarlo si excede $timeoutSec convierte ese cuelgue silencioso en un
    # error logueado normal, que no bloquea las siguientes ejecuciones.
    $job = Start-Job -ScriptBlock {
        param($pkg, $Period)
        $out = & npx $pkg claude $Period --json 2>$null
        [PSCustomObject]@{ Output = ($out -join "`n"); ExitCode = $LASTEXITCODE }
    } -ArgumentList $pkg, $Period

    try {
        if (-not (Wait-Job -Job $job -Timeout $timeoutSec)) {
            throw "ccusage colgado para el periodo '$Period' (timeout ${timeoutSec}s) -- npx no respondio a tiempo"
        }
        $result = Receive-Job -Job $job
        if ($result.ExitCode -ne 0 -or -not $result.Output) {
            throw "ccusage fallo para el periodo '$Period' (exit=$($result.ExitCode))"
        }
        return ($result.Output | ConvertFrom-Json)
    } finally {
        Stop-Job -Job $job -ErrorAction SilentlyContinue | Out-Null
        Remove-Job -Job $job -Force -ErrorAction SilentlyContinue
    }
}

function Send-Report {
    param($Payload)
    $body = $Payload | ConvertTo-Json -Depth 20 -Compress
    $headers = @{ "X-Claude-Usage-Token" = $config.token; "Content-Type" = "application/json" }
    $uri = "$($config.server_url)/api/claude-usage/report"

    $maxAttempts = 3
    $delays = @(5, 15, 45)
    for ($attempt = 1; $attempt -le $maxAttempts; $attempt++) {
        try {
            Invoke-RestMethod -Uri $uri -Method Post -Headers $headers -Body $body -TimeoutSec 20 | Out-Null
            Write-Log "INFO" "Reporte enviado correctamente (intento $attempt)"
            return $true
        } catch {
            Write-Log "WARN" "Intento $attempt fallo: $($_.Exception.Message)"
            if ($attempt -lt $maxAttempts) {
                Start-Sleep -Seconds $delays[$attempt - 1]
            }
        }
    }
    return $false
}

try {
    Write-Log "INFO" "Iniciando recoleccion de metricas ccusage"
    $reports = @{}
    foreach ($period in @("daily", "monthly", "session")) {
        $reports[$period] = Get-CcusageReport -Period $period
    }

    $payload = @{
        source       = $config.source
        generated_at = (Get-Date).ToString("o")
        reports      = $reports
    }

    $ok = Send-Report -Payload $payload
    if (-not $ok) {
        Write-Log "ERROR" "No se pudo enviar el reporte tras varios intentos"
        exit 1
    }
    exit 0
} catch {
    Write-Log "ERROR" "Fallo la recoleccion: $($_.Exception.Message)"
    exit 1
}
