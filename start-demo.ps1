[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$backendPort = 8000
$frontendPort = 3000
$backendUrl = "http://127.0.0.1:$backendPort"
$frontendUrl = "http://127.0.0.1:$frontendPort"
$pythonPath = Join-Path $repoRoot '.venv\Scripts\python.exe'
$envPath = Join-Path $repoRoot '.env'
$frontendRoot = Join-Path $repoRoot 'frontend'
$runtimeRoot = Join-Path $repoRoot '.demo-runtime'

function Test-HttpEndpoint {
    param(
        [Parameter(Mandatory = $true)][string]$Url,
        [int]$TimeoutSeconds = 2
    )

    try {
        Invoke-WebRequest -Uri $Url -TimeoutSec $TimeoutSeconds -UseBasicParsing | Out-Null
        return $true
    }
    catch {
        return $false
    }
}

function Wait-HttpEndpoint {
    param(
        [Parameter(Mandatory = $true)][string]$Url,
        [int]$TimeoutSeconds = 30
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-HttpEndpoint -Url $Url -TimeoutSeconds 1) {
            return $true
        }
        Start-Sleep -Milliseconds 500
    }
    return $false
}

function Show-LogTail {
    param([string[]]$Paths)

    foreach ($path in $Paths) {
        if (Test-Path -LiteralPath $path) {
            Write-Host "`n--- $path ---" -ForegroundColor Yellow
            Get-Content -LiteralPath $path -Tail 40
        }
    }
}

function Stop-ProcessTree {
    param([Parameter(Mandatory = $true)][int]$RootProcessId)

    try {
        $children = Get-CimInstance Win32_Process -Filter "ParentProcessId = $RootProcessId" -ErrorAction SilentlyContinue
        foreach ($child in $children) {
            Stop-ProcessTree -RootProcessId $child.ProcessId
        }
    }
    catch {
        # Continue with the process started by this script if child lookup fails.
    }

    Stop-Process -Id $RootProcessId -Force -ErrorAction SilentlyContinue
}

function Assert-BackendDemoData {
    param([Parameter(Mandatory = $true)][string]$BaseUrl)

    $loginBody = @{ phone = '13800000001'; password = 'password123' } | ConvertTo-Json
    $login = Invoke-RestMethod `
        -Method Post `
        -Uri "$BaseUrl/api/v1/auth/login" `
        -ContentType 'application/json' `
        -Body $loginBody `
        -TimeoutSec 5

    $headers = @{ Authorization = "Bearer $($login.access_token)" }
    $accountsResponse = Invoke-WebRequest `
        -Method Get `
        -Uri "$BaseUrl/api/v1/accounts/$($login.user.user_id)/accounts" `
        -Headers $headers `
        -TimeoutSec 5 `
        -UseBasicParsing
    $accounts = $accountsResponse.Content | ConvertFrom-Json
    $accountCount = if ($null -eq $accounts) { 0 } elseif ($accounts -is [array]) { $accounts.Length } else { 1 }
    if ($accountCount -lt 1) {
        throw 'The backend is running, but the demo user has no account. Check DATABASE_URL and seed data.'
    }

    $subscriptionsResponse = Invoke-WebRequest `
        -Method Get `
        -Uri "$BaseUrl/api/v1/subscriptions" `
        -Headers $headers `
        -TimeoutSec 5 `
        -UseBasicParsing
    $subscriptions = $subscriptionsResponse.Content | ConvertFrom-Json
    $subscriptionCount = if ($null -eq $subscriptions) { 0 } elseif ($subscriptions -is [array]) { $subscriptions.Length } else { 1 }

    Write-Host "Backend preflight passed: $accountCount account(s), $subscriptionCount subscription(s)." -ForegroundColor Green
}

if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw "Python virtual environment not found: $pythonPath`nCreate .venv and install backend/requirements.txt first."
}
if (-not (Test-Path -LiteralPath $envPath)) {
    throw ".env not found: $envPath. Copy .env.example and configure it first."
}
if (-not (Test-Path -LiteralPath (Join-Path $frontendRoot 'node_modules'))) {
    throw 'Frontend dependencies not found. Run: cd frontend; npm install'
}

$npmCommand = Get-Command npm.cmd -ErrorAction SilentlyContinue
if ($null -eq $npmCommand) {
    throw 'npm.cmd was not found. Install Node.js and add it to PATH.'
}

New-Item -ItemType Directory -Path $runtimeRoot -Force | Out-Null
$backendOut = Join-Path $runtimeRoot 'backend.out.log'
$backendErr = Join-Path $runtimeRoot 'backend.err.log'
$frontendOut = Join-Path $runtimeRoot 'frontend.out.log'
$frontendErr = Join-Path $runtimeRoot 'frontend.err.log'
Remove-Item -LiteralPath $backendOut, $backendErr, $frontendOut, $frontendErr -Force -ErrorAction SilentlyContinue

$ownedBackend = $null
$ownedFrontend = $null

try {
    Set-Location -LiteralPath $repoRoot

    if (Test-HttpEndpoint -Url "$backendUrl/health") {
        Write-Host "Using the backend already running at $backendUrl" -ForegroundColor Cyan
    }
    else {
        Write-Host 'Starting backend...' -ForegroundColor Cyan
        $ownedBackend = Start-Process `
            -FilePath $pythonPath `
            -ArgumentList @(
                '-m', 'uvicorn', 'app.main:app',
                '--app-dir', (Join-Path $repoRoot 'backend'),
                '--env-file', $envPath,
                '--host', '127.0.0.1',
                '--port', "$backendPort"
            ) `
            -WorkingDirectory $repoRoot `
            -RedirectStandardOutput $backendOut `
            -RedirectStandardError $backendErr `
            -WindowStyle Hidden `
            -PassThru

        if (-not (Wait-HttpEndpoint -Url "$backendUrl/health" -TimeoutSeconds 30)) {
            Show-LogTail -Paths @($backendOut, $backendErr)
            throw "Backend did not start within 30 seconds. Logs: $runtimeRoot"
        }
    }

    try {
        Assert-BackendDemoData -BaseUrl $backendUrl
    }
    catch {
        Show-LogTail -Paths @($backendOut, $backendErr)
        throw "Backend demo-data preflight failed: $($_.Exception.Message)"
    }

    if (Test-HttpEndpoint -Url $frontendUrl) {
        Write-Host "Using the frontend already running at $frontendUrl" -ForegroundColor Cyan
    }
    else {
        Write-Host 'Starting frontend...' -ForegroundColor Cyan
        $ownedFrontend = Start-Process `
            -FilePath $npmCommand.Source `
            -ArgumentList @('run', 'dev', '--', '--host', '127.0.0.1', '--port', "$frontendPort") `
            -WorkingDirectory $frontendRoot `
            -RedirectStandardOutput $frontendOut `
            -RedirectStandardError $frontendErr `
            -WindowStyle Hidden `
            -PassThru

        if (-not (Wait-HttpEndpoint -Url $frontendUrl -TimeoutSeconds 30)) {
            Show-LogTail -Paths @($frontendOut, $frontendErr)
            throw "Frontend did not start within 30 seconds. Logs: $runtimeRoot"
        }
    }

    Write-Host "`nDemo is ready: $frontendUrl" -ForegroundColor Green
    Write-Host 'Demo account: 13800000001 / password123'
    Write-Host 'Keep this window open. Press Ctrl+C to stop services started by this script.'

    while ($true) {
        if ($null -ne $ownedBackend -and $ownedBackend.HasExited) {
            Show-LogTail -Paths @($backendOut, $backendErr)
            throw 'The backend process exited unexpectedly.'
        }
        if ($null -ne $ownedFrontend -and $ownedFrontend.HasExited) {
            Show-LogTail -Paths @($frontendOut, $frontendErr)
            throw 'The frontend process exited unexpectedly.'
        }
        Start-Sleep -Seconds 1
    }
}
finally {
    Write-Host "`nStopping services started by this script..." -ForegroundColor Cyan
    if ($null -ne $ownedFrontend -and -not $ownedFrontend.HasExited) {
        Stop-ProcessTree -RootProcessId $ownedFrontend.Id
    }
    if ($null -ne $ownedBackend -and -not $ownedBackend.HasExited) {
        Stop-ProcessTree -RootProcessId $ownedBackend.Id
    }
    Set-Location -LiteralPath $repoRoot
}
