# ============================================================
# NeuroForge 生产部署脚本
# 用法: .\scripts\deploy.ps1 [--help] [--build-only] [--port PORT]
# ============================================================
param(
    [string]$Port = "8001",
    [switch]$BuildOnly = $false,
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
    Write-Banner "NeuroForge 生产部署脚本"
    Write-Host "用法:"
    Write-Host "  .\scripts\deploy.ps1              完整部署（配置+迁移+构建+启动）"
    Write-Host "  .\scripts\deploy.ps1 --build-only 仅构建 Docker 镜像"
    Write-Host "  .\scripts\deploy.ps1 --port 9001  指定生产端口"
    Write-Host "  .\scripts\deploy.ps1 --help       显示帮助"
    Write-Host ""
    Write-Host "部署步骤:"
    Write-Host "  1. 环境变量配置（检查 .env 文件）"
    Write-Host "  2. 数据库迁移（确保表结构最新）"
    Write-Host "  3. 构建和启动（Docker 或直接 uvicorn）"
    exit 0
}

Write-Banner "NeuroForge 生产部署"

# ---- 1. 环境变量配置 ----
Write-Step "1/3" "检查环境变量配置..."

$envFile = Join-Path $ProjectRoot ".env"
$envExample = Join-Path $ProjectRoot ".env.example"

if (-not (Test-Path $envFile)) {
    if (Test-Path $envExample) {
        Write-Warn ".env 不存在，正在从 .env.example 复制..."
        Copy-Item $envExample $envFile
        Write-Warn "已创建 .env，请编辑后填入真实密钥再重新运行部署"
        Write-Host "  编辑文件: $envFile" -ForegroundColor Gray
        Write-Host "  关键配置项: LLM_API_KEY, LLM_BASE_URL, LLM_MODEL" -ForegroundColor Gray
        exit 1
    } else {
        Write-Host "错误: .env 和 .env.example 都不存在" -ForegroundColor Red
        exit 1
    }
}

# 检查关键配置
$envContent = Get-Content $envFile -ErrorAction SilentlyContinue
if (-not $envContent) {
    Write-Host "错误: .env 文件为空" -ForegroundColor Red
    exit 1
}

$llmApiKeyLine = $envContent | Where-Object { $_ -match '^LLM_API_KEY=' }
if (-not $llmApiKeyLine -or $llmApiKeyLine -match 'sk-your-key-here' -or $llmApiKeyLine -match '^LLM_API_KEY=$') {
    Write-Warn "LLM_API_KEY 未配置，请在 .env 中设置真实的 API 密钥"
    Write-Host "  当前值: $llmApiKeyLine" -ForegroundColor Gray
}

Write-Success "环境变量配置已就绪"

# 创建必要目录
$dataDir = Join-Path $ProjectRoot "data"
if (-not (Test-Path $dataDir)) {
    New-Item -ItemType Directory -Path $dataDir -Force | Out-Null
    Write-Host "  创建数据目录: data/" -ForegroundColor Gray
}

# ---- 2. 数据库迁移 ----
Write-Step "2/3" "数据库迁移..."

$pythonCmd = $null
foreach ($cmd in @("python", "python3")) {
    try {
        & $cmd --version 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) {
            $pythonCmd = $cmd
            break
        }
    } catch {}
}

if (-not $pythonCmd) {
    Write-Host "错误: 未检测到 Python" -ForegroundColor Red
    exit 1
}

# 激活虚拟环境（如果存在）
$venvActivate = Join-Path $ProjectRoot ".venv" "Scripts" "Activate.ps1"
if (Test-Path $venvActivate) {
    . $venvActivate
    Write-Success "虚拟环境已激活"
} else {
    Write-Warn "虚拟环境不存在，使用系统 Python"
}

Push-Location $ProjectRoot
try {
    & $pythonCmd -c "from app.db.sqlite_client import db; print('数据库迁移完成:', db.engine.url)"
    if ($LASTEXITCODE -eq 0) {
        Write-Success "数据库迁移完成"
    } else {
        Write-Host "错误: 数据库迁移失败" -ForegroundColor Red
        Pop-Location
        exit 1
    }
} catch {
    Write-Host "错误: 数据库迁移失败: $_" -ForegroundColor Red
    Pop-Location
    exit 1
}

# 初始化 RAG 向量库（如果为空）
$ragScript = Join-Path $ProjectRoot "scripts" "init_vector_db.py"
$chromaDir = Join-Path $ProjectRoot "data" "chroma"
if ((Test-Path $ragScript) -and (-not (Test-Path $chromaDir) -or -not (Get-ChildItem $chromaDir -ErrorAction SilentlyContinue))) {
    Write-Host "  初始化 RAG 向量库..." -ForegroundColor Yellow
    & $pythonCmd $ragScript
    if ($LASTEXITCODE -eq 0) {
        Write-Success "RAG 向量库初始化完成"
    } else {
        Write-Warn "RAG 向量库初始化失败"
    }
} else {
    Write-Success "RAG 向量库已存在，跳过"
}
Pop-Location

# ---- 3. 构建和启动 ----
Write-Step "3/3" "构建和启动..."

# 检查是否使用 Docker
$dockerfile = Join-Path $ProjectRoot "Dockerfile"
$dockerCompose = Join-Path $ProjectRoot "docker-compose.yml"
$useDocker = $false

try {
    $dockerVersion = & docker --version 2>&1
    if ($LASTEXITCODE -eq 0) {
        $useDocker = $true
        Write-Success "Docker 可用: $dockerVersion"
    }
} catch {
    Write-Warn "Docker 不可用，使用原生 uvicorn 启动"
}

if ($BuildOnly -and $useDocker) {
    Write-Host "  仅构建 Docker 镜像..." -ForegroundColor Yellow
    Push-Location $ProjectRoot
    & docker build -t neuroforge:latest .
    Pop-Location
    if ($LASTEXITCODE -eq 0) {
        Write-Success "Docker 镜像构建完成: neuroforge:latest"
    } else {
        Write-Host "错误: Docker 构建失败" -ForegroundColor Red
        exit 1
    }
    Write-Banner "构建完成"
    exit 0
}

if ($useDocker -and (Test-Path $dockerCompose)) {
    Write-Host "  使用 Docker Compose 启动生产服务..." -ForegroundColor Yellow
    Push-Location $ProjectRoot

    # 检查是否已有运行中的容器
    $running = & docker ps --filter "name=neuroforge" --format "{{.Names}}" 2>&1
    if ($running -match "neuroforge") {
        Write-Warn "检测到运行中的容器，正在停止..."
        & docker compose down
    }

    & docker compose up -d --build
    if ($LASTEXITCODE -eq 0) {
        Write-Success "Docker 服务已启动"
        Write-Host ""
        Write-Host "  容器状态:" -ForegroundColor White
        & docker compose ps
    } else {
        Write-Host "错误: Docker 启动失败" -ForegroundColor Red
        Pop-Location
        exit 1
    }
    Pop-Location
} else {
    # 原生 uvicorn 启动
    Write-Host "  使用 uvicorn 启动生产服务器..." -ForegroundColor Yellow
    Write-Host "  端口: $Port" -ForegroundColor Gray

    $env:APP_ENV = "prod"
    $env:APP_PORT = $Port

    Push-Location $ProjectRoot
    try {
        & uvicorn app.main:app --host 0.0.0.0 --port $Port
    } finally {
        Pop-Location
    }
}

Write-Banner "部署完成"

if ($useDocker) {
    Write-Host "  访问地址: http://localhost:$Port" -ForegroundColor Green
    Write-Host "  查看日志: docker compose logs -f" -ForegroundColor Gray
    Write-Host "  停止服务: docker compose down" -ForegroundColor Gray
} else {
    Write-Host "  访问地址: http://localhost:$Port" -ForegroundColor Green
    Write-Host "  按 Ctrl+C 停止服务" -ForegroundColor Gray
}
Write-Host ""