[CmdletBinding()]
param(
    [string]$ConnectorCommandFile = (Join-Path $PSScriptRoot "cloudflared 커넥터 설치.txt"),
    [string]$ComposeEnvFile = (Join-Path $PSScriptRoot "compose.env"),
    [switch]$FullStack
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $ConnectorCommandFile -PathType Leaf)) {
    throw "Cloudflare connector command file not found: $ConnectorCommandFile"
}

if (-not (Test-Path -LiteralPath $ComposeEnvFile -PathType Leaf)) {
    $exampleEnv = Join-Path $PSScriptRoot "compose.env.example"
    if ($FullStack) {
        throw "Create compose.env and replace all example secrets before starting the full stack."
    }
    $ComposeEnvFile = $exampleEnv
}

$connectorCommand = Get-Content -Raw -LiteralPath $ConnectorCommandFile
$tokenMatch = [regex]::Match(
    $connectorCommand,
    '(?:--token(?:=|\s+))(?<token>[^\s"'']+)'
)

if (-not $tokenMatch.Success) {
    throw "A --token value was not found in the Cloudflare connector command file."
}

$previousToken = [Environment]::GetEnvironmentVariable(
    "CLOUDFLARE_TUNNEL_TOKEN",
    "Process"
)

try {
    [Environment]::SetEnvironmentVariable(
        "CLOUDFLARE_TUNNEL_TOKEN",
        $tokenMatch.Groups["token"].Value,
        "Process"
    )

    $composeArguments = @(
        "compose",
        "--env-file", $ComposeEnvFile,
        "up", "-d"
    )
    if (-not $FullStack) {
        $composeArguments += @("traefik", "cloudflared")
    }

    & docker @composeArguments
    if ($LASTEXITCODE -ne 0) {
        throw "docker compose failed with exit code $LASTEXITCODE."
    }
}
finally {
    [Environment]::SetEnvironmentVariable(
        "CLOUDFLARE_TUNNEL_TOKEN",
        $previousToken,
        "Process"
    )
}
