# Project-local caches and credentials; this script never prints secrets.
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$projectPath = Split-Path -Parent $PSScriptRoot
$interpreterPath = Join-Path $projectPath '.venv\Scripts\python.exe'
$env:PYTHONUTF8 = '1'
$env:PIP_CACHE_DIR = Join-Path $projectPath 'var\cache\pip'
$env:TEMP = Join-Path $projectPath 'var\tmp'
$env:TMP = $env:TEMP
New-Item -ItemType Directory -Force -Path $env:PIP_CACHE_DIR, $env:TEMP | Out-Null

function Import-LocalInfra {
    $credentialsPath = Join-Path $projectPath 'var\infra-secrets.env'
    if (-not (Test-Path -LiteralPath $credentialsPath)) {
        throw 'Run scripts/init-infra.ps1 to create local infrastructure credentials.'
    }
    foreach ($line in [IO.File]::ReadAllLines($credentialsPath)) {
        if ($line -match '^(EA_[A-Z_]+)=(.*)$') {
            [Environment]::SetEnvironmentVariable($Matches[1], $Matches[2], 'Process')
        }
    }
}

function Get-ProjectDocker {
    $dockerPath = 'D:\tools\DockerDesktop\resources\bin\docker.exe'
    if (-not (Test-Path -LiteralPath $dockerPath)) {
        $command = Get-Command docker -ErrorAction SilentlyContinue
        if ($null -eq $command) { throw 'Docker Desktop is required; see docs/deployment.md.' }
        $dockerPath = $command.Source
    }
    # Docker's credential helper must be reachable by the CLI.
    $env:PATH = (Split-Path -Parent $dockerPath) + ';' + $env:PATH
    return $dockerPath
}

function Assert-ProjectPython {
    if (-not (Test-Path -LiteralPath $interpreterPath)) {
        throw 'Configure a Python 3.13 project virtual environment and run scripts/bootstrap.ps1.'
    }
}
