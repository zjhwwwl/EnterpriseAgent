param([string]$AsOf = (Get-Date -Format 'yyyy-MM-dd'), [int]$Seed = 42, [int]$Orders = 500)
. (Join-Path $PSScriptRoot 'common.ps1')
Assert-ProjectPython
Import-LocalInfra
Push-Location $projectPath
try {
    & $interpreterPath -m enterprise_agent.seed --as-of $AsOf --seed $Seed --orders $Orders
    if ($LASTEXITCODE -ne 0) { throw 'Demo initialization failed or was refused.' }
} finally { Pop-Location }
