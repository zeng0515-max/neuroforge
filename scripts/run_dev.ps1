# PowerShell 开发环境启动脚本
# 用法: .\scripts\run_dev.ps1

$ErrorActionPreference = "Stop"

# 激活虚拟环境
$venvPath = Join-Path $PSScriptRoot ".." ".venv" "Scripts" "Activate.ps1"
if (Test-Path $venvPath) {
    Write-Host "Activating virtual environment..." -ForegroundColor Green
    . $venvPath
} else {
    Write-Host "Warning: .venv not found, using system Python." -ForegroundColor Yellow
}

# 启动 uvicorn
Write-Host "Starting uvicorn dev server..." -ForegroundColor Green
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload