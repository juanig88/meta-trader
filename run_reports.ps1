# Reportes programados. Solo leen historial del broker y logs; nunca mandan ordenes ni tocan el bot.
#
#   Daily:  reporte del dia anterior (contra el broker) -> Telegram si TELEGRAM_NOTIFY_DAILY_REPORT=1
#   Weekly: calibracion backfill -> calibration_report -> propuesta Gemini -> Telegram si TELEGRAM_NOTIFY_NIGHTLY=1
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\run_reports.ps1 -Mode Daily
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\run_reports.ps1 -Mode Weekly
#
# Salida en logs\reports_<mode>_<fecha>.log. Las tareas de Windows: "MetaTrader Daily Report" / "MetaTrader Weekly Gemini".

param(
    [Parameter(Mandatory = $true)][ValidateSet("Daily", "Weekly")][string]$Mode
)

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root
$env:PYTHONIOENCODING = "utf-8"
$Py = (Get-Command py -ErrorAction SilentlyContinue).Source
if (-not $Py) { $Py = "py" }

New-Item -ItemType Directory -Force -Path (Join-Path $Root "logs") | Out-Null
$Log = Join-Path $Root ("logs\reports_{0}_{1}.log" -f $Mode.ToLower(), (Get-Date -Format "yyyy-MM-dd_HHmmss"))

function Run-Step {
    param([string]$Name, [string[]]$PyArgs)
    "=== $(Get-Date -Format s) $Name" | Out-File -FilePath $Log -Append -Encoding utf8
    & $Py @PyArgs *>&1 | Out-File -FilePath $Log -Append -Encoding utf8
    $code = $LASTEXITCODE
    "=== exit $code" | Out-File -FilePath $Log -Append -Encoding utf8
    return $code
}

$failed = 0
if ($Mode -eq "Daily") {
    $day = (Get-Date).AddDays(-1).ToString("yyyy-MM-dd")
    if ((Run-Step "daily_report $day" @("-m", "trading.application.daily_report", "--day", $day)) -ne 0) { $failed++ }
} else {
    # daily_latest.json ya lo refresca la tarea diaria; aca solo se recalcula la calibracion.
    if ((Run-Step "calibration_backfill" @("-m", "trading.application.calibration_backfill")) -ne 0) { $failed++ }
    if ((Run-Step "calibration_report" @("-m", "trading.application.calibration_report")) -ne 0) { $failed++ }
    if ((Run-Step "nightly_review" @("-m", "trading.application.nightly_review")) -ne 0) { $failed++ }
}
"=== done failed_steps=$failed" | Out-File -FilePath $Log -Append -Encoding utf8
exit $failed
