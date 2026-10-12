param()

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$projectPath = Split-Path -Parent $PSScriptRoot
$interpreterPath = Join-Path $projectPath '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $interpreterPath -PathType Leaf)) {
    throw 'Configure a Python 3.13 project virtual environment in PyCharm first.'
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
    & $interpreterPath -c 'import sys; sys.exit(0 if sys.version_info[:2] == (3, 13) else 1)'
    if ($LASTEXITCODE -ne 0) { throw 'The project interpreter must be Python 3.13.' }
    & $interpreterPath -m pip install --require-hashes -r requirements/dev.lock
    if ($LASTEXITCODE -ne 0) { throw 'Locked dependency installation failed.' }
    & $interpreterPath -m pip install --no-deps --no-build-isolation -e .
    if ($LASTEXITCODE -ne 0) { throw 'Editable project installation failed.' }
    & $interpreterPath -m pip check
    if ($LASTEXITCODE -ne 0) { throw 'Dependency consistency check failed.' }
    $environmentFile = Join-Path $projectPath '.env'
    if (-not (Test-Path -LiteralPath $environmentFile)) {
        Copy-Item -LiteralPath (Join-Path $projectPath '.env.example') -Destination $environmentFile
    }
    Write-Output 'EnterpriseAgent Python environment is ready.'
} finally {
    Pop-Location
}
