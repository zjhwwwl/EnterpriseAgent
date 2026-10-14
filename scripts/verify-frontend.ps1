param(
    [switch]$AllowLive,
    [switch]$Prepared,
    [switch]$ScreenshotsOnly,
    [ValidatePattern('^$|^[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$')][string]$ResumeTask = ''
)
. (Join-Path $PSScriptRoot 'common.ps1')
Assert-ProjectPython
if (-not $AllowLive -and -not $ScreenshotsOnly) { throw 'Browser workflow uses the configured model. Pass -AllowLive to explicitly opt in, or use -ScreenshotsOnly for existing read-only pages.' }
if ($ScreenshotsOnly -and $ResumeTask) { throw 'Read-only screenshots never resume a task.' }
$nodePath = (Get-Command node -ErrorAction Stop).Source
$npmPath = Join-Path (Split-Path -Parent $nodePath) 'npm.cmd'
$frontendPath = Join-Path $projectPath 'frontend'
$resultPath = Join-Path $projectPath 'var/test-results/phase7-browser'
if (-not (Test-Path -LiteralPath (Join-Path $frontendPath 'dist/index.html'))) { throw 'Build the frontend first: scripts/frontend.ps1 -Mode Check.' }
foreach ($localPort in @(8001, 5174)) {
    if (Get-NetTCPConnection -LocalPort $localPort -State Listen -ErrorAction SilentlyContinue) {
        throw ('Browser verification refuses to replace an existing listener on ' + $localPort)
    }
}
New-Item -ItemType Directory -Force -Path $resultPath | Out-Null
$oldArtifacts = Join-Path $resultPath 'artifacts'
if (Test-Path -LiteralPath $oldArtifacts) {
    $priorSummary = Get-ChildItem -LiteralPath $oldArtifacts -Recurse -File | Where-Object { $_.Name -in @('live-results.json', 'captured.json') } | Select-Object -First 1
    if ($null -ne $priorSummary) {
        $priorHash = (Get-FileHash -LiteralPath $priorSummary.FullName -Algorithm SHA256).Hash
        $trialsRoot = Join-Path $projectPath 'var/test-results/phase7-browser-trials'
        $alreadyArchived = @(Get-ChildItem -LiteralPath $trialsRoot -Recurse -File -ErrorAction SilentlyContinue | Where-Object { $_.Name -in @('live-results.json', 'captured.json') -and (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash -eq $priorHash }).Count -gt 0
        if (-not $alreadyArchived) {
            $archivePath = Join-Path $trialsRoot ([DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfffffffZ'))
            New-Item -ItemType Directory -Force -Path $archivePath | Out-Null
            Copy-Item -LiteralPath $oldArtifacts -Destination $archivePath -Recurse
            foreach ($logName in @('playwright.json', 'api.stdout.jsonl', 'api.stderr.log', 'preview.stdout.log', 'preview.stderr.log')) {
                $logSource = Join-Path $resultPath $logName
                if (Test-Path -LiteralPath $logSource) { Copy-Item -LiteralPath $logSource -Destination $archivePath }
            }
            Write-Output 'Preserved previous browser trial before running again.'
        }
    }
}
Import-LocalInfra
function Assert-TestDatabaseUrl([string]$value, [string]$expectedRole) {
    if (-not $value.StartsWith('postgresql+psycopg://')) { throw 'A dedicated psycopg test URL is required.' }
    $parsed = [Uri]$value
    $configuredRole = [Uri]::UnescapeDataString($parsed.UserInfo.Split(':', 2)[0])
    if ($parsed.Host -notin @('127.0.0.1', 'localhost') -or $parsed.AbsolutePath -ne '/enterprise_agent_test' -or $configuredRole -ne $expectedRole) {
        throw 'Browser server refuses a non-test database, account, or host.'
    }
}
Assert-TestDatabaseUrl $env:EA_TEST_DATABASE_URL 'ea_test_app'
Assert-TestDatabaseUrl $env:EA_TEST_ANALYTICS_DATABASE_URL 'ea_test_analytics_reader'
if (-not $Prepared -and -not $ScreenshotsOnly) {
    & $interpreterPath (Join-Path $PSScriptRoot 'prepare-frontend-demo.py')
    if ($LASTEXITCODE -ne 0) { throw 'Dedicated test data/index preparation failed.' }
}
$preparation = Get-Content -Raw -Encoding utf8 (Join-Path $projectPath 'var/test-results/phase7-browser-preparation.json') | ConvertFrom-Json
if ($preparation.database -ne 'enterprise_agent_test' -or $preparation.collection_prefix -ne 'ea_frontend_test') { throw 'A verified isolated test preparation is required.' }
$testBusinessUrl = $env:EA_TEST_DATABASE_URL
$testReaderUrl = $env:EA_TEST_ANALYTICS_DATABASE_URL
$env:EA_DATABASE_URL = $testBusinessUrl
$env:EA_ANALYTICS_DATABASE_URL = $testReaderUrl
$env:EA_APP_ENV = 'test'
$env:EA_HOST = '127.0.0.1'
$env:EA_PORT = '8001'
$env:EA_DATA_DIR = Join-Path $projectPath 'var/frontend-test'
$env:EA_RAG_ENABLED = 'true'
$env:EA_RAG_COLLECTION_PREFIX = 'ea_frontend_test'
$env:EA_ANALYTICS_ENABLED = 'true'
$env:EA_REDIS_URL = 'redis://127.0.0.1:56379/1'
$env:EA_LOGIN_MAX_ATTEMPTS = '100'
if ($ScreenshotsOnly) { $env:EA_LLM_ENABLED = 'false' }
$env:EA_JWT_SECRET = [Guid]::NewGuid().ToString('N') + [Guid]::NewGuid().ToString('N')
[IO.File]::WriteAllText((Join-Path $resultPath 'private-jwt-secret'), $env:EA_JWT_SECRET, [Text.UTF8Encoding]::new($false))
$env:EA_MIGRATION_DATABASE_URL = ''
$env:EA_TEST_DATABASE_URL = ''
$env:EA_TEST_MIGRATION_DATABASE_URL = ''
$env:EA_TEST_ANALYTICS_DATABASE_URL = ''
$apiLauncher = $null
$previewLauncher = $null
function Stop-VerifiedChild([int]$ownedPid, [string]$executable, [string]$argumentPattern) {
    $processInfo = Get-CimInstance Win32_Process -Filter "ProcessId = $ownedPid"
    if ($null -eq $processInfo) { return }
    if (-not [string]::Equals($processInfo.ExecutablePath, $executable, [StringComparison]::OrdinalIgnoreCase) -or $processInfo.CommandLine -notmatch $argumentPattern) {
        throw 'Recorded child identity changed; no unknown process will be stopped.'
    }
    & (Join-Path $env:SystemRoot 'System32/taskkill.exe') /PID $ownedPid /T /F | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Owned browser process could not be stopped.' }
}
function Wait-Ready([string]$url) {
    $watch = [Diagnostics.Stopwatch]::StartNew()
    while ($watch.Elapsed.TotalSeconds -lt 30) {
        try { $reply = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 3; if ($reply.StatusCode -eq 200) { return } } catch { }
        Start-Sleep -Milliseconds 500
    }
    throw ('Local browser service did not become ready: ' + $url)
}
try {
    $apiLauncher = Start-Process -FilePath $interpreterPath -ArgumentList 'main.py' -WorkingDirectory $projectPath -WindowStyle Hidden -RedirectStandardOutput (Join-Path $resultPath 'api.stdout.jsonl') -RedirectStandardError (Join-Path $resultPath 'api.stderr.log') -PassThru
    Wait-Ready 'http://127.0.0.1:8001/health/ready'
    $env:EA_WEB_API_TARGET = 'http://127.0.0.1:8001'
    # The static preview needs no backend credentials in its own environment.
    Get-ChildItem Env: | Where-Object { $_.Name -like 'EA_*' -and $_.Name -ne 'EA_WEB_API_TARGET' } | ForEach-Object { Remove-Item -LiteralPath ('Env:' + $_.Name) }
    $previewLauncher = Start-Process -FilePath $nodePath -ArgumentList @('node_modules/vite/bin/vite.js', 'preview', '--host', '127.0.0.1', '--port', '5174', '--strictPort') -WorkingDirectory $frontendPath -WindowStyle Hidden -RedirectStandardOutput (Join-Path $resultPath 'preview.stdout.log') -RedirectStandardError (Join-Path $resultPath 'preview.stderr.log') -PassThru
    Wait-Ready 'http://127.0.0.1:5174/'
    # All EA_* variables above were cleared before preview startup, including
    # any ambient test selector; only this invocation selects a browser suite.
    if ($ScreenshotsOnly) { $env:EA_FRONTEND_SCREENSHOTS_ONLY = 'true' }
    else { $env:EA_FRONTEND_ALLOW_LIVE = 'true' }
    $env:EA_FRONTEND_TEST_DATABASE = 'enterprise_agent_test'
    if ($ResumeTask) { $env:EA_FRONTEND_REUSE_TASK_ID = $ResumeTask }
    $env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $projectPath 'var/cache/playwright'
    $env:npm_config_cache = Join-Path $projectPath 'var/cache/npm'
    Push-Location $frontendPath
    try {
        & $npmPath run test:browser
        if ($LASTEXITCODE -ne 0) { throw 'Actual browser verification failed; inspect preserved artifacts.' }
    } finally { Pop-Location }
} finally {
    if ($null -ne $previewLauncher) { Stop-VerifiedChild $previewLauncher.Id $nodePath 'vite[/\\]bin[/\\]vite\.js.+preview' }
    if ($null -ne $apiLauncher) { Stop-VerifiedChild $apiLauncher.Id $interpreterPath '(?:^|\s)main\.py(?:\s|$)' }
}
