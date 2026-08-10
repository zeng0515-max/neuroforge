# ============================================================
# NeuroForge 项目初始化脚本
# 用法: .\scripts\setup.ps1 [--help] [--skip-rag]
# ============================================================
param(
    [switch]$SkipRag = $false,
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
    Write-Banner "NeuroForge 项目初始化脚本"
    Write-Host "用法:"
    Write-Host "  .\scripts\setup.ps1          完整初始化（环境+依赖+目录+数据库+RAG）"
    Write-Host "  .\scripts\setup.ps1 --skip-rag 跳过 RAG 向量库初始化"
    Write-Host "  .\scripts\setup.ps1 --help    显示帮助"
    Write-Host ""
    Write-Host "初始化步骤:"
    Write-Host "  1. 环境检查（Python 版本、虚拟环境）"
    Write-Host "  2. 依赖安装（pip install -r requirements.txt）"
    Write-Host "  3. 目录结构创建（data/ 等）"
    Write-Host "  4. 数据库初始化（SQLite）"
    Write-Host "  5. RAG 向量库初始化（ChromaDB 种子数据）"
    exit 0
}

Write-Banner "NeuroForge 项目初始化"

# ---- 1. 环境检查 ----
Write-Step "1/5" "环境检查..."

$pythonCmd = $null
foreach ($cmd in @("python", "python3")) {
    try {
        $version = & $cmd --version 2>&1
        if ($LASTEXITCODE -eq 0) {
            $verStr = ($version -split " ")[1]
            $major = [int]($verStr -split "\.")[0]
            $minor = [int]($verStr -split "\.")[1]
            if ($major -ge 3 -and $minor -ge 10) {
                $pythonCmd = $cmd
                Write-Success "Python 版本满足要求: $version"
            } else {
                Write-Host "错误: Python 版本过低，需要 >= 3.10，当前: $version" -ForegroundColor Red
                exit 1
            }
            break
        }
    } catch {}
}

if (-not $pythonCmd) {
    Write-Host "错误: 未检测到 Python，请安装 Python 3.10+" -ForegroundColor Red
    exit 1
}

# 创建虚拟环境
$venvPath = Join-Path $ProjectRoot ".venv"
$venvActivate = Join-Path $venvPath "Scripts" "Activate.ps1"

if (-not (Test-Path $venvActivate)) {
    Write-Host "  创建虚拟环境..." -ForegroundColor Yellow
    Push-Location $ProjectRoot
    & $pythonCmd -m venv .venv
    Pop-Location
    if (-not (Test-Path $venvActivate)) {
        Write-Host "错误: 虚拟环境创建失败" -ForegroundColor Red
        exit 1
    }
    Write-Success "虚拟环境创建成功 (.venv)"
} else {
    Write-Success "虚拟环境已存在"
}

. $venvActivate

# ---- 2. 依赖安装 ----
Write-Step "2/5" "安装项目依赖..."

$reqFile = Join-Path $ProjectRoot "requirements.txt"
if (-not (Test-Path $reqFile)) {
    Write-Host "错误: requirements.txt 不存在" -ForegroundColor Red
    exit 1
}

Write-Host "  升级 pip..." -ForegroundColor Yellow
& pip install --upgrade pip
if ($LASTEXITCODE -ne 0) {
    Write-Warn "pip 升级失败，继续使用当前版本"
}

Write-Host "  安装依赖（首次可能需要几分钟）..." -ForegroundColor Yellow
& pip install -r $reqFile
if ($LASTEXITCODE -ne 0) {
    Write-Host "错误: 依赖安装失败" -ForegroundColor Red
    exit 1
}
Write-Success "依赖安装完成"

# ---- 3. 目录结构创建 ----
Write-Step "3/5" "创建目录结构..."

$dirs = @(
    "data",
    "data\chroma",
    "data\checkpoints"
)

foreach ($dir in $dirs) {
    $fullPath = Join-Path $ProjectRoot $dir
    if (-not (Test-Path $fullPath)) {
        New-Item -ItemType Directory -Path $fullPath -Force | Out-Null
        Write-Host "  创建目录: $dir" -ForegroundColor Gray
    }
}

# 保证 data/.gitkeep 存在
$gitkeep = Join-Path $ProjectRoot "data" ".gitkeep"
if (-not (Test-Path $gitkeep)) {
    New-Item -ItemType File -Path $gitkeep -Force | Out-Null
}

Write-Success "目录结构就绪"

# ---- 4. 数据库初始化 ----
Write-Step "4/5" "初始化数据库..."

Push-Location $ProjectRoot
try {
    & $pythonCmd -c "from app.db.sqlite_client import db; print('数据库表创建成功:', db.engine.url)"
    if ($LASTEXITCODE -eq 0) {
        Write-Success "数据库初始化完成"
    } else {
        Write-Host "错误: 数据库初始化失败" -ForegroundColor Red
        Pop-Location
        exit 1
    }
} catch {
    Write-Host "错误: 数据库初始化失败: $_" -ForegroundColor Red
    Pop-Location
    exit 1
}
Pop-Location

# ---- 5. RAG 向量库初始化 ----
if (-not $SkipRag) {
    Write-Step "5/5" "初始化 RAG 向量库..."

    $ragScript = Join-Path $ProjectRoot "scripts" "init_vector_db.py"
    if (Test-Path $ragScript) {
        Push-Location $ProjectRoot
        & $pythonCmd $ragScript
        if ($LASTEXITCODE -eq 0) {
            Write-Success "RAG 向量库初始化完成"
        } else {
            Write-Warn "RAG 向量库初始化失败（不影响主流程，可手动执行 scripts\init_vector_db.py）"
        }
        Pop-Location
    } else {
        Write-Warn "RAG 初始化脚本不存在，跳过"
    }
} else {
    Write-Step "5/5" "跳过 RAG 向量库初始化（--skip-rag）"
}

# ---- 完成 ----
Write-Banner "初始化完成"

Write-Host "下一步:" -ForegroundColor White
Write-Host "  1. 配置环境变量: copy .env.example .env" -ForegroundColor White
Write-Host "     然后编辑 .env，设置 LLM_API_KEY 等配置" -ForegroundColor Gray
Write-Host "  2. 启动开发服务器: .\scripts\run.ps1" -ForegroundColor White
Write-Host "  3. 运行测试: pytest tests/ -v" -ForegroundColor White
Write-Host ""