$ErrorActionPreference = 'Stop'
$mainRoot = 'D:\Project\Fintechathon-2026-demo'
$frontendRoot = 'D:\Project\Fintechathon-2026-frontend'
$frontendDir = Join-Path $frontendRoot 'frontend'
$pidFile = Join-Path $mainRoot '.tmp\frontend-dev.pid'

New-Item -ItemType Directory -Force (Split-Path $pidFile) | Out-Null
Set-Location $mainRoot
Write-Host 'Starting main branch Compose services...' -ForegroundColor Cyan
docker compose up -d

Write-Host 'Waiting for main backend on http://127.0.0.1:8000 ...' -ForegroundColor Cyan
$deadline = (Get-Date).AddMinutes(3)
while ((Get-Date) -lt $deadline) {
  try {
    $health = Invoke-RestMethod 'http://127.0.0.1:8000/health' -TimeoutSec 5
    if ($health.status -eq 'ok') { break }
  } catch { }
  Start-Sleep -Seconds 3
}

$existing = Get-CimInstance Win32_Process | Where-Object {
  $_.CommandLine -and $_.CommandLine.Contains($frontendRoot) -and $_.CommandLine.Contains('vite')
}
if ($existing) {
  Write-Host 'Frontend development branch is already running on port 3000.' -ForegroundColor Yellow
} else {
  Write-Host 'Starting frontend development branch (codex/frontend-20261020) on port 3000...' -ForegroundColor Cyan
  $proc = Start-Process -FilePath 'npm.cmd' -ArgumentList @('run','dev','--','--host','127.0.0.1','--port','3000') -WorkingDirectory $frontendDir -WindowStyle Normal -PassThru
  Set-Content -LiteralPath $pidFile -Value $proc.Id -Encoding ASCII
  $frontendReady = $false
  for ($i = 0; $i -lt 20; $i++) {
    try {
      Invoke-WebRequest 'http://127.0.0.1:3000/' -UseBasicParsing -TimeoutSec 2 | Out-Null
      $frontendReady = $true
      break
    } catch { Start-Sleep -Seconds 1 }
  }
  if (-not $frontendReady) { throw 'Frontend development branch did not start on port 3000.' }
}

Write-Host ''
Write-Host 'Main branch:     http://127.0.0.1:3101/' -ForegroundColor Green
Write-Host 'Frontend branch: http://127.0.0.1:3000/' -ForegroundColor Green
