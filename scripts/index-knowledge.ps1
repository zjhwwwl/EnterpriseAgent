param()
. (Join-Path $PSScriptRoot 'common.ps1')
Assert-ProjectPython
Import-LocalInfra
$env:EA_RAG_ENABLED = 'true'
Push-Location $projectPath
try {
    & $interpreterPath -m enterprise_agent.knowledge_cli
    if ($LASTEXITCODE -ne 0) { throw 'Knowledge indexing did not complete.' }
} finally { Pop-Location }
