param(
    [ValidateSet('Prepare', 'Up', 'Check', 'Test', 'Stop')][string]$Mode = 'Up'
)
. (Join-Path $PSScriptRoot 'common.ps1')
$dockerPath = Get-ProjectDocker
$composePath = Join-Path $projectPath 'deploy/compose.full.yaml'
$stackPath = Join-Path $projectPath 'var/compose'
$secretPath = Join-Path $stackPath 'secrets'
$publicConfigPath = Join-Path $stackPath 'compose.env'
$proofPath = Join-Path $projectPath 'var/test-results/phase9'
New-Item -ItemType Directory -Force -Path $proofPath | Out-Null

function Initialize-StackFiles {
    foreach ($name in @('postgres', 'redis', 'chroma', 'app', 'test-output', 'secrets')) {
        New-Item -ItemType Directory -Force -Path (Join-Path $stackPath $name) | Out-Null
    }
    if (-not (Test-Path -LiteralPath $publicConfigPath)) {
        [IO.File]::WriteAllText($publicConfigPath, "EA_FULL_LLM_ENABLED=false`nEA_FULL_DOCS_ENABLED=false`n", [Text.UTF8Encoding]::new($false))
    }
    foreach ($name in @('postgres-superuser-password', 'postgres-app-password', 'postgres-test-password',
                       'postgres-analytics-password', 'postgres-test-analytics-password', 'jwt-secret')) {
        $file = Join-Path $secretPath $name
        if (-not (Test-Path -LiteralPath $file)) {
            $bytes = New-Object byte[] 32
            $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
            try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
            $value = [BitConverter]::ToString($bytes).Replace('-', '').ToLowerInvariant()
            [IO.File]::WriteAllText($file, $value, [Text.UTF8Encoding]::new($false))
        }
        if ([IO.File]::ReadAllText($file).Trim() -notmatch '^[0-9a-f]{64}$') {
            throw 'An existing generated stack credential is invalid; no credential was replaced.'
        }
    }
    # Match the visible demonstration login; infrastructure secrets stay random.
    $demoPasswordPath = Join-Path $secretPath 'demo-password'
    if (-not (Test-Path -LiteralPath $demoPasswordPath)) {
        [IO.File]::WriteAllText($demoPasswordPath, 'DemoOnly!2026', [Text.UTF8Encoding]::new($false))
    }
    $demoPassword = [IO.File]::ReadAllText($demoPasswordPath).Trim()
    if ($demoPassword.Length -lt 8 -or $demoPassword.Length -gt 256 -or $demoPassword -match '[\s\x00-\x1f\x7f]') {
        throw 'The existing demo login password is invalid; no credential was replaced.'
    }
    $modelKey = Join-Path $secretPath 'deepseek-api-key'
    if (-not (Test-Path -LiteralPath $modelKey)) {
        [IO.File]::WriteAllText($modelKey, '', [Text.UTF8Encoding]::new($false))
    }
    foreach ($modelAsset in @('embedding', 'reranker', 'manifest.json')) {
        if (-not (Test-Path -LiteralPath (Join-Path $projectPath ('var/models/' + $modelAsset)))) {
            throw 'Prepare pinned RAG models with scripts/prepare-rag.ps1 before starting the stack.'
        }
    }
    Write-Output 'Independent stack directories and credentials are ready; existing credentials and data were preserved.'
}

function Assert-OwnedStack {
    $ids = @(& $dockerPath compose --project-name enterpriseagent-full --env-file $publicConfigPath -f $composePath ps --all --quiet)
    if ($LASTEXITCODE -ne 0) { throw 'Stack inventory failed.' }
    foreach ($containerId in $ids) {
        $labelsJson = & $dockerPath inspect --format '{{json .Config.Labels}}' $containerId
        if ($LASTEXITCODE -ne 0) { throw 'Container inspection failed; no container will be stopped.' }
        $labels = $labelsJson | ConvertFrom-Json
        if ($labels.'com.docker.compose.project' -ne 'enterpriseagent-full') {
            throw 'Container ownership check failed; no container will be stopped.'
        }
    }
}

function Check-Stack {
    $api = Invoke-RestMethod -Uri 'http://127.0.0.1:18000/health/ready' -TimeoutSec 20
    $proxy = Invoke-RestMethod -Uri 'http://127.0.0.1:15173/health/ready' -TimeoutSec 20
    $web = Invoke-WebRequest -Uri 'http://127.0.0.1:15173/' -UseBasicParsing -TimeoutSec 10
    if ($api.status -ne 'ok' -or $proxy.status -ne 'ok' -or $api.version -ne '0.9.0' -or
        $proxy.version -ne $api.version -or $api.phase -ne 9 -or $proxy.phase -ne 9 -or $web.StatusCode -ne 200) {
        throw 'Stack readiness or version mismatch.'
    }
    if (@($api.checks.PSObject.Properties).Count -ne 6 -or
        @($api.checks.PSObject.Properties | Where-Object { $_.Value -ne 'ok' }).Count -ne 0 -or
        @($proxy.checks.PSObject.Properties | Where-Object { $_.Value -ne 'ok' }).Count -ne 0) {
        throw 'An enabled backend dependency is not ready.'
    }
    $result = [ordered]@{
        checked_at_utc = [DateTime]::UtcNow.ToString('o'); status = 'passed'
        api_origin = 'http://127.0.0.1:18000'; web_origin = 'http://127.0.0.1:15173'
        api = $api; proxy = $proxy; web_status = $web.StatusCode
        scope = 'Local full-stack readiness and static frontend; no chat model request or cloud deployment'
    }
    [IO.File]::WriteAllText((Join-Path $proofPath 'readiness.json'), ($result | ConvertTo-Json -Depth 6), [Text.UTF8Encoding]::new($false))
    Write-Output ($result | ConvertTo-Json -Depth 6)
}

Push-Location $projectPath
try {
    if ($Mode -eq 'Stop') {
        Assert-OwnedStack
        & $dockerPath compose --project-name enterpriseagent-full --env-file $publicConfigPath -f $composePath stop
        if ($LASTEXITCODE -ne 0) { throw 'Owned stack stop failed.' }
        return
    }
    Initialize-StackFiles
    & $dockerPath compose --project-name enterpriseagent-full --env-file $publicConfigPath -f $composePath config --quiet
    if ($LASTEXITCODE -ne 0) { throw 'Compose configuration failed validation.' }
    if ($Mode -eq 'Prepare') { return }
    if ($Mode -eq 'Up') {
        & $dockerPath compose --project-name enterpriseagent-full --env-file $publicConfigPath -f $composePath build api web
        if ($LASTEXITCODE -ne 0) { throw 'Application image build failed.' }
        & $dockerPath compose --project-name enterpriseagent-full --env-file $publicConfigPath -f $composePath up -d --wait --wait-timeout 240
        if ($LASTEXITCODE -ne 0) { throw 'Full stack failed readiness; data and logs were retained.' }
        Check-Stack
    } elseif ($Mode -eq 'Test') {
        & $dockerPath compose --project-name enterpriseagent-full --env-file $publicConfigPath -f $composePath --profile test build tests
        if ($LASTEXITCODE -ne 0) { throw 'Linux test image build failed.' }
        & $dockerPath compose --project-name enterpriseagent-full --env-file $publicConfigPath -f $composePath --profile test run --rm tests
        if ($LASTEXITCODE -ne 0) { throw 'Linux tests failed; evidence remains in var/compose/test-output.' }
    } else { Check-Stack }
} finally { Pop-Location }
