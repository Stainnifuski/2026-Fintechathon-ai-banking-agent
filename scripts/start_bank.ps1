$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $taskRoot
if (!(Test-Path -LiteralPath '.env')) { throw 'Missing .env. Copy .env.example once and configure your own secrets.' }
if (!(Test-Path -LiteralPath '.deps/wealth_portfolio_data/snapshots/d59ecfcc2ecb09e6573278d5b5f38524407f2ac72ac0051ebf7e7b63f53ff3e1.index.json')) {
    throw 'Install the fixed offline package with install_wealth_offline.cmd first. No fixture fallback is used.'
}
$taskRevision = 'source-archive'
if ((Test-Path -LiteralPath '.git') -and (Get-Command git -ErrorAction SilentlyContinue)) {
    $taskRevision = git rev-parse --short HEAD
    if ($LASTEXITCODE -ne 0) { throw 'Cannot identify this checkout; refusing an ambiguous source version.' }
    if (git status --porcelain --untracked-files=no) { $taskRevision += '-dirty' }
}
$env:SOURCE_REVISION = $taskRevision
# A private local override may select an existing ledger. It must not contain
# account credentials or be committed. Compose env files are never printed.
$taskArgs = @('compose', '-f', 'docker-compose.yml')
if (Test-Path -LiteralPath '.deps/bank-runtime.override.yml') {
    $taskArgs += @('-f', '.deps/bank-runtime.override.yml')
}
& docker @taskArgs config --quiet
if ($LASTEXITCODE -ne 0) { throw 'Invalid Docker Compose configuration.' }
& docker @taskArgs up -d --build --wait --wait-timeout 300 backend frontend
if ($LASTEXITCODE -ne 0) { throw 'Startup failed. Inspect docker compose logs; do not delete database volumes.' }
$taskHealth = Invoke-RestMethod 'http://localhost:3000/health' -TimeoutSec 15
if (!$taskHealth.portfolio_ready) { throw 'Portfolio material is not ready.' }
if ($taskHealth.source_revision -ne $taskRevision) { throw 'Running backend source does not match this checkout.' }
Write-Output "Ready: http://localhost:3000  API: http://localhost:3000/docs  source=$($taskHealth.source_revision)"
