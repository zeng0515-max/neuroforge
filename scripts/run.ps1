# ============================================================
# NeuroForge 开发环境一键启动脚本
# 用法: .\scripts\run.ps1 [--help] [--port PORT] [--no-reload]
# ============================================================
param(
    [string]$Port = "8000",
    [switch]$NoReload = $false,
    [switch]$Help = $false
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")

function Write-Banner {
    param([string]$Text)
    Write-Host ""
    Write-Host "============================================================" -ForegroundColor Cyan
    Write-Host "  $Text" -ForegroundColor Cyan
    Write-Host "============================================================" -ForegroundColor Cyan
    Write-Host ""
}

function Write-Step {
    param([string]$Step, [string]$Text)
    Write-Host "[$Step] $Text" -ForegroundColor Yellow
}

function Write-Success {
    param([string]$Text)
    Write-Host "  -> $Text" -ForegroundColor Green
}

function Write-Warn {
    param([string]$Text)
    Write-Host "  !! $Text" -ForegroundColor Yellow
}

if ($Help) {
    Write-Banner "NeuroForge 开发环境启动脚本"
    Write-Host "用法:"
    Write-Host "  .\scripts\run.ps1              启动开发服务器（默认端口 8000，启用热重载）"
    Write-Host "  .\scripts\run.ps1 --port 9000  指定端口启动"
    Write-Host "  .\scripts\run.ps1 --no-reload  禁用热重载"
    Write-Host "  .\scripts\run.ps1 --help       显示帮助"
    Write-Host ""
    Write-Host "前置条件:"
    Write-Host "  - Python 3.10+ 已安装"
    Write-Host "  - 首次运行会自动创建虚拟环境、安装依赖、初始化数据库"
    Write-Host "  - 需先配置 .env 文件（从 .env.example 复制）"
    exit 0
}

Write-Banner "NeuroForge 开发环境启动脚本"

# ---- 1. 检查 Python 环境 ----
Write-Step "1/5" "检查 Python 环境..."

$pythonCmd = $null
foreach ($cmd in @("python", "python3")) {
    try {
        $version = & $cmd --version 2>&1
        if ($LASTEXITCODE -eq 0) {
            $pythonCmd = $cmd
            Write-Success "找到 Python: $version ($cmd)"
            break
        }
    } catch {}
}

if (-not $pythonCmd) {
    Write-Host "错误: 未检测到 Python，请安装 Python 3.10+" -ForegroundColor Red
    exit 1
}

# ---- 2. 创建/激活虚拟环境 ----
Write-Step "2/5" "检查虚拟环境..."

$venvPath = Join-Path $ProjectRoot ".venv"
$venvActivate = Join-Path $venvPath "Scripts" "Activate.ps1"

if (-not (Test-Path $venvActivate)) {
    Write-Host "  虚拟环境不存在，正在创建..." -ForegroundColor Yellow
    Push-Location $ProjectRoot
    & $pythonCmd -m venv .venv
    Pop-Location
    if (-not (Test-Path $venvActivate)) {
        Write-Host "错误: 虚拟环境创建失败" -ForegroundColor Red
        exit 1
    }
    Write-Success "虚拟环境创建成功"
} else {
    Write-Success "虚拟环境已存在"
}

Write-Host "  激活虚拟环境..." -ForegroundColor Yellow
. $venvActivate
Write-Success "虚拟环境已激活"

# ---- 3. 安装依赖 ----
Write-Step "3/5" "检查依赖..."

$reqFile = Join-Path $ProjectRoot "requirements.txt"
if (-not (Test-Path $reqFile)) {
    Write-Host "错误: requirements.txt 不存在" -ForegroundColor Red
    exit 1
}

$installed = & pip show fastapi 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "  首次安装依赖（可能需要几分钟）..." -ForegroundColor Yellow
    & pip install -r $reqFile
    if ($LASTEXITCODE -ne 0) {
        Write-Host "错误: 依赖安装失败" -ForegroundColor Red
        exit 1
    }
    Write-Success "依赖安装完成"
} else {
    Write-Success "依赖已安装"
}

# ---- 4. 初始化数据库 ----
Write-Step "4/5" "检查数据库..."

$dbPath = Join-Path $ProjectRoot "data" "app.db"
$ragScript = Join-Path $ProjectRoot "scripts" "init_vector_db.py"

if (-not (Test-Path $dbPath)) {
    Write-Host "  数据库不存在，正在初始化..." -ForegroundColor Yellow
    $dataDir = Join-Path $ProjectRoot "data"
    if (-not (Test-Path $dataDir)) {
        New-Item -ItemType Directory -Path $dataDir -Force | Out-Null
    }
    Push-Location $ProjectRoot
    & $pythonCmd -c "from app.db.sqlite_client import db; print('数据库初始化成功:', db.engine.url)"
    Pop-Location
    Write-Success "数据库初始化完成"
} else {
    Write-Success "数据库已存在"
}

# 初始化 RAG 向量库
if ((Test-Path $ragScript) -and (Test-Path $dbPath)) {
    $chromaDir = Join-Path $ProjectRoot "data" "chroma"
    if (-not (Test-Path $chromaDir) -or -not (Get-ChildItem $chromaDir -ErrorAction SilentlyContinue)) {
        Write-Host "  RAG 向量库为空，正在初始化..." -ForegroundColor Yellow
        Push-Location $ProjectRoot
        & $pythonCmd $ragScript
        Pop-Location
        if ($LASTEXITCODE -eq 0) {
            Write-Success "RAG 向量库初始化完成"
        } else {
            Write-Warn "RAG 向量库初始化失败（不影响主流程）"
        }
    } else {
        Write-Success "RAG 向量库已存在"
    }
}

# ---- 5. 启动开发服务器 ----
Write-Step "5/5" "启动开发服务器..."

$reloadFlag = ""
if (-not $NoReload) {
    $reloadFlag = "--reload"
}

$envFile = Join-Path $ProjectRoot ".env"
if (-not (Test-Path $envFile)) {
    Write-Warn ".env 文件不存在，请从 .env.example 复制并配置"
    Write-Host "  copy .env.example .env" -ForegroundColor Gray
}

Write-Host ""
Write-Host "  开发服务器地址: http://localhost:$Port" -ForegroundColor Green
if (-not $NoReload) {
    Write-Host "  热重载: 已启用（代码修改后自动重启）" -ForegroundColor Green
}
Write-Host "  按 Ctrl+C 停止服务器" -ForegroundColor Gray
Write-Host ""

Push-Location $ProjectRoot
try {
    & uvicorn app.main:app --host 0.0.0.0 --port $Port $reloadFlag
} finally {
    Pop-Location
    Write-Host ""
    Write-Host "服务器已停止" -ForegroundColor Yellow
}