$ErrorActionPreference = 'Continue'
$mainRoot = 'D:\Project\Fintechathon-2026-demo'
$frontendRoot = 'D:\Project\Fintechathon-2026-frontend'
$pidFile = Join-Path $mainRoot '.tmp\frontend-dev.pid'

Write-Host 'Stopping frontend development branch...' -ForegroundColor Cyan
if (Test-Path $pidFile) {
  $pid = Get-Content $pidFile | Select-Object -First 1
  if ($pid -match '^\d+$') { taskkill.exe /PID $pid /T /F 2>$null | Out-Null }
  Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
}
$devProcesses = Get-CimInstance Win32_Process | Where-Object {
  $_.CommandLine -and $_.CommandLine.Contains($frontendRoot) -and $_.CommandLine.Contains('vite')
}
foreach ($p in $devProcesses) { taskkill.exe /PID $p.ProcessId /T /F 2>$null | Out-Null }

Write-Host 'Stopping main branch Compose services...' -ForegroundColor Cyan
Set-Location $mainRoot
docker compose down
Write-Host 'Both project services have been stopped.' -ForegroundColor Green
