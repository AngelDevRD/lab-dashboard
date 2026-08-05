# Menu para conectar por SSH a cualquiera de los servidores del lab.
# Lee la lista desde backend/servers.json (misma fuente que usa el dashboard).
# Uso: doble clic, o desde PowerShell: .\system\ssh-menu.ps1

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$serversFile = Join-Path $repoRoot "backend\servers.json"

if (-not (Test-Path $serversFile)) {
    Write-Host "No se encontro $serversFile" -ForegroundColor Red
    exit 1
}

$config = Get-Content $serversFile -Raw | ConvertFrom-Json
$servers = $config.servers

Write-Host ""
Write-Host "=== Servidores del lab ===" -ForegroundColor Cyan
for ($i = 0; $i -lt $servers.Count; $i++) {
    $s = $servers[$i]
    Write-Host ("  [{0}] {1,-12} {2}@{3}:{4}" -f ($i + 1), $s.name, $s.ssh_user, $s.host, $s.ssh_port)
}
$allOption = $servers.Count + 1
Write-Host ("  [{0}] Abrir los {1} servidores (una ventana por cada uno)" -f $allOption, $servers.Count)
Write-Host ""

$choice = Read-Host "Elegi una opcion (numero)"
$index = 0
if (-not [int]::TryParse($choice, [ref]$index) -or $index -lt 1 -or $index -gt $allOption) {
    Write-Host "Opcion invalida." -ForegroundColor Red
    exit 1
}

if ($index -eq $allOption) {
    $wt = Get-Command wt -ErrorAction SilentlyContinue
    if (-not $wt) {
        # Fallback: sin Windows Terminal, una ventana de PowerShell por servidor.
        foreach ($s in $servers) {
            Write-Host "Abriendo $($s.name) ($($s.ssh_user)@$($s.host))..." -ForegroundColor Green
            Start-Process powershell -ArgumentList "-NoExit -Command ssh -p $($s.ssh_port) $($s.ssh_user)@$($s.host)"
        }
        exit 0
    }

    # Una sola ventana de Windows Terminal, un servidor por pestana.
    Write-Host "Abriendo Windows Terminal con los $($servers.Count) servidores, una pestana por servidor..." -ForegroundColor Green
    $wtArgs = @()
    foreach ($s in $servers) {
        if ($wtArgs.Count -gt 0) { $wtArgs += ";" }
        $cmd = "ssh -p $($s.ssh_port) $($s.ssh_user)@$($s.host)"
        $wtArgs += @("new-tab", "powershell", "-NoExit", "-Command", $cmd)
    }
    & wt @wtArgs
    exit 0
}

$target = $servers[$index - 1]
Write-Host "Conectando a $($target.name) ($($target.ssh_user)@$($target.host))..." -ForegroundColor Green

ssh -p $target.ssh_port "$($target.ssh_user)@$($target.host)"
