param([string]$OrderNo = '10086')
. (Join-Path $PSScriptRoot 'common.ps1')
Assert-ProjectPython
Import-LocalInfra
# Only the ordinary business connection is exported to the MCP child by its client.
# Neither process reads .env; this demo performs local login and read-only calls.
$env:EA_LLM_ENABLED = 'false'
$env:EA_AGENT_ENABLED = 'false'
$env:EA_RAG_ENABLED = 'true'
$env:EA_ANALYTICS_ENABLED = 'false'
foreach ($credentialName in @(
    'EA_DEEPSEEK_API_KEY', 'EA_MIGRATION_DATABASE_URL',
    'EA_TEST_DATABASE_URL', 'EA_TEST_MIGRATION_DATABASE_URL', 'EA_REDIS_URL',
    'EA_MCP_ACCESS_TOKEN', 'EA_ANALYTICS_DATABASE_URL', 'EA_TEST_ANALYTICS_DATABASE_URL'
)) {
    [Environment]::SetEnvironmentVariable($credentialName, $null, 'Process')
}
Push-Location $projectPath
try {
    & $interpreterPath -m enterprise_agent.mcp.client --demo-login --order-no $OrderNo
    if ($LASTEXITCODE -ne 0) { throw 'Local MCP demonstration failed; inspect the sanitized report.' }
} finally {
    Pop-Location
}
