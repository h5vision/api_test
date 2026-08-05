#requires -Version 5.1

<#
.SYNOPSIS
Safely verifies the Public GitHub Commit Snapshot MVP end to end.

.DESCRIPTION
Runs compile/tests, optionally applies the additive PostgreSQL migration,
verifies feature OFF and ON through the real Traefik endpoint, validates
authentication and the API smoke flow, and restores feature OFF on failure.

The script invokes .venv\Scripts\python.exe directly. PowerShell activation is
not required.
#>

[CmdletBinding()]
param(
    [string]$ProjectRoot = (Split-Path -Parent $PSScriptRoot),
    [string]$ApiBaseUrl = "",
    [string]$PostgresService = "postgres",
    [switch]$ApplyMigration,
    [switch]$RunGitHubIntegrationTests,
    [switch]$RunApiSmokeTest,
    [switch]$LeaveSnapshotEnabled,
    [string]$SnapshotToken = "",
    [ValidateRange(0, 64)]
    [int]$ApiReplicas = 0
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ComposeBase = Join-Path $ProjectRoot "compose.yaml"
$ComposeOverride = Join-Path $ProjectRoot "compose.snapshot-mvp.yaml"
$MigrationFile = Join-Path $ProjectRoot "backend\migrations\versions\0001_github_snapshot_mvp.sql"
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$OriginalLocation = Get-Location
$ManagedEnvironment = @(
    "SNAPSHOT_CONTROL_PLANE_ENABLED",
    "SNAPSHOT_MVP_TOKEN",
    "SNAPSHOT_API_BASE_URL",
    "GITHUB_INTEGRATION_TESTS"
)
$OriginalEnvironment = @{}
$RuntimeChanged = $false
$Succeeded = $false

foreach ($name in $ManagedEnvironment) {
    $item = Get-Item -LiteralPath "Env:$name" -ErrorAction SilentlyContinue
    $OriginalEnvironment[$name] = if ($null -eq $item) {
        @{ Exists = $false; Value = $null }
    }
    else {
        @{ Exists = $true; Value = $item.Value }
    }
}

function Write-Section {
    param([Parameter(Mandatory = $true)][string]$Title)
    Write-Host ""
    Write-Host ("=" * 68) -ForegroundColor Cyan
    Write-Host $Title -ForegroundColor Cyan
    Write-Host ("=" * 68) -ForegroundColor Cyan
}

function Assert-NativeSuccess {
    param([Parameter(Mandatory = $true)][string]$Message)
    if ($LASTEXITCODE -ne 0) {
        throw "$Message (exit code: $LASTEXITCODE)"
    }
}

function Assert-File {
    param([Parameter(Mandatory = $true)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Required file is missing: $Path"
    }
}

function Get-OpenApiPaths {
    param([Parameter(Mandatory = $true)][string]$BaseUrl)
    $url = "$($BaseUrl.TrimEnd('/'))/openapi.json"
    $document = Invoke-RestMethod -Method Get -Uri $url -TimeoutSec 10
    return @($document.paths.PSObject.Properties.Name)
}

function Wait-ApiReady {
    param(
        [Parameter(Mandatory = $true)][string]$BaseUrl,
        [int]$TimeoutSeconds = 120
    )
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        try {
            $null = Get-OpenApiPaths -BaseUrl $BaseUrl
            return
        }
        catch {
            Start-Sleep -Seconds 2
        }
    } while ((Get-Date) -lt $deadline)
    throw "API did not become ready within $TimeoutSeconds seconds: $BaseUrl"
}

function Assert-SnapshotRouteState {
    param(
        [Parameter(Mandatory = $true)][string]$BaseUrl,
        [Parameter(Mandatory = $true)][bool]$ExpectedEnabled,
        [Parameter(Mandatory = $true)][int]$ReplicaCount
    )
    $required = @(
        "/v1/snapshot-control/repositories",
        "/v1/snapshot-control/repositories/{repository_id}/snapshots",
        "/v1/snapshot-control/snapshots/{snapshot_id}/resolve",
        "/v1/snapshot-control/snapshots/{snapshot_id}/tree",
        "/v1/snapshot-control/snapshots/{snapshot_id}/file"
    )
    $attempts = [Math]::Max(3, $ReplicaCount * 3)
    for ($attempt = 1; $attempt -le $attempts; $attempt++) {
        $paths = Get-OpenApiPaths -BaseUrl $BaseUrl
        $present = @($required | Where-Object { $paths -contains $_ })
        if ($ExpectedEnabled -and $present.Count -ne $required.Count) {
            throw "Snapshot routes are incomplete on attempt ${attempt}: $($present -join ', ')"
        }
        if ((-not $ExpectedEnabled) -and $present.Count -ne 0) {
            throw "Snapshot routes remain enabled on attempt ${attempt}: $($present -join ', ')"
        }
    }
}

function Get-HttpStatus {
    param(
        [Parameter(Mandatory = $true)][string]$Uri,
        [hashtable]$Headers = @{}
    )
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Method Get -Uri $Uri `
            -Headers $Headers -TimeoutSec 20
        return [int]$response.StatusCode
    }
    catch {
        $response = $_.Exception.Response
        if ($null -ne $response) {
            return [int]$response.StatusCode
        }
        throw
    }
}

function Assert-AuthenticationContract {
    param(
        [Parameter(Mandatory = $true)][string]$BaseUrl,
        [Parameter(Mandatory = $true)][string]$Token
    )
    # Use an existing GET route shape. Calling GET on the POST-only collection
    # route returns 405 before router dependencies can validate authentication.
    $uri = "$($BaseUrl.TrimEnd('/'))/v1/snapshot-control/repositories/auth-probe"
    $missing = Get-HttpStatus -Uri $uri
    if ($missing -ne 401) {
        throw "Missing Snapshot token must return HTTP 401, received $missing"
    }
    $wrong = Get-HttpStatus -Uri $uri -Headers @{
        "X-Vision-Snapshot-Token" = ("wrong-" + ("x" * 32))
    }
    if ($wrong -ne 403) {
        throw "Wrong Snapshot token must return HTTP 403, received $wrong"
    }
}

function Get-ApiReplicaCount {
    $ids = @(& docker compose -f $ComposeBase ps -q api)
    Assert-NativeSuccess "Could not inspect the API replica count"
    $count = @($ids | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }).Count
    if ($count -lt 1) { return 1 }
    return $count
}

function Set-SnapshotRuntime {
    param(
        [Parameter(Mandatory = $true)][bool]$Enabled,
        [Parameter(Mandatory = $true)][int]$ReplicaCount,
        [Parameter(Mandatory = $true)][bool]$UseOverride
    )
    $env:SNAPSHOT_CONTROL_PLANE_ENABLED = if ($Enabled) { "true" } else { "false" }
    $files = @("-f", $ComposeBase)
    if ($UseOverride) {
        $files += @("-f", $ComposeOverride)
    }
    & docker compose @files up -d --no-deps --force-recreate `
        --scale "api=$ReplicaCount" api
    Assert-NativeSuccess "Could not recreate the API service"
    $script:RuntimeChanged = $true
    Wait-ApiReady -BaseUrl $ApiBaseUrl
}

function Get-SnapshotTableCount {
    $query = @"
SELECT count(*)
FROM pg_catalog.pg_tables
WHERE schemaname = 'public'
  AND tablename IN (
    'snapshot_mvp_repositories',
    'snapshot_mvp_snapshots',
    'snapshot_mvp_locators'
  );
"@
    $output = @(& docker compose -f $ComposeBase exec -T $PostgresService `
        psql -U vision -d vision -At -v ON_ERROR_STOP=1 -c $query)
    Assert-NativeSuccess "Could not verify Snapshot migration tables"
    $numeric = @($output | ForEach-Object { $_.Trim() } | Where-Object { $_ -match '^\d+$' })
    if ($numeric.Count -eq 0) {
        throw "Snapshot migration table count was not returned"
    }
    return [int]$numeric[-1]
}

function Restore-Environment {
    foreach ($name in $ManagedEnvironment) {
        $entry = $OriginalEnvironment[$name]
        if ($entry.Exists) {
            Set-Item -LiteralPath "Env:$name" -Value $entry.Value
        }
        else {
            Remove-Item -LiteralPath "Env:$name" -ErrorAction SilentlyContinue
        }
    }
}

try {
    Set-Location -LiteralPath $ProjectRoot
    foreach ($file in @($ComposeBase, $ComposeOverride, $MigrationFile, $Python)) {
        Assert-File -Path $file
    }

    if ([string]::IsNullOrWhiteSpace($ApiBaseUrl)) {
        $bindIp = if ([string]::IsNullOrWhiteSpace($env:TRAEFIK_BIND_IP)) {
            "192.168.0.7"
        }
        else {
            $env:TRAEFIK_BIND_IP.Trim()
        }
        $ApiBaseUrl = "http://${bindIp}:8000"
    }
    $ApiBaseUrl = $ApiBaseUrl.TrimEnd('/')

    if ($LeaveSnapshotEnabled -and [string]::IsNullOrWhiteSpace($SnapshotToken)) {
        throw "-LeaveSnapshotEnabled requires a persistent -SnapshotToken"
    }
    if ([string]::IsNullOrWhiteSpace($SnapshotToken)) {
        $bytes = New-Object byte[] 32
        [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
        $SnapshotToken = [Convert]::ToBase64String($bytes)
    }
    if ([Text.Encoding]::UTF8.GetByteCount($SnapshotToken) -lt 32) {
        throw "SnapshotToken must contain at least 32 UTF-8 bytes"
    }
    $env:SNAPSHOT_MVP_TOKEN = $SnapshotToken
    $env:SNAPSHOT_API_BASE_URL = $ApiBaseUrl

    Write-Section "1. Static verification"
    & $Python -m compileall -q backend tests verify_github_snapshot_mvp.py `
        verify_github_snapshot_api.py
    Assert-NativeSuccess "Python compile verification failed"
    & $Python -m pytest tests/test_github_snapshot_mvp.py -q
    Assert-NativeSuccess "Snapshot MVP tests failed"
    $env:GITHUB_INTEGRATION_TESTS = if ($RunGitHubIntegrationTests) { "1" } else { "0" }
    & $Python verify_github_snapshot_mvp.py
    Assert-NativeSuccess "Snapshot static/integration verification failed"
    # The dedicated verifier already covered the optional live GitHub call.
    # Keep the later full regression deterministic and avoid duplicate calls.
    $env:GITHUB_INTEGRATION_TESTS = "0"
    & docker compose -f $ComposeBase -f $ComposeOverride config --quiet
    Assert-NativeSuccess "Merged Compose configuration is invalid"

    if ($ApplyMigration) {
        Write-Section "2. PostgreSQL migration"
        $sql = Get-Content -LiteralPath $MigrationFile -Raw
        $sql | & docker compose -f $ComposeBase exec -T $PostgresService `
            psql -U vision -d vision -v ON_ERROR_STOP=1
        Assert-NativeSuccess "Snapshot migration failed"
    }
    $tableCount = Get-SnapshotTableCount
    if ($tableCount -ne 3) {
        throw "Expected 3 Snapshot tables, found $tableCount. Run with -ApplyMigration."
    }

    if ($ApiReplicas -eq 0) {
        $ApiReplicas = Get-ApiReplicaCount
    }
    Write-Host "API endpoint: $ApiBaseUrl"
    Write-Host "API replicas preserved: $ApiReplicas"
    Write-Host "Snapshot token: configured (value hidden)"

    Write-Section "3. Feature OFF contract"
    Set-SnapshotRuntime -Enabled $false -ReplicaCount $ApiReplicas -UseOverride $true
    Assert-SnapshotRouteState -BaseUrl $ApiBaseUrl -ExpectedEnabled $false `
        -ReplicaCount $ApiReplicas

    Write-Section "4. Feature ON, authentication, and smoke"
    Set-SnapshotRuntime -Enabled $true -ReplicaCount $ApiReplicas -UseOverride $true
    Assert-SnapshotRouteState -BaseUrl $ApiBaseUrl -ExpectedEnabled $true `
        -ReplicaCount $ApiReplicas
    Assert-AuthenticationContract -BaseUrl $ApiBaseUrl -Token $SnapshotToken
    if ($RunApiSmokeTest) {
        & $Python verify_github_snapshot_api.py
        Assert-NativeSuccess "Snapshot API smoke verification failed"
    }

    Write-Section "5. Full regression"
    & $Python -m pytest -q
    Assert-NativeSuccess "Full regression test failed"
    $Succeeded = $true
}
finally {
    $keepEnabled = $Succeeded -and $LeaveSnapshotEnabled
    if ($RuntimeChanged -and (-not $keepEnabled)) {
        Write-Section "Cleanup: restore production Feature OFF"
        try {
            # Remove the temporary verification token before recreating the
            # production container. Values from the caller/.env are preserved.
            Restore-Environment
            $env:SNAPSHOT_CONTROL_PLANE_ENABLED = "false"
            & docker compose -f $ComposeBase up -d --no-deps --force-recreate `
                --scale "api=$ApiReplicas" api
            Assert-NativeSuccess "Could not restore the API service"
            Wait-ApiReady -BaseUrl $ApiBaseUrl
            Assert-SnapshotRouteState -BaseUrl $ApiBaseUrl -ExpectedEnabled $false `
                -ReplicaCount $ApiReplicas
        }
        catch {
            # Environment and working-directory restoration must still run even
            # if Docker cleanup itself fails.
            Write-Warning "Snapshot cleanup failed: $($_.Exception.Message)"
        }
    }
    Restore-Environment
    Set-Location -LiteralPath $OriginalLocation
}

if ($Succeeded) {
    Write-Host "Snapshot MVP verification passed." -ForegroundColor Green
    if ($LeaveSnapshotEnabled) {
        Write-Host "Feature remains enabled with the caller-supplied persistent token."
    }
    else {
        Write-Host "Feature was restored to OFF. Migration tables remain installed."
    }
}
