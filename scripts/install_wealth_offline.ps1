param([string]$ProjectRoot, [string]$Package, [switch]$NonInteractive)
$ErrorActionPreference = 'Stop'
try {
    if (-not $ProjectRoot) {
        $candidate = Split-Path -Parent $PSScriptRoot
        if (Test-Path -LiteralPath (Join-Path $candidate 'scripts/wealth_portfolio_package.py')) {
            $ProjectRoot = $candidate
        } elseif (-not $NonInteractive) {
            Add-Type -AssemblyName System.Windows.Forms
            $picker = New-Object System.Windows.Forms.FolderBrowserDialog
            $picker.Description = '请选择已克隆的银行项目目录'
            if ($picker.ShowDialog() -ne 'OK') { throw '已取消，未安装资料。' }
            $ProjectRoot = $picker.SelectedPath
        } else { throw '请提供ProjectRoot。' }
    }
    if (-not $Package) {
        $name = 'wealth-compact-900-v1.zip'
        foreach ($folder in @((Join-Path $ProjectRoot 'data/wealth'), $PSScriptRoot, (Split-Path -Parent $PSScriptRoot), $ProjectRoot)) {
            $candidate = Join-Path $folder $name
            if (Test-Path -LiteralPath $candidate) { $Package = $candidate; break }
        }
        if (-not $Package -and -not $NonInteractive) {
            Add-Type -AssemblyName System.Windows.Forms
            $picker = New-Object System.Windows.Forms.OpenFileDialog
            $picker.Title = '选择固定离线资料包'
            $picker.Filter = 'ZIP (*.zip)|*.zip'
            if ($picker.ShowDialog() -ne 'OK') { throw '已取消，未安装资料。' }
            $Package = $picker.FileName
        }
    }
    if (-not $Package) { throw '未找到指定ZIP。' }
    $python = $null
    foreach ($relative in @('.venv/Scripts/python.exe', 'venv/Scripts/python.exe')) {
        $candidate = Join-Path $ProjectRoot $relative
        if (Test-Path -LiteralPath $candidate) { $python = $candidate; break }
    }
    if (-not $python) {
        foreach ($name in @('python', 'python3', 'py')) {
            $command = Get-Command $name -ErrorAction SilentlyContinue
            if ($command) { $python = $command.Source; break }
        }
    }
    if (-not $python) { throw '未找到Python，请先安装项目支持的Python和依赖。' }
    & $python (Join-Path $PSScriptRoot 'install_wealth_offline.py') --project-root $ProjectRoot --package $Package
    exit $LASTEXITCODE
} catch {
    Write-Host ('安装未完成：' + $_.Exception.Message) -ForegroundColor Red
    exit 1
}
