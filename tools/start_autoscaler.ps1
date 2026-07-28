param(
    [switch]$Restart
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$runtimeDirectory = Join-Path $projectRoot "data"
$pidPath = Join-Path $runtimeDirectory "autoscaler.pid"
$stdoutPath = Join-Path $runtimeDirectory "autoscaler.log"
$stderrPath = Join-Path $runtimeDirectory "autoscaler.error.log"
$autoscalerPath = Join-Path $PSScriptRoot "autoscale_compose.ps1"

New-Item -ItemType Directory -Path $runtimeDirectory -Force | Out-Null

if (Test-Path -LiteralPath $pidPath) {
    $existingPid = 0
    [void][int]::TryParse(
        (Get-Content -LiteralPath $pidPath -Raw).Trim(),
        [ref]$existingPid
    )
    $existing = if ($existingPid -gt 0) {
        Get-Process -Id $existingPid -ErrorAction SilentlyContinue
    } else {
        $null
    }
    if ($existing -and -not $Restart) {
        Write-Host "Vision autoscaler is already running (PID $existingPid)."
        exit 0
    }
    if ($existing -and $Restart) {
        Stop-Process -Id $existingPid
        $existing.WaitForExit()
    }
    Remove-Item -LiteralPath $pidPath -Force -ErrorAction SilentlyContinue
}

$arguments = @(
    "-NoProfile",
    "-ExecutionPolicy", "Bypass",
    "-File", "`"$autoscalerPath`""
)
$process = Start-Process `
    -FilePath "powershell.exe" `
    -ArgumentList $arguments `
    -WindowStyle Hidden `
    -RedirectStandardOutput $stdoutPath `
    -RedirectStandardError $stderrPath `
    -PassThru
Set-Content -LiteralPath $pidPath -Value $process.Id -Encoding ascii
Write-Host "Vision autoscaler started (PID $($process.Id))."
Write-Host "Log: $stdoutPath"
