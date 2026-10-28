# Levanta el stack local del bot, o lo baja.
#
# Motor vivo (desde 2026-10-07): swing diario Donchian 20/10 + SMA200, demo XBTFX.
# Runbook: trading/docs/SWING_ENGINE.md
# La rotacion (bot_mt5.py) esta apagada y no se lanza aca (kill_switch.halt rotacion_reemplazada_por_swing).
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\start_all.ps1
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\start_all.ps1 -Stop
#
# Cada proceso abre su propia ventana. MetaTrader 5 tiene que estar abierto.
# No usa Docker: el swing no necesita Postgres ni Redis. Tick daemon, alpha engine y
# manual miner (ayudantes de la rotacion) si lo necesitan y no se lanzan aca; para
# usarlos: docker compose --env-file .env -f infra/docker-compose.yml up -d

param(
    [switch]$Stop
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

# Nombre, patron en la linea de comando, argumentos de py.
$Processes = @(
    @{ Name = "swing";     Pattern = "swing_daily\.py";     PyArgs = "-u .\swing_daily.py" },
    @{ Name = "dashboard"; Pattern = "flask_dashboard\.py"; PyArgs = "-u .\flask_dashboard.py" }
)

function Get-StackProcesses {
    Get-CimInstance Win32_Process | Where-Object {
        $cmd = $_.CommandLine
        if (-not $cmd) { return $false }
        foreach ($proc in $Processes) {
            if ($cmd -match $proc.Pattern) { return $true }
        }
        return $false
    }
}

function Stop-Stack {
    $running = @(Get-StackProcesses)
    $ids = @{}
    foreach ($proc in $running) { $ids[$proc.ProcessId] = $true }
    $roots = @($running | Where-Object { -not $ids.ContainsKey($_.ParentProcessId) })
    foreach ($proc in $roots) {
        & taskkill.exe /PID $proc.ProcessId /T /F | Out-Null
        Write-Host ("detenido PID {0}" -f $proc.ProcessId)
    }
    if (-not $roots) {
        Write-Host "no habia procesos del stack"
    }
}

function Test-AlreadyRunning {
    param([string]$Pattern)
    $hit = Get-CimInstance Win32_Process | Where-Object {
        $_.CommandLine -and $_.CommandLine -match $Pattern
    } | Select-Object -First 1
    return $null -ne $hit
}

function Start-PyWindow {
    param([string]$Name, [string]$PyArgs)
    $command = "Set-Location -LiteralPath '$Root'; `$host.UI.RawUI.WindowTitle = '$Name'; py $PyArgs"
    Start-Process -FilePath "powershell.exe" -WorkingDirectory $Root -ArgumentList @(
        "-NoExit", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", $command
    ) | Out-Null
    Write-Host "arranque: $Name"
}

if ($Stop) {
    Stop-Stack
    exit 0
}

if (-not (Test-Path -LiteralPath (Join-Path $Root ".env"))) {
    Write-Error "Falta .env en $Root"
    exit 1
}

if (-not (Get-Process -Name "terminal64" -ErrorAction SilentlyContinue)) {
    Write-Host "aviso: MetaTrader 5 no esta abierto; abrilo y verifica que este logueado en la demo XBTFX"
}

foreach ($proc in $Processes) {
    if (Test-AlreadyRunning -Pattern $proc.Pattern) {
        Write-Host ("ya corre: {0}" -f $proc.Name)
        continue
    }
    Start-PyWindow -Name $proc.Name -PyArgs $proc.PyArgs
}

Write-Host "dashboard: http://127.0.0.1:8000"
Write-Host "para bajar todo: powershell -NoProfile -ExecutionPolicy Bypass -File .\start_all.ps1 -Stop"
