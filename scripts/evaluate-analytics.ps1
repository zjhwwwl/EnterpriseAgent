param([string]$Output = 'var/evaluation/sql')
. (Join-Path $PSScriptRoot 'common.ps1')
Assert-ProjectPython
Import-LocalInfra

# Keep only the two disposable database credentials needed for real SQL references.
$credentialReader = $env:EA_TEST_ANALYTICS_DATABASE_URL
$credentialReference = $env:EA_TEST_MIGRATION_DATABASE_URL
$evaluationEnvironment = @{}
foreach ($name in @([Environment]::GetEnvironmentVariables('Process').Keys)) {
    if ($name -like 'EA_*') {
        $evaluationEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
        [Environment]::SetEnvironmentVariable($name, $null, 'Process')
    }
}
$env:EA_TEST_ANALYTICS_DATABASE_URL = $credentialReader
$env:EA_TEST_MIGRATION_DATABASE_URL = $credentialReference
$env:EA_APP_ENV = 'test'
$env:EA_LLM_ENABLED = 'false'
$env:EA_AGENT_ENABLED = 'false'

Push-Location $projectPath
try {
    & $interpreterPath -m enterprise_agent.analytics.evaluation --output $Output
    if ($LASTEXITCODE -ne 0) { throw 'SQL evaluation failed; check the safe report and test infrastructure.' }
} finally {
    Pop-Location
    foreach ($name in @([Environment]::GetEnvironmentVariables('Process').Keys)) {
        if ($name -like 'EA_*') { [Environment]::SetEnvironmentVariable($name, $null, 'Process') }
    }
    foreach ($name in $evaluationEnvironment.Keys) {
        [Environment]::SetEnvironmentVariable($name, $evaluationEnvironment[$name], 'Process')
    }
}
