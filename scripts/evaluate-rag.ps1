param()
. (Join-Path $PSScriptRoot 'common.ps1')
Assert-ProjectPython
Import-LocalInfra
$env:EA_RAG_ENABLED = 'true'
Push-Location $projectPath
try {
    & $interpreterPath -m enterprise_agent.evaluation --output var/evaluation/phase3
    if ($LASTEXITCODE -ne 0) { throw 'Retrieval evaluation failed; see the reported dependency or data error.' }
} finally { Pop-Location }
