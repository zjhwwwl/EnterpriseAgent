param()
. (Join-Path $PSScriptRoot 'common.ps1')
Assert-ProjectPython
Import-LocalInfra
# The running API does not need initialization or test-owner credentials.
$env:EA_MIGRATION_DATABASE_URL = ''
$env:EA_TEST_DATABASE_URL = ''
$env:EA_TEST_MIGRATION_DATABASE_URL = ''
Push-Location $projectPath
try { & $interpreterPath main.py } finally { Pop-Location }
