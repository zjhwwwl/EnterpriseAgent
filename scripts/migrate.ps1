param([switch]$TestDatabase, [switch]$Check)
. (Join-Path $PSScriptRoot 'common.ps1')
Assert-ProjectPython
Import-LocalInfra
if ($TestDatabase) {
    $env:EA_DATABASE_URL = $env:EA_TEST_DATABASE_URL
    $env:EA_MIGRATION_DATABASE_URL = $env:EA_TEST_MIGRATION_DATABASE_URL
    if (-not $env:EA_MIGRATION_DATABASE_URL.EndsWith('/enterprise_agent_test')) {
        throw 'Refusing migration: test database name must be enterprise_agent_test.'
    }
}
Push-Location $projectPath
try {
    if ($Check) { & $interpreterPath -m alembic check }
    else { & $interpreterPath -m alembic upgrade head }
    if ($LASTEXITCODE -ne 0) { throw 'Database migration/check failed.' }
} finally { Pop-Location }
