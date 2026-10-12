param([switch]$SkipPostgres)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$projectPath = Split-Path -Parent $PSScriptRoot
$interpreterPath = Join-Path $projectPath '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $interpreterPath -PathType Leaf)) {
    throw 'Run scripts/bootstrap.ps1 after configuring the PyCharm interpreter.'
}
$cachePath = Join-Path $projectPath 'var\cache\pip'
$tempPath = Join-Path $projectPath 'var\tmp'
New-Item -ItemType Directory -Force -Path $cachePath, $tempPath | Out-Null
$env:PIP_CACHE_DIR = $cachePath
$env:TEMP = $tempPath
$env:TMP = $tempPath
$env:PYTHONUTF8 = '1'
if (-not $SkipPostgres) {
    . (Join-Path $PSScriptRoot 'common.ps1')
    Import-LocalInfra
}
Push-Location $projectPath
try {
    & $interpreterPath -m ruff check .
    if ($LASTEXITCODE -ne 0) { throw 'Ruff lint failed.' }
    & $interpreterPath -m ruff format --check .
    if ($LASTEXITCODE -ne 0) { throw 'Ruff format check failed.' }
    $pytestArguments = @('--junitxml=var/test-results/phase2.xml')
    if ($SkipPostgres) { $pytestArguments += @('-m', 'not postgres') }
    else { $pytestArguments += '--require-postgres' }
    & $interpreterPath -m pytest @pytestArguments
    if ($LASTEXITCODE -ne 0) { throw 'Tests failed.' }
    [xml]$report = Get-Content -LiteralPath 'var/test-results/phase2.xml' -Raw
    $suite = $report.testsuites.testsuite
    $result = [ordered]@{
        phase = 2; generated_at = [DateTime]::UtcNow.ToString('o')
        tests = [int]$suite.tests; failures = [int]$suite.failures
        errors = [int]$suite.errors; skipped = [int]$suite.skipped
        elapsed_seconds = [double]::Parse($suite.time, [Globalization.CultureInfo]::InvariantCulture)
        required_postgres = (-not $SkipPostgres)
    }
    $result | ConvertTo-Json | Set-Content -Encoding utf8 'var/test-results/phase2.json'
    & $interpreterPath -m pip check
    if ($LASTEXITCODE -ne 0) { throw 'Dependency consistency check failed.' }
} finally {
    Pop-Location
}
