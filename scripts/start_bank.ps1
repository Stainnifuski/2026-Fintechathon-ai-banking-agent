$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $taskRoot
if (!(Test-Path -LiteralPath '.env')) { throw 'Missing .env. Copy .env.example once and configure your own secrets.' }
# The pinned compact package ships in source. The rebuilt backend verifies and
# installs it; empty directories must exist before Compose's guarded bind mount.
New-Item -ItemType Directory -Force -Path '.deps/wealth_portfolio_data/snapshots', '.deps/upgrade_backups' | Out-Null
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
& docker @taskArgs build backend frontend
if ($LASTEXITCODE -ne 0) { throw 'Build failed; existing database volumes are preserved.' }
& docker @taskArgs up -d --wait --wait-timeout 300 backend
if ($LASTEXITCODE -ne 0) { throw 'Backend upgrade failed. Inspect logs and backups; do not delete volumes.' }
# Recreate only Nginx after backend readiness: discard stale DNS/health state
# from the old backend container without recreating the PostgreSQL dependency.
& docker @taskArgs up -d --no-deps --force-recreate --wait --wait-timeout 60 frontend
if ($LASTEXITCODE -ne 0) { throw 'Startup failed. Inspect docker compose logs; do not delete database volumes.' }
$taskHealth = Invoke-RestMethod 'http://localhost:3000/health' -TimeoutSec 15
if (!$taskHealth.portfolio_ready) { throw 'Portfolio material is not ready.' }
if ($taskHealth.source_revision -ne $taskRevision) { throw 'Running backend source does not match this checkout.' }
Write-Output "Ready: http://localhost:3000  API: http://localhost:3000/docs  source=$($taskHealth.source_revision)"
