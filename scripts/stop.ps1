param()
. (Join-Path $PSScriptRoot 'common.ps1')
$pidPath = Join-Path $projectPath 'var\runtime\api.pid'
if (-not (Test-Path -LiteralPath $pidPath)) {
    throw 'No recorded background API. A foreground run.ps1 server can be stopped with Ctrl+C.'
}
$recorded = [IO.File]::ReadAllText($pidPath).Trim()
if ($recorded -notmatch '^\d+$') { throw 'Invalid local API process record.' }
$apiProcessId = [int]$recorded
$processInfo = Get-CimInstance Win32_Process -Filter "ProcessId = $apiProcessId"
if ($null -eq $processInfo) {
    Write-Output 'Recorded API process has already exited.'
    return
}
if (-not [string]::Equals($processInfo.ExecutablePath, $interpreterPath, [StringComparison]::OrdinalIgnoreCase) -or
    $processInfo.CommandLine -notmatch '(?:^|\s)main\.py(?:\s|$)') {
    throw 'Process record does not identify this project API; no process was stopped.'
}
# Terminate only the verified project launcher and its own children.
$taskkillPath = Join-Path $env:SystemRoot 'System32\taskkill.exe'
& $taskkillPath /PID $apiProcessId /T /F
if ($LASTEXITCODE -ne 0) { throw 'Recorded API process could not be stopped.' }
Write-Output 'This project background API is stopped. Use scripts/run.ps1 to start it in the foreground.'
