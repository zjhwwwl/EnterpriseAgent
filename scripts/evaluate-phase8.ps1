param(
    [ValidateSet('Boundaries', 'Prepare', 'Live')][string]$Mode = 'Boundaries',
    [switch]$AllowLive,
    [switch]$Prepared
)
. (Join-Path $PSScriptRoot 'common.ps1')
Assert-ProjectPython
$resultRoot = Join-Path $projectPath 'var/evaluation/phase8'
if ($Mode -eq 'Boundaries') {
    & $interpreterPath -m enterprise_agent.evaluations.live --mode boundaries --output (Join-Path $resultRoot 'boundaries')
    if ($LASTEXITCODE -ne 0) { throw 'Controlled boundary evaluation failed.' }
    return
}
if ($Mode -eq 'Live' -and -not $AllowLive) {
    throw 'Live evaluation uses the configured paid model. Pass -AllowLive explicitly.'
}
Import-LocalInfra
$env:EA_LANGSMITH_ENABLED = 'false'
if (-not $Prepared -or $Mode -eq 'Prepare') {
    & $interpreterPath -m enterprise_agent.evaluations.live --mode prepare --output $resultRoot
    if ($LASTEXITCODE -ne 0) { throw 'Dedicated test preparation failed.' }
}
if ($Mode -eq 'Prepare') { return }
$preparation = Get-Content -LiteralPath (Join-Path $resultRoot 'preparation.json') -Encoding utf8 -Raw | ConvertFrom-Json
if ($preparation.database -ne 'enterprise_agent_test' -or $preparation.collection_prefix -ne 'ea_phase8_eval') {
    throw 'An isolated real test preparation is required.'
}
function Assert-TestUrl([string]$value, [string]$role) {
    $uri = [Uri]$value
    if (-not $value.StartsWith('postgresql+psycopg://') -or $uri.Host -notin @('localhost', '127.0.0.1') -or
        $uri.AbsolutePath -ne '/enterprise_agent_test' -or [Uri]::UnescapeDataString($uri.UserInfo.Split(':', 2)[0]) -ne $role) {
        throw 'Evaluation refuses a non-test database, role or host.'
    }
}
Assert-TestUrl $env:EA_TEST_DATABASE_URL 'ea_test_app'
Assert-TestUrl $env:EA_TEST_ANALYTICS_DATABASE_URL 'ea_test_analytics_reader'
Assert-TestUrl $env:EA_TEST_MIGRATION_DATABASE_URL 'ea_local_admin'
if (Get-NetTCPConnection -LocalPort 8002 -State Listen -ErrorAction SilentlyContinue) {
    throw 'Evaluation refuses to replace an existing listener on port 8002.'
}
$outputPath = Join-Path $resultRoot ('live/' + [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfffffffZ'))
New-Item -ItemType Directory -Force -Path $outputPath | Out-Null
$env:EA_DATABASE_URL = $env:EA_TEST_DATABASE_URL
$env:EA_ANALYTICS_DATABASE_URL = $env:EA_TEST_ANALYTICS_DATABASE_URL
$env:EA_HOST = '127.0.0.1'
$env:EA_PORT = '8002'
$env:EA_APP_ENV = 'test'
$env:EA_DATA_DIR = Join-Path $projectPath 'var/evaluation-test'
$env:EA_RAG_ENABLED = 'true'
$env:EA_RAG_COLLECTION_PREFIX = 'ea_phase8_eval'
$env:EA_ANALYTICS_ENABLED = 'true'
$env:EA_LLM_ENABLED = 'true'
$env:EA_OBSERVABILITY_ENABLED = 'true'
$env:EA_REDIS_URL = 'redis://127.0.0.1:56379/2'
$env:EA_LOGIN_MAX_ATTEMPTS = '100'
$env:EA_JWT_SECRET = [Guid]::NewGuid().ToString('N') + [Guid]::NewGuid().ToString('N')
[IO.File]::WriteAllText((Join-Path $outputPath 'private-jwt-secret'), $env:EA_JWT_SECRET)
# The API process never receives owner or reference credentials.
$env:EA_MIGRATION_DATABASE_URL = ''
$env:EA_TEST_DATABASE_URL = ''
$env:EA_TEST_MIGRATION_DATABASE_URL = ''
$env:EA_TEST_ANALYTICS_DATABASE_URL = ''
$server = $null
try {
    $server = Start-Process -FilePath $interpreterPath -ArgumentList 'main.py' -WorkingDirectory $projectPath -WindowStyle Hidden -RedirectStandardOutput (Join-Path $outputPath 'api.stdout.jsonl') -RedirectStandardError (Join-Path $outputPath 'api.stderr.log') -PassThru
    $ready = $false
    $watch = [Diagnostics.Stopwatch]::StartNew()
    while ($watch.Elapsed.TotalSeconds -lt 30) {
        try {
            $response = Invoke-RestMethod 'http://127.0.0.1:8002/health/ready' -TimeoutSec 3
            if ($response.status -eq 'ok') { $ready = $true; break }
        } catch { }
        Start-Sleep -Milliseconds 500
    }
    if (-not $ready) { throw 'Isolated evaluation API did not become ready.' }
    # Only the evaluator receives the separately guarded read-only reference URL.
    Import-LocalInfra
    $env:EA_LANGSMITH_ENABLED = 'false'
    & $interpreterPath -m enterprise_agent.evaluations.live --mode live --allow-live --base-url 'http://127.0.0.1:8002' --output $outputPath
    if ($LASTEXITCODE -ne 0) { throw 'Evaluation could not complete; preserved partial evidence in the output directory.' }
    Write-Output ('Evaluation report saved: ' + $outputPath)
    Write-Output 'Completion records all fail/manual_review outcomes; it is not a model quality PASS gate.'
} finally {
    if ($null -ne $server) {
        $info = Get-CimInstance Win32_Process -Filter ('ProcessId = ' + $server.Id)
        if ($null -ne $info) {
            if (-not [string]::Equals($info.ExecutablePath, $interpreterPath, [StringComparison]::OrdinalIgnoreCase) -or
                $info.CommandLine -notmatch '(?:^|\s)main\.py(?:\s|$)') {
                throw 'Recorded server identity changed; no unknown process will be stopped.'
            }
            & (Join-Path $env:SystemRoot 'System32/taskkill.exe') /PID $server.Id /T /F | Out-Null
            if ($LASTEXITCODE -ne 0) { throw 'Owned evaluation API could not be stopped.' }
        }
    }
}
