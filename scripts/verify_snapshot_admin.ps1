param(
    [string]$ProjectRoot = "C:\Users\PC2412\Documents\HancomAI5\Vision",
    [string]$AdminBaseUrl = "http://127.0.0.1:4180",
    [string]$ApiBaseUrl = "http://192.168.0.7:8000",
    [switch]$RunTargetedTests,
    [switch]$RunFullRegression,
    [switch]$RunFrontendBuild,
    [switch]$RunSnapshotMvpRegression,
    [switch]$RestartServices,
    [switch]$RunApiSmoke
)


$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest


function Write-Step([string]$Message) {
    Write-Host "`n==> $Message" -ForegroundColor Cyan
}


function Assert-True([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
}


function Wait-Http200([string]$Uri, [int]$Attempts = 30) {
    for ($index = 1; $index -le $Attempts; $index++) {
        try {
            $response = Invoke-WebRequest -Uri $Uri -Method Get -UseBasicParsing -TimeoutSec 5
            if ($response.StatusCode -eq 200) { return }
        } catch {
            if ($index -eq $Attempts) { throw }
        }
        Start-Sleep -Seconds 2
    }
    throw "HTTP 200 대기 실패: $Uri"
}


function Get-HttpFailureStatus([scriptblock]$Action) {
    try {
        & $Action | Out-Null
        return 200
    } catch {
        $response = $_.Exception.Response
        if ($null -ne $response) {
            try { return [int]$response.StatusCode } catch { }
            try { return [int]$response.StatusCode.value__ } catch { }
        }
        throw
    }
}


$ProjectRoot = (Resolve-Path $ProjectRoot).Path
Set-Location $ProjectRoot


$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
Assert-True (Test-Path $Python) "Python 가상환경을 찾을 수 없습니다: $Python"


if (-not ($RunTargetedTests -or $RunFullRegression -or $RunFrontendBuild -or $RunSnapshotMvpRegression -or $RestartServices -or $RunApiSmoke)) {
    $RunTargetedTests = $true
    $RunFrontendBuild = $true
}


Write-Step "Backend Python 컴파일"
& $Python -m compileall backend
if ($LASTEXITCODE -ne 0) { throw "Backend compileall 실패" }


if ($RunTargetedTests) {
    Write-Step "Snapshot 관리자 Targeted 테스트"
    & $Python -m pytest `
        tests/test_admin_snapshot_api.py `
        tests/test_admin_snapshot_frontend_contract.py `
        tests/test_admin_snapshot_listing.py `
        tests/test_github_snapshot_mvp.py `
        -q
    if ($LASTEXITCODE -ne 0) { throw "Snapshot 관리자 Targeted 테스트 실패" }
}


if ($RunFullRegression) {
    Write-Step "전체 Backend 회귀 테스트"
    & $Python -m pytest -q
    if ($LASTEXITCODE -ne 0) { throw "전체 Backend 회귀 테스트 실패" }
}


if ($RunFrontendBuild) {
    Write-Step "Admin TypeScript strict + Vite build"
    & npm.cmd --prefix admin run build
    if ($LASTEXITCODE -ne 0) { throw "Admin Frontend build 실패" }


    $BundleText = Get-ChildItem (Join-Path $ProjectRoot "admin\dist\assets") -Filter "*.js" -ErrorAction SilentlyContinue |
        Get-Content -Raw -ErrorAction SilentlyContinue |
        Out-String
    Assert-True ($BundleText -notmatch "X-Vision-Snapshot-Token") "Frontend bundle에 Snapshot 인증 Header 문자열이 포함됐습니다."
    Assert-True ($BundleText -notmatch "SNAPSHOT_MVP_TOKEN") "Frontend bundle에 Snapshot Token 환경변수 문자열이 포함됐습니다."
}


Write-Step "Docker Compose 병합 검증"
docker compose config -q
if ($LASTEXITCODE -ne 0) { throw "docker compose config 실패" }


if ($RunSnapshotMvpRegression) {
    Write-Step "기존 Public GitHub Snapshot MVP 전체 검증"
    & (Join-Path $ProjectRoot "scripts\verify_snapshot_mvp.ps1") `
        -ApplyMigration `
        -RunGitHubIntegrationTests `
        -RunApiSmokeTest
    if ($LASTEXITCODE -ne 0) { throw "기존 Snapshot MVP 검증 실패" }
}


if ($RestartServices) {
    Write-Step "Admin 정적 번들 재생성"
    docker compose run --rm admin-build
    if ($LASTEXITCODE -ne 0) { throw "admin-build 컨테이너 실행 실패" }


    Write-Step "API와 Admin Web 재시작"
    docker compose up -d --force-recreate api admin-web
    if ($LASTEXITCODE -ne 0) { throw "API/Admin Web 재시작 실패" }


    Wait-Http200 "$ApiBaseUrl/v1/health"
    Wait-Http200 "$AdminBaseUrl/healthz"
}


if ($RunApiSmoke) {
    Write-Step "직접 Admin API 접근 차단 확인"
    $DirectStatus = Get-HttpFailureStatus {
        Invoke-WebRequest -Uri "$ApiBaseUrl/v1/admin/snapshots/status" -Method Get -UseBasicParsing -TimeoutSec 10
    }
    Assert-True ($DirectStatus -eq 403) "직접 Admin API 접근은 403이어야 합니다. 실제: $DirectStatus"


    Write-Step "Admin Proxy를 통한 Snapshot 상태 조회"
    $Status = Invoke-RestMethod -Uri "$AdminBaseUrl/admin-api/snapshots/status" -Method Get -TimeoutSec 20
    Assert-True ($Status.table_count -eq 3) "Snapshot DB 테이블은 3개여야 합니다."
    Assert-True ($Status.database_ready -eq $true) "Snapshot DB 상태가 READY가 아닙니다."
    Assert-True ($Status.counts.repositories -ge 0) "Repository count가 잘못됐습니다."
    Assert-True ($Status.counts.snapshots -ge 0) "Snapshot count가 잘못됐습니다."
    Assert-True ($Status.counts.locators -ge 0) "Locator count가 잘못됐습니다."


    $StatusJson = $Status | ConvertTo-Json -Depth 20
    Assert-True ($StatusJson -notmatch "X-Vision-Snapshot-Token") "상태 응답에 Snapshot 인증 Header가 노출됐습니다."
    Assert-True ($StatusJson -notmatch '"SNAPSHOT_MVP_TOKEN"') "상태 응답에 Snapshot Token 환경변수가 노출됐습니다."


    Write-Step "Repository와 Snapshot 목록 조회"
    $Overview = Invoke-RestMethod -Uri "$AdminBaseUrl/admin-api/snapshots?page=1&page_size=50" -Method Get -TimeoutSec 20
    Assert-True ($Overview.total_repositories -eq $Status.counts.repositories) "Repository exact total이 상태 count와 다릅니다."
    Assert-True ($Overview.total_snapshots -eq $Status.counts.snapshots) "Snapshot exact total이 상태 count와 다릅니다."
    Assert-True ($Overview.total_locators -eq $Status.counts.locators) "Locator exact total이 상태 count와 다릅니다."


    Write-Step "Snapshot Explorer SPA 경로 확인"
    $SnapshotPage = Invoke-WebRequest -Uri "$AdminBaseUrl/snapshots" -Method Get -UseBasicParsing -TimeoutSec 10
    Assert-True ($SnapshotPage.StatusCode -eq 200) "/snapshots 페이지가 200을 반환하지 않았습니다."


    if ($Overview.snapshots.Count -gt 0) {
        $SnapshotId = [string]$Overview.snapshots[0].snapshot_id
        Write-Step "Snapshot 상세·Resolve 확인: $SnapshotId"
        $Detail = Invoke-RestMethod -Uri "$AdminBaseUrl/admin-api/snapshots/$SnapshotId" -Method Get -TimeoutSec 20
        Assert-True ($Detail.snapshot.snapshot_id -eq $SnapshotId) "Snapshot 상세 ID가 다릅니다."
        Assert-True ($null -ne $Detail.access_plan) "AccessPlan이 없습니다."
        Assert-True ($Detail.access_plan.available -eq $true) "Snapshot AccessPlan이 available이 아닙니다."
        Assert-True ($Detail.locator.access_mode -eq "backend-proxy") "Locator access_mode가 backend-proxy가 아닙니다."


        Write-Step "GitHub Tree 조회"
        $Tree = Invoke-RestMethod -Uri "$AdminBaseUrl/admin-api/snapshots/$SnapshotId/tree" -Method Get -TimeoutSec 60
        Assert-True ($Tree.snapshot_id -eq $SnapshotId) "Tree의 Snapshot ID가 다릅니다."
        Assert-True ($Tree.total -eq $Tree.entries.Count) "Tree total과 entries 수가 다릅니다."


        $FirstFile = $Tree.entries | Where-Object { $_.entry_type -eq "blob" } | Select-Object -First 1
        if ($null -ne $FirstFile) {
            $EncodedPath = [Uri]::EscapeDataString([string]$FirstFile.path)
            Write-Step "Snapshot UTF-8 파일 조회: $($FirstFile.path)"
            $File = Invoke-RestMethod -Uri "$AdminBaseUrl/admin-api/snapshots/$SnapshotId/file?path=$EncodedPath" -Method Get -TimeoutSec 60
            Assert-True ($File.snapshot_id -eq $SnapshotId) "File의 Snapshot ID가 다릅니다."
            Assert-True ($File.path -eq $FirstFile.path) "File path가 요청 경로와 다릅니다."
            Assert-True ($File.encoding -eq "utf-8") "File encoding이 utf-8이 아닙니다."
        }
    } else {
        Write-Warning "저장된 Snapshot이 없어 상세·Tree·File Live Smoke는 건너뜁니다."
    }


    Write-Step "현재 Public Snapshot Route 상태 확인"
    $OpenApi = Invoke-RestMethod -Uri "$ApiBaseUrl/openapi.json" -Method Get -TimeoutSec 20
    $PublicSnapshotRoutes = @(
        $OpenApi.paths.PSObject.Properties.Name |
            Where-Object { $_ -like "/v1/snapshot-control*" }
    )
    if ($Status.feature_enabled) {
        Assert-True ($PublicSnapshotRoutes.Count -gt 0) "Feature ON인데 Public Snapshot 경로가 없습니다."
    } else {
        Assert-True ($PublicSnapshotRoutes.Count -eq 0) "Feature OFF인데 Public Snapshot 경로가 노출됐습니다."
    }
}


Write-Host "`nSnapshot 관리자 페이지 검증이 완료됐습니다." -ForegroundColor Green
Write-Host "Project: $ProjectRoot"
Write-Host "Admin : $AdminBaseUrl/snapshots"
Write-Host "API   : $ApiBaseUrl"