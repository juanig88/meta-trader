# Compact Docker Desktop's docker_data.vhdx without touching bot processes.
#
# Leaves bot_mt5.py, watcher_ladder.py, mt5_tick_daemon.py and
# trading.application.alpha_engine running. Those already reconnect when
# Postgres/Redis return. After Docker starts, this script only runs
# `docker compose up -d` so the data-layer containers come back.
#
# Requires an elevated PowerShell (diskpart). Docker will be down 5-15 min.
#
# Usage (from repo root, as Administrator):
#   powershell -ExecutionPolicy Bypass -File infra\compact_docker_vhdx.ps1

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$Vhdx = Join-Path $env:LOCALAPPDATA "Docker\wsl\disk\docker_data.vhdx"
$ComposeFile = Join-Path $PSScriptRoot "docker-compose.yml"
$EnvFile = Join-Path $RepoRoot ".env"

function Assert-Admin {
    $ok = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator
    )
    if (-not $ok) {
        throw "Run this script from an elevated PowerShell (diskpart needs admin)."
    }
}

function Get-VhdxGb([string]$path) {
    if (-not (Test-Path -LiteralPath $path)) { return $null }
    return [math]::Round((Get-Item -LiteralPath $path).Length / 1GB, 2)
}

function Invoke-GuestTrim {
    Write-Host "TRIM inside docker-desktop (marks unused VHDX blocks)..."
    wsl -d docker-desktop -- fstrim -v /mnt/docker-desktop-disk
    if ($LASTEXITCODE -ne 0) {
        throw "fstrim failed; compact would not reclaim space."
    }
}

function Wait-DockerEngine([int]$timeoutSec = 180) {
    $deadline = (Get-Date).AddSeconds($timeoutSec)
    while ((Get-Date) -lt $deadline) {
        try {
            docker info --format "{{.ServerVersion}}" 2>$null | Out-Null
            if ($LASTEXITCODE -eq 0) { return }
        } catch { }
        Start-Sleep -Seconds 3
    }
    throw "Docker engine did not come back within ${timeoutSec}s."
}

Assert-Admin
if (-not (Test-Path -LiteralPath $Vhdx)) {
    throw "VHDX not found: $Vhdx"
}

$before = Get-VhdxGb $Vhdx
Write-Host "VHDX before: $before GB  ($Vhdx)"
Write-Host "Not stopping Python ingest/alpha/bot processes."

Invoke-GuestTrim

Write-Host "Stopping Docker Desktop..."
docker desktop stop
if ($LASTEXITCODE -ne 0) {
    throw "docker desktop stop failed"
}

wsl --shutdown

$diskpartScript = @"
select vdisk file="$Vhdx"
attach vdisk readonly
compact vdisk
detach vdisk
"@
$dpFile = Join-Path $env:TEMP "meta-trader-compact-vhdx.txt"
Set-Content -LiteralPath $dpFile -Value $diskpartScript -Encoding ascii
Write-Host "Compacting VHDX (this can take several minutes)..."
diskpart /s $dpFile
if ($LASTEXITCODE -ne 0) {
    throw "diskpart compact failed"
}

$afterStop = Get-VhdxGb $Vhdx
Write-Host "VHDX after compact: $afterStop GB"

Write-Host "Starting Docker Desktop..."
docker desktop start
if ($LASTEXITCODE -ne 0) {
    throw "docker desktop start failed"
}
Wait-DockerEngine

if (Test-Path -LiteralPath $EnvFile) {
    Write-Host "Bringing meta-trader compose back (postgres + redis)..."
    docker compose --env-file $EnvFile -f $ComposeFile up -d
    if ($LASTEXITCODE -ne 0) {
        throw "docker compose up failed"
    }
} else {
    Write-Host "No .env at $EnvFile; skipped compose up. Start postgres/redis yourself."
}

$after = Get-VhdxGb $Vhdx
Write-Host "Done. VHDX $before GB -> $after GB. Tick daemon and alpha engine should resume writes on their own."
