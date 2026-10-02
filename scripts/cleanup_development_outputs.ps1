param([switch]$Apply)
# Only named reproducible outputs; preserve secrets, runtime ledgers and unknown backups.
$ErrorActionPreference = 'Stop'
$taskRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$taskNames = @(
 '.deps/form-input-cleanup-20260927/form-input.png',
 '.deps/local-update-20260927/phone-entry.png',
 '.deps/startup-baseline-20261001/wealth-page.png',
 '.deps/bank-readiness-20260928/browser.png',
 '.deps/wealth-v12-validation/migration-copy.db',
 '.deps/wealth-supplement/directory-raw.json',
 '.deps/wealth-supplement/cash.html', '.deps/wealth-supplement/daily.html', '.deps/wealth-supplement/periodic.html',
 '.deps/wealth-supplement/periodic28.pdf', '.deps/wealth-supplement/source-3.pdf',
 '.deps/wealth-supplement/closed-page3.png',
 '.deps/wealth-supplement/preload-summary.json', '.deps/wealth-supplement/preload-bounded-summary.json',
 '.deps/wealth-supplement/preload-valid-summary.json', '.deps/wealth-supplement/preload-rules-summary.json',
 '.deps/wealth-supplement/compose-summary.json', '.deps/wealth-supplement/make_rules.py',
 '.deps/wealth-supplement/check_directory.py', '.deps/wealth-supplement/select_pool.py',
 '.deps/wealth-supplement/list_pdf.py', '.deps/wealth-supplement/more_sources.py',
 '.deps/wealth-supplement/official_probe.py', '.deps/wealth-supplement/pdf_sources.py',
 '.deps/wealth-supplement/periodic_rules.py', '.deps/wealth-supplement/money_paging_probe.py',
 '.deps/wealth-supplement/repair_fee_cache.py'
 '.deps/wealth-market/latest-probe.json', '.deps/wealth-market/reprobe.json', '.deps/wealth-market/lpr-reprobe.json'
)
foreach ($taskCodeFolder in @('backend','tests','scripts')) {
 Get-ChildItem -LiteralPath (Join-Path $taskRoot $taskCodeFolder) -Directory -Recurse -Filter '__pycache__' |
 ForEach-Object {
  Get-ChildItem -LiteralPath $_.FullName -File -Filter '*.pyc' | ForEach-Object {
   $taskNames += [IO.Path]::GetRelativePath($taskRoot,$_.FullName)
  }
 }
}
$taskPytestCache = Join-Path $taskRoot '.pytest_cache'
if (Test-Path -LiteralPath $taskPytestCache) {
 Get-ChildItem -LiteralPath $taskPytestCache -Recurse -File | ForEach-Object {
  $taskNames += [IO.Path]::GetRelativePath($taskRoot,$_.FullName)
 }
}
foreach ($taskFolder in @('.deps/wealth-validation','.deps/wealth-v12-validation')) {
 $taskFolderPath = Join-Path $taskRoot $taskFolder
 if (Test-Path -LiteralPath $taskFolderPath) {
  Get-ChildItem -LiteralPath $taskFolderPath -File | Where-Object {
   $_.Extension -in @('.png','.jpg') -or $_.Name -like 'model-chain*.json'
  } | ForEach-Object { $taskNames += [IO.Path]::GetRelativePath($taskRoot,$_.FullName) }
 }
}
$taskSupplement = Join-Path $taskRoot '.deps/wealth-supplement'
if (Test-Path -LiteralPath $taskSupplement) {
 Get-ChildItem -LiteralPath $taskSupplement -File -Filter '*.txt' | ForEach-Object {
  $taskNames += [IO.Path]::GetRelativePath($taskRoot,$_.FullName)
 }
}
$taskTargets = @()
foreach ($taskName in ($taskNames | Sort-Object -Unique)) {
 $taskPath = [IO.Path]::GetFullPath((Join-Path $taskRoot $taskName))
 if (-not $taskPath.StartsWith($taskRoot+[IO.Path]::DirectorySeparatorChar,[StringComparison]::OrdinalIgnoreCase)) { throw 'Target outside workspace' }
 if (Test-Path -LiteralPath $taskPath) {
  $taskItem = Get-Item -LiteralPath $taskPath
  if ($taskItem.PSIsContainer -or ($taskItem.Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw 'Only regular output files allowed' }
  $taskTargets += $taskItem
 }
}
$taskTargets | Select-Object FullName,Length
if ($Apply) { foreach ($taskTarget in $taskTargets) { Remove-Item -LiteralPath $taskTarget.FullName } }
@{Applied=[bool]$Apply;Count=$taskTargets.Count;Bytes=($taskTargets | Measure-Object Length -Sum).Sum} | ConvertTo-Json -Compress | Write-Output
