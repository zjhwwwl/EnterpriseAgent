param(
    [ValidateSet('Install', 'Run', 'Check')][string]$Mode = 'Run',
    [string]$ApiTarget = 'http://127.0.0.1:8000'
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$projectPath = Split-Path -Parent $PSScriptRoot
$frontendPath = Join-Path $projectPath 'frontend'
$env:npm_config_cache = Join-Path $projectPath 'var/cache/npm'
$env:TEMP = Join-Path $projectPath 'var/tmp'
$env:TMP = $env:TEMP
$env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $projectPath 'var/cache/playwright'
New-Item -ItemType Directory -Force -Path $env:TEMP, $env:npm_config_cache | Out-Null
$nodePath = (Get-Command node -ErrorAction Stop).Source
$npmPath = Join-Path (Split-Path -Parent $nodePath) 'npm.cmd'
if (-not (Test-Path -LiteralPath $npmPath)) { throw 'npm must match the configured Node installation.' }
$nodeVersion = & $nodePath --version
if ($nodeVersion -notmatch '^v24\.(\d+)\.' -or [int]$Matches[1] -lt 15) { throw 'This frontend requires Node 24.15+ LTS (24.x).' }
$target = [Uri]$ApiTarget
if ($target.Scheme -ne 'http' -or $target.Host -notin @('127.0.0.1', 'localhost', '::1') -or $target.AbsolutePath -ne '/' -or $target.UserInfo -or $target.Query -or $target.Fragment) {
    throw 'ApiTarget must be a plain HTTP loopback origin.'
}
$env:EA_WEB_API_TARGET = $ApiTarget
Push-Location $frontendPath
try {
    if ($Mode -eq 'Install') {
        & $npmPath ci --no-fund
        if ($LASTEXITCODE -ne 0) { throw 'npm ci failed.' }
    } elseif ($Mode -eq 'Run') {
        & $npmPath run dev
        if ($LASTEXITCODE -ne 0) { throw 'Frontend server exited with an error.' }
    } else {
        & $npmPath run format:check
        if ($LASTEXITCODE -ne 0) { throw 'Frontend format check failed.' }
        & $npmPath run test -- --reporter=default --reporter=json --outputFile=../var/test-results/phase9-frontend-unit.json
        if ($LASTEXITCODE -ne 0) { throw 'Frontend tests failed.' }
        & $npmPath run build
        if ($LASTEXITCODE -ne 0) { throw 'Frontend typecheck/build failed.' }
        & $npmPath audit --omit=dev
        if ($LASTEXITCODE -ne 0) { throw 'Production dependency audit failed; inspect the reported advisories.' }
    }
} finally { Pop-Location }
