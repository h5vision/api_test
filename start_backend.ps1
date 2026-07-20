$ErrorActionPreference = 'Stop'

$projectRoot = $PSScriptRoot
$parentPython = Join-Path $projectRoot '..\.venv\Scripts\python.exe'
$localPython = Join-Path $projectRoot '.venv\Scripts\python.exe'

if (Test-Path -LiteralPath $parentPython -PathType Leaf) {
    $pythonPath = (Resolve-Path -LiteralPath $parentPython).Path
} elseif (Test-Path -LiteralPath $localPython -PathType Leaf) {
    $pythonPath = (Resolve-Path -LiteralPath $localPython).Path
} else {
    throw 'Python virtual environment was not found in ..\.venv or .\.venv.'
}

Set-Location -LiteralPath $projectRoot
& $pythonPath (Join-Path $projectRoot 'main.py')

