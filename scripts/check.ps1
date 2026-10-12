param()

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
Push-Location $projectPath
try {
    & $interpreterPath -m ruff check .
    if ($LASTEXITCODE -ne 0) { throw 'Ruff lint failed.' }
    & $interpreterPath -m ruff format --check .
    if ($LASTEXITCODE -ne 0) { throw 'Ruff format check failed.' }
    & $interpreterPath -m pytest --junitxml=var/test-results/phase1.xml
    if ($LASTEXITCODE -ne 0) { throw 'Tests failed.' }
    & $interpreterPath -m pip check
    if ($LASTEXITCODE -ne 0) { throw 'Dependency consistency check failed.' }
} finally {
    Pop-Location
}
