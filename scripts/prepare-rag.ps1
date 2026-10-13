param()
. (Join-Path $PSScriptRoot 'common.ps1')
Assert-ProjectPython
$env:HF_HOME = Join-Path $projectPath 'var\cache\huggingface'
Push-Location $projectPath
try {
    & $interpreterPath scripts/prepare-rag-models.py
    if ($LASTEXITCODE -ne 0) { throw 'Pinned local model preparation failed.' }
} finally { Pop-Location }
