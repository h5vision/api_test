param(
    [string]$ProjectName = "vision",
    [string]$MetricsUrl = "http://127.0.0.1:4173/admin-api/runtime-metrics",
    [int]$MinApi = 1,
    [int]$MaxApi = 6,
    [int]$MinWorker = 1,
    [int]$MaxWorker = 4,
    [int]$RequestsPerMinutePerApi = 120,
    [int]$ActiveRequestsPerApi = 20,
    [double]$CpuScaleUpPercent = 70,
    [double]$MemoryScaleUpPercent = 80,
    [int]$QueuedJobsPerWorker = 2,
    [int]$CooldownSeconds = 90,
    [int]$ScaleDownSamples = 5,
    [int]$PollSeconds = 30,
    [switch]$Once,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$scriptRoot = Split-Path -Parent $PSScriptRoot
$composePath = Join-Path $scriptRoot "compose.yaml"
$envPath = Join-Path $scriptRoot ".env"
$lastScaleAt = [datetime]::MinValue
$lowLoadSamples = 0
$createdMutex = $false
$autoscalerMutex = [Threading.Mutex]::new(
    $true,
    "Global\VisionComposeAutoscaler",
    [ref]$createdMutex
)
if (-not $createdMutex) {
    Write-Host "Vision Compose autoscaler is already running."
    $autoscalerMutex.Dispose()
    exit 0
}

function Import-ContainerEnvironment(
    [string]$ContainerName,
    [string]$ContainerVariable,
    [string]$ProcessVariable
) {
    if (-not [string]::IsNullOrWhiteSpace(
        [Environment]::GetEnvironmentVariable($ProcessVariable, "Process")
    )) {
        return
    }
    $raw = docker inspect $ContainerName --format "{{json .Config.Env}}" 2>$null
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($raw)) {
        return
    }
    $values = $raw | ConvertFrom-Json
    $prefix = "$ContainerVariable="
    $match = $values | Where-Object { $_.StartsWith($prefix) } | Select-Object -First 1
    if ($match) {
        [Environment]::SetEnvironmentVariable(
            $ProcessVariable,
            $match.Substring($prefix.Length),
            "Process"
        )
    }
}

Import-ContainerEnvironment "vision-api-1" "QDRANT_API_KEY" "QDRANT_API_KEY"
Import-ContainerEnvironment "vision-api-1" "REDIS_PASSWORD" "REDIS_PASSWORD"

function Convert-Percent([string]$Value) {
    if ([string]::IsNullOrWhiteSpace($Value)) { return 0.0 }
    return [double]($Value.Trim().TrimEnd("%"))
}

function Get-ContainerStats([string]$Service) {
    $prefix = "$ProjectName-$Service-"
    $rows = @()
    $lines = docker stats --no-stream --format "{{json .}}"
    foreach ($line in $lines) {
        if ([string]::IsNullOrWhiteSpace($line)) { continue }
        $item = $line | ConvertFrom-Json
        if ($item.Name -like "$prefix*") {
            $rows += [pscustomobject]@{
                Name = $item.Name
                Cpu = Convert-Percent $item.CPUPerc
                Memory = Convert-Percent $item.MemPerc
            }
        }
    }
    return @($rows)
}

function Invoke-ComposeScale([int]$ApiCount, [int]$WorkerCount) {
    $arguments = @(
        "compose",
        "--project-name", $ProjectName,
        "--file", $composePath
    )
    if (Test-Path -LiteralPath $envPath) {
        $arguments += @("--env-file", $envPath)
    }
    $arguments += @(
        "up", "-d", "--no-deps",
        "--scale", "api=$ApiCount",
        "--scale", "worker=$WorkerCount",
        "api", "worker"
    )
    if ($DryRun) {
        Write-Host "[dry-run] docker $($arguments -join ' ')"
        return
    }
    & docker @arguments
    if ($LASTEXITCODE -ne 0) {
        throw "docker compose scale failed with exit code $LASTEXITCODE"
    }
}

try {
    do {
        try {
        $metrics = Invoke-RestMethod -Uri $MetricsUrl -TimeoutSec 10
        $apiStats = @(Get-ContainerStats "api")
        $workerStats = @(Get-ContainerStats "worker")
        $apiCount = [Math]::Max(1, $apiStats.Count)
        $workerCount = $workerStats.Count
        $averageCpu = if ($apiStats.Count) {
            ($apiStats | Measure-Object -Property Cpu -Average).Average
        } else { 0 }
        $averageMemory = if ($apiStats.Count) {
            ($apiStats | Measure-Object -Property Memory -Average).Average
        } else { 0 }
        $requestsPerMinute = [Math]::Max(
            [int]$metrics.requests_current_minute,
            [int]$metrics.requests_previous_minute
        )
        $activeRequests = [int]$metrics.active_requests
        $queuedJobs = [int]$metrics.queue_depth
        $processingJobs = [int]$metrics.processing_tasks

        $desiredApi = $apiCount
        $apiOverloaded = (
            $requestsPerMinute -gt ($RequestsPerMinutePerApi * $apiCount) -or
            $activeRequests -gt ($ActiveRequestsPerApi * $apiCount) -or
            $averageCpu -ge $CpuScaleUpPercent -or
            $averageMemory -ge $MemoryScaleUpPercent
        )
        $apiUnderloaded = (
            $requestsPerMinute -lt (($RequestsPerMinutePerApi * $apiCount) / 3) -and
            $activeRequests -lt [Math]::Max(1, (($ActiveRequestsPerApi * $apiCount) / 3)) -and
            $averageCpu -lt ($CpuScaleUpPercent / 2) -and
            $averageMemory -lt ($MemoryScaleUpPercent / 2)
        )

        if ($apiOverloaded) {
            $desiredApi = [Math]::Min($MaxApi, $apiCount + 1)
            $lowLoadSamples = 0
        } elseif ($apiUnderloaded) {
            $lowLoadSamples += 1
            if ($lowLoadSamples -ge $ScaleDownSamples) {
                $desiredApi = [Math]::Max($MinApi, $apiCount - 1)
                $lowLoadSamples = 0
            }
        } else {
            $lowLoadSamples = 0
        }

        $jobDemand = $queuedJobs + $processingJobs
        $desiredWorker = if ($jobDemand -le 0) {
            $MinWorker
        } else {
            [Math]::Ceiling($jobDemand / [double]$QueuedJobsPerWorker)
        }
        $desiredWorker = [Math]::Max(
            $MinWorker,
            [Math]::Min($MaxWorker, [int]$desiredWorker)
        )

        $cooldownReady = (
            ([datetime]::UtcNow - $lastScaleAt).TotalSeconds -ge $CooldownSeconds
        )
        $statusLine = (
            "[{0:u}] api={1}->{2} worker={3}->{4} rpm={5} active={6} " +
            "cpu={7:N1}% mem={8:N1}% queued={9} processing={10}"
        ) -f (
            [datetime]::UtcNow,
            $apiCount,
            $desiredApi,
            $workerCount,
            $desiredWorker,
            $requestsPerMinute,
            $activeRequests,
            $averageCpu,
            $averageMemory,
            $queuedJobs,
            $processingJobs
        )
        Write-Host $statusLine

        if (
            $cooldownReady -and
            ($desiredApi -ne $apiCount -or $desiredWorker -ne $workerCount)
        ) {
            Invoke-ComposeScale $desiredApi $desiredWorker
            $lastScaleAt = [datetime]::UtcNow
        }
        } catch {
            Write-Warning "autoscaler sample failed: $($_.Exception.Message)"
        }

        if (-not $Once) {
            Start-Sleep -Seconds $PollSeconds
        }
    } while (-not $Once)
} finally {
    if ($createdMutex) {
        $autoscalerMutex.ReleaseMutex()
    }
    $autoscalerMutex.Dispose()
}
