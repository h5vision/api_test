$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$pidPath = Join-Path $projectRoot "data\autoscaler.pid"

if (-not (Test-Path -LiteralPath $pidPath)) {
    Write-Host "Vision autoscaler PID file was not found."
    exit 0
}

$autoscalerPid = 0
[void][int]::TryParse(
    (Get-Content -LiteralPath $pidPath -Raw).Trim(),
    [ref]$autoscalerPid
)
if ($autoscalerPid -gt 0) {
    $process = Get-Process -Id $autoscalerPid -ErrorAction SilentlyContinue
    if ($process) {
        Stop-Process -Id $autoscalerPid
        $process.WaitForExit()
        Write-Host "Vision autoscaler stopped (PID $autoscalerPid)."
    }
}
Remove-Item -LiteralPath $pidPath -Force
