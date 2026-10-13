param(
    [string]$PostgresDataDir = 'D:\DockerData\enterpriseagent\postgres',
    [string]$RedisDataDir = 'D:\DockerData\enterpriseagent\redis',
    [string]$ChromaDataDir = 'D:\DockerData\enterpriseagent\chroma'
)
. (Join-Path $PSScriptRoot 'common.ps1')
$dockerPath = Get-ProjectDocker
$secretsPath = Join-Path $projectPath 'var\infra'
New-Item -ItemType Directory -Force -Path $secretsPath | Out-Null
$utf8 = New-Object Text.UTF8Encoding($false)
$credentials = @{}
foreach ($name in @('postgres-superuser-password', 'postgres-app-password', 'postgres-test-password', 'jwt-secret')) {
    $target = Join-Path $secretsPath $name
    if (-not (Test-Path -LiteralPath $target)) {
        $bytes = New-Object byte[] 32
        $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
        try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
        $value = [BitConverter]::ToString($bytes).Replace('-', '').ToLowerInvariant()
        [IO.File]::WriteAllText($target, $value, $utf8)
    }
    $credentials[$name] = [IO.File]::ReadAllText($target).Trim()
}
$credentialsPath = Join-Path $projectPath 'var\infra-secrets.env'
if (-not (Test-Path -LiteralPath $credentialsPath)) {
    $admin = $credentials['postgres-superuser-password']
    $appPassword = $credentials['postgres-app-password']
    $testPassword = $credentials['postgres-test-password']
    $lines = @(
        "EA_DATABASE_URL=postgresql+psycopg://ea_app:${appPassword}@127.0.0.1:55432/enterprise_agent"
        "EA_MIGRATION_DATABASE_URL=postgresql+psycopg://ea_local_admin:${admin}@127.0.0.1:55432/enterprise_agent"
        "EA_TEST_DATABASE_URL=postgresql+psycopg://ea_test_app:${testPassword}@127.0.0.1:55432/enterprise_agent_test"
        "EA_TEST_MIGRATION_DATABASE_URL=postgresql+psycopg://ea_local_admin:${admin}@127.0.0.1:55432/enterprise_agent_test"
        "EA_JWT_SECRET=$($credentials['jwt-secret'])"
        'EA_REDIS_URL=redis://127.0.0.1:56379/0'
        "EA_POSTGRES_DATA_DIR=$($PostgresDataDir.Replace('\', '/'))"
        "EA_REDIS_DATA_DIR=$($RedisDataDir.Replace('\', '/'))"
        "EA_CHROMA_DATA_DIR=$($ChromaDataDir.Replace('\', '/'))"
    )
    [IO.File]::WriteAllLines($credentialsPath, $lines, $utf8)
}
Import-LocalInfra
if (-not $env:EA_CHROMA_DATA_DIR) { $env:EA_CHROMA_DATA_DIR = $ChromaDataDir.Replace('\', '/') }
Push-Location $projectPath
try {
    & $dockerPath compose --env-file var/infra-secrets.env -f deploy/compose.yaml up -d --wait
    if ($LASTEXITCODE -ne 0) { throw 'Local infrastructure did not become healthy.' }
    Write-Output 'Local PostgreSQL and Redis are healthy. Credentials remain in ignored var/ files.'
} finally { Pop-Location }
