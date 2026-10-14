param()
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$projectPath = Split-Path -Parent $PSScriptRoot
$pidPath = Join-Path $projectPath 'var/runtime/frontend.pid'
if (-not (Test-Path -LiteralPath $pidPath)) {
    throw 'No recorded background frontend. Stop a foreground frontend.ps1 server with Ctrl+C.'
}
$recorded = [IO.File]::ReadAllText($pidPath).Trim()
if ($recorded -notmatch '^\d+$') { throw 'Invalid frontend process record.' }
$frontendProcessId = [int]$recorded
$processInfo = Get-CimInstance Win32_Process -Filter "ProcessId = $frontendProcessId"
if ($null -eq $processInfo) { Write-Output 'Recorded frontend has already exited.'; return }
$nodePath = (Get-Command node -ErrorAction Stop).Source
$entryPath = [IO.Path]::GetFullPath((Join-Path $projectPath 'frontend/node_modules/vite/bin/vite.js'))
if (-not [string]::Equals($processInfo.ExecutablePath, $nodePath, [StringComparison]::OrdinalIgnoreCase) -or
    $processInfo.CommandLine -notmatch ([Regex]::Escape($entryPath) + '(?:"|\s|$)') -or
    $processInfo.CommandLine -notmatch '(?:^|\s)--port\s+5173(?:\s|$)') {
    throw 'Process record does not identify this project frontend; no process was stopped.'
}
& (Join-Path $env:SystemRoot 'System32/taskkill.exe') /PID $frontendProcessId /T /F
if ($LASTEXITCODE -ne 0) { throw 'Recorded frontend could not be stopped.' }
Write-Output 'This project background frontend is stopped. Use scripts/frontend.ps1 -Mode Run to start it in the foreground.'
